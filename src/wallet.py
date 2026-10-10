"""Read-only Aave V3 Ethereum account discovery, separate from scenario decisions.

ABI references: aave-dao/aave-v3-origin IPool, IPoolDataProvider,
IPriceOracleGetter, and ERC-20 metadata/balanceOf. No wallet persistence.
"""
import hashlib
import os
import re
import time
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from urllib.parse import urlsplit

import requests

from src.sources import (ADDRESS_BOOK_URL, DEFAULT_ETH_RPC_URL, SELECTORS,
                         SourceError, _address, _contract_code, _decode_address,
                         _quantity, _words, parse_address_book)

# Keccak selectors verified with public Ethereum web3_sha3 during Blocks 06A/B.
WALLET_SELECTORS = {
    **SELECTORS,
    'getUserAccountData(address)': '0xbf92857c',
    'getUserConfiguration(address)': '0x4417a583',
    'getUserEMode(address)': '0xeddf1b79',
    'getReservesList()': '0xd1946dbc',
    'getReservesCount()': '0x72218d04',
    'getReserveAddressById(uint16)': '0x52751797',
    'getUserReserveData(address,address)': '0x28dd2d01',
    'getReserveTokensAddresses(address)': '0xd2493b6c',
    'getAssetPrice(address)': '0xb3596f07',
    'BASE_CURRENCY()': '0xe19f4700',
    'BASE_CURRENCY_UNIT()': '0x8c89b64f',
    'getEModeCategoryCollateralConfig(uint8)': '0xb286f467',
    'getEModeCategoryCollateralBitmap(uint8)': '0xb0771dba',
    'decimals()': '0x313ce567',
    'symbol()': '0x95d89b41',
    'balanceOf(address)': '0x70a08231',
}
MAX_UINT256 = 2**256 - 1
MAX_RESERVES = 128
MAX_AGE_SECONDS = 3600
RPC_TIMEOUT_SECONDS = 12
MAX_HTTP_REQUESTS = 512
MAX_IMPORT_SECONDS = 120
MAX_TRANSIENT_RETRIES = 3


class WalletProviderError(SourceError):
    """Safe transport diagnosis, distinct from rejected account/ABI evidence."""

    def __init__(self, reason, diagnostic):
        self.diagnostic = diagnostic
        super().__init__(reason + '; no complete position imported.')


def _rpc_error_info(payload):
    """Classify remote text internally; never echo messages, URLs or credentials."""
    entries = payload if isinstance(payload, list) else [payload]
    codes, kinds = [], []
    for entry in entries:
        error = entry.get('error') if isinstance(entry, dict) else None
        if not isinstance(error, dict):
            continue
        code = error.get('code')
        if type(code) is int:
            codes.append(code)
        message = error.get('message', '')
        message = message.lower() if isinstance(message, str) else ''
        if 'archive' in message and any(word in message for word in ('token', 'access', 'require')):
            kinds.append('archive_access_required')
        elif any(word in message for word in ('missing trie node', 'historical state', 'state is not available', 'header not found')):
            kinds.append('historical_state_unavailable')
        elif 'batch' in message and any(word in message for word in
                                       ('unsupported', 'not support', 'disabled', 'limit', 'too many',
                                        'not allowed', 'more than', 'exceed', 'maximum')):
            kinds.append('batch_unsupported')
        elif 'rate limit' in message or 'too many requests' in message:
            kinds.append('rate_limited')
        elif 'revert' in message:
            kinds.append('contract_revert')
        else:
            kinds.append('provider_rpc_error')
    return sorted(set(codes)), next(iter(kinds), None)


def _explicit_batch_rejection(payload):
    """Only a whole-batch transport rejection permits sequential re-reading."""
    entries = payload if isinstance(payload, list) else [payload]
    return bool(entries) and all(isinstance(entry, dict) and 'result' not in entry
                                 and isinstance(entry.get('error'), dict)
                                 and _rpc_error_info(entry)[1] == 'batch_unsupported'
                                 for entry in entries)


class _BatchRejected(Exception):
    pass


def validate_wallet_address(value):
    """Require a nonzero 20-byte hex address; mixed-case checksum checked by RPC."""
    if not isinstance(value, str) or not re.fullmatch(r'0x[0-9a-fA-F]{40}', value):
        raise SourceError('Enter a 0x-prefixed, 40-hex-digit Ethereum address (no ENS or whitespace).')
    if int(value, 16) == 0:
        raise SourceError('The zero address is not an inspectable wallet.')
    return value.lower()


def _units(value, decimals):
    with localcontext() as context:
        context.prec = 120
        return format(Decimal(value) / Decimal(10)**decimals, 'f')


def _address_array(raw):
    if not isinstance(raw, str) or not re.fullmatch(r'0x(?:[0-9a-fA-F]{64})+', raw):
        raise SourceError('Malformed reserve array ABI')
    values = _words(raw, (len(raw) - 2) // 64)
    if len(values) < 2 or values[0] != 32 or not 1 <= values[1] <= MAX_RESERVES:
        raise SourceError('Invalid reserve array offset or count')
    if len(values) != values[1] + 2:
        raise SourceError('Incomplete reserve array')
    addresses = [_decode_address('0x' + format(value, '064x')) for value in values[2:]]
    if len(set(addresses)) != len(addresses):
        raise SourceError('Duplicate reserves in enumeration')
    return addresses


def _symbol(raw):
    if not isinstance(raw, str) or not re.fullmatch(r'0x(?:[0-9a-fA-F]{2})+', raw):
        raise SourceError('Invalid symbol ABI')
    data = bytes.fromhex(raw[2:])
    if len(data) < 96 or int.from_bytes(data[:32], 'big') != 32:
        raise SourceError('Unsupported symbol ABI: expected dynamic ERC-20 string')
    length = int.from_bytes(data[32:64], 'big')
    if not 1 <= length <= 256 or len(data) != 64 + ((length + 31) // 32) * 32:
        raise SourceError('Invalid symbol length or padding')
    if any(data[64 + length:]):
        raise SourceError('Invalid symbol padding')
    try:
        symbol = data[64:64 + length].decode('utf-8')
    except UnicodeDecodeError as exc:
        raise SourceError('Invalid symbol encoding') from exc
    if not symbol.isprintable() or not symbol.strip() or len(symbol) > 64:
        raise SourceError('Unsupported token symbol')
    return symbol


def _header(raw, expected=None):
    if not isinstance(raw, dict):
        raise SourceError('Missing wallet block header')
    number = _quantity(raw.get('number'))
    timestamp = _quantity(raw.get('timestamp'))
    block_hash = raw.get('hash')
    if (number <= 0 or timestamp <= 0 or (expected is not None and number != expected)
            or not isinstance(block_hash, str) or not re.fullmatch(r'0x[0-9a-fA-F]{64}', block_hash)):
        raise SourceError('Invalid wallet block header')
    return {'number': number, 'hash': block_hash.lower(), 'timestamp': timestamp}


class _RPC:
    """Bounded JSON-RPC batches; reject missing, duplicate or failed responses."""

    def __init__(self, client, endpoint, *, max_seconds=None,
                 max_requests=None, timeout_seconds=None, max_retries=None):
        try:
            if not isinstance(endpoint, str):
                raise ValueError('Invalid endpoint type')
            parsed = urlsplit(endpoint)
            parsed.port
            if parsed.scheme not in ('http', 'https') or not parsed.hostname:
                raise ValueError('Invalid endpoint')
        except (TypeError, ValueError) as exc:
            raise SourceError('ETH_RPC_URL must be a valid HTTP(S) endpoint') from exc
        self.client, self.endpoint, self.host = client, endpoint, parsed.hostname
        self.evidence = []
        self.next_id = 0
        self.sequential = False
        self.diagnostics = []
        self.http_requests = 0
        self.retries = 0
        self.started = time.monotonic()
        self.max_seconds = MAX_IMPORT_SECONDS if max_seconds is None else max_seconds
        self.max_requests = MAX_HTTP_REQUESTS if max_requests is None else max_requests
        self.timeout_seconds = RPC_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
        self.max_retries = MAX_TRANSIENT_RETRIES if max_retries is None else max_retries

    def _post(self, payload):
        batch = isinstance(payload, list)
        methods = sorted({item['method'] for item in payload}) if batch else [payload['method']]
        for attempt in range(2):
            remaining = self.max_seconds - (time.monotonic() - self.started)
            if remaining <= 0 or self.http_requests >= self.max_requests:
                raise WalletProviderError('Wallet RPC request/time budget exhausted', {'kind': 'budget_exhausted'})
            self.http_requests += 1
            status = None
            try:
                response = self.client.post(self.endpoint, json=payload,
                                            timeout=min(self.timeout_seconds, remaining))
                status = getattr(response, 'status_code', 200)
                try:
                    body = response.json()
                except ValueError:
                    body = None
                codes, kind = _rpc_error_info(body)
                if status == 429:
                    kind = 'rate_limited'
                diagnostic = {'http_status': status, 'rpc_codes': codes, 'kind': kind,
                              'methods': methods, 'batch': batch, 'attempt': attempt + 1}
                if status >= 400:
                    self.diagnostics.append(diagnostic)
                    # Never downgrade a per-call failure, access denial or rate limit.
                    if batch and status in (400, 413, 415, 422, 500) and _explicit_batch_rejection(body):
                        raise _BatchRejected()
                    if batch and status in (400, 413, 415, 422) and (
                            kind == 'batch_unsupported' or (kind is None and not isinstance(body, list))
                            or (isinstance(body, dict) and codes == [-32600])):
                        raise _BatchRejected()
                    if status in (429, 502, 503, 504) and attempt == 0 and self.retries < self.max_retries:
                        self.retries += 1
                        time.sleep(0.25)
                        continue
                    reason = {'archive_access_required': 'Provider requires archive access for this historical block',
                              'historical_state_unavailable': 'Provider cannot serve the requested historical state',
                              'rate_limited': 'Provider rate limit reached'}.get(kind, 'Provider HTTP access/request failure')
                    raise WalletProviderError(f'{reason} (HTTP {status}' +
                                              (f", RPC {','.join(map(str, codes))}" if codes else '') + ')', diagnostic)
                if batch and (_explicit_batch_rejection(body) or (
                        isinstance(body, dict) and body.get('jsonrpc') == '2.0'
                        and body.get('id') is None and codes == [-32600])):
                    self.diagnostics.append(diagnostic)
                    raise _BatchRejected()
                if kind:
                    self.diagnostics.append(diagnostic)
                    raise WalletProviderError('Provider RPC error (' + kind +
                                              (f", code {','.join(map(str, codes))}" if codes else '') + ')', diagnostic)
                if body is None:
                    raise WalletProviderError('Provider returned invalid JSON', {'kind': 'invalid_json', 'http_status': status})
                response.raise_for_status()
                return body
            except (requests.Timeout, requests.ConnectionError) as exc:
                diagnostic = {'kind': type(exc).__name__, 'methods': methods, 'batch': batch}
                self.diagnostics.append(diagnostic)
                if attempt == 0 and self.retries < self.max_retries:
                    self.retries += 1
                    time.sleep(0.25)
                    continue
                raise WalletProviderError(f'Wallet provider/network failure ({type(exc).__name__})', diagnostic) from exc
            except requests.RequestException as exc:
                raise WalletProviderError(f'Wallet provider HTTP failure ({type(exc).__name__})',
                                          {'kind': type(exc).__name__, 'http_status': status}) from exc
            except ValueError as exc:
                raise WalletProviderError('Provider returned invalid JSON', {'kind': 'invalid_json'}) from exc

    def many(self, jobs):
        results = []
        for start in range(0, len(jobs), 20):
            requests_batch = []
            for method, params in jobs[start:start + 20]:
                self.next_id += 1
                requests_batch.append({'jsonrpc': '2.0', 'id': self.next_id,
                                       'method': method, 'params': params})
            single = len(requests_batch) == 1
            if self.sequential or single:
                bodies = [self._post(request) for request in requests_batch]
            else:
                try:
                    bodies = self._post(requests_batch)
                except _BatchRejected:
                    self.sequential = True
                    bodies = [self._post(request) for request in requests_batch]
            if not isinstance(bodies, list) or len(bodies) != len(requests_batch):
                raise SourceError('Incomplete wallet RPC batch; no complete position imported.')
            by_id = {}
            for body in bodies:
                if (not isinstance(body, dict) or body.get('jsonrpc') != '2.0'
                        or type(body.get('id')) is not int or body['id'] in by_id
                        or 'error' in body or body.get('result') is None):
                    raise SourceError('Wallet RPC error or invalid response; no complete position imported.')
                by_id[body['id']] = body
            if set(by_id) != {request['id'] for request in requests_batch}:
                raise SourceError('Wallet RPC response IDs do not match requests')
            for request in requests_batch:
                result = by_id[request['id']]['result']
                self.evidence.append({**request, 'result': result})
                results.append(result)
        return results

    def read(self, method, params):
        return self.many([(method, params)])[0]

    def calls(self, jobs, tag):
        return self.many([('eth_call', [{'to': target, 'data': WALLET_SELECTORS[signature] + argument}, tag])
                          for target, signature, argument in jobs])


def wallet_quality_issues(observation, now=None):
    """Dated wallet evidence never gains freshness on a UI rerun."""
    now = now or datetime.now(timezone.utc)
    issues = []
    try:
        retrieved = datetime.fromisoformat(observation['retrieved_at'])
        if retrieved.tzinfo is None:
            raise ValueError('Missing timezone')
        for timestamp in (retrieved.timestamp(), observation['block']['timestamp']):
            age = now.timestamp() - timestamp
            if age < -60 or age > MAX_AGE_SECONDS:
                issues.append('Wallet observation is stale or has an unreliable timestamp.')
                break
        if observation['historical']:
            issues.append('Explicit historical wallet inspection; not live.')
    except (KeyError, TypeError, ValueError, OverflowError):
        issues.append('Wallet timestamps are missing or invalid.')
    return tuple(issues)


def fetch_wallet_position(wallet, session=None, rpc_url=None, block_number=None):
    """Return only a complete, reconciled observation; never save a market snapshot.

    block_number is an explicit research/replay option. The dashboard fetches latest.
    Native account HF is observed, not substituted into the synthetic risk model.
    """
    normalized = validate_wallet_address(wallet)
    if block_number is not None and (type(block_number) is not int or block_number <= 0):
        raise SourceError('Invalid historical block number')
    client = session or requests
    endpoint = rpc_url if rpc_url is not None else os.getenv('ETH_RPC_URL', DEFAULT_ETH_RPC_URL)
    rpc = _RPC(client, endpoint)
    # Unchecksummed lower/upper-case addresses are accepted; enforce EIP-55 for mixed case.
    body = wallet[2:]
    if body != body.lower() and body != body.upper():
        digest = rpc.read('web3_sha3', ['0x' + normalized[2:].encode('ascii').hex()])
        _words(digest, 1)
        checksum = ''.join(char.upper() if int(digest[2 + i], 16) >= 8 else char
                           for i, char in enumerate(normalized[2:]))
        if body != checksum:
            raise SourceError('Invalid mixed-case EIP-55 wallet checksum')
    try:
        registry = client.get(ADDRESS_BOOK_URL, timeout=12)
        registry.raise_for_status()
        addresses = parse_address_book(registry.text)
    except (requests.RequestException, ValueError, TypeError) as exc:
        raise SourceError(f'Wallet registry retrieval failed ({type(exc).__name__})') from exc
    if _quantity(rpc.read('eth_chainId', [])) != 1:
        raise SourceError('Wallet RPC must be Ethereum mainnet (chain ID 1)')
    block = _header(rpc.read('eth_getBlockByNumber',
                             ['latest' if block_number is None else hex(block_number), False]), block_number)
    tag = hex(block['number'])
    provider = _address(addresses['POOL_ADDRESSES_PROVIDER'])
    pool, data_provider, oracle = [
        _decode_address(raw) for raw in rpc.calls([
            (provider, 'getPool()', ''), (provider, 'getPoolDataProvider()', ''),
            (provider, 'getPriceOracle()', '')], tag)]
    if any(actual != _address(addresses[key]) for actual, key in
           [(pool, 'POOL'), (data_provider, 'AAVE_PROTOCOL_DATA_PROVIDER'), (oracle, 'ORACLE')]):
        raise SourceError('Wallet market contracts differ from published registry')
    for code in rpc.many([('eth_getCode', [target, tag]) for target in (provider, pool, data_provider, oracle)]):
        _contract_code(code)
    checks = rpc.calls([(target, 'ADDRESSES_PROVIDER()', '') for target in (pool, data_provider, oracle)]
                      + [(data_provider, 'POOL()', '')], tag)
    if any(_decode_address(raw) != expected for raw, expected in zip(checks, [provider] * 3 + [pool])):
        raise SourceError('Wallet contract belongs to a different Aave market')
    arg = normalized[2:].rjust(64, '0')
    raw_account, raw_mode, raw_flags, raw_list, raw_count, raw_base, raw_unit = rpc.calls([
        (pool, 'getUserAccountData(address)', arg), (pool, 'getUserEMode(address)', arg),
        (pool, 'getUserConfiguration(address)', arg), (pool, 'getReservesList()', ''),
        (pool, 'getReservesCount()', ''), (oracle, 'BASE_CURRENCY()', ''),
        (oracle, 'BASE_CURRENCY_UNIT()', '')], tag)
    account = _words(raw_account, 6)
    mode, flags, count = [_words(raw, 1)[0] for raw in (raw_mode, raw_flags, raw_count)]
    if mode > 255 or not 1 <= count <= MAX_RESERVES:
        raise SourceError('Unsupported account eMode or reserve count')
    if flags >> (2 * count):
        raise SourceError('User configuration references unenumerated reserves')
    base = _decode_address(raw_base, allow_zero=True)
    unit = _words(raw_unit, 1)[0]
    # Verified Ethereum market convention: address(0) denotes USD and 1e8 units.
    if int(base, 16) != 0 or unit != 10**8:
        raise SourceError('Unsupported Aave oracle base currency; USD labeling refused')
    reserves = _address_array(raw_list)
    by_id = [_decode_address(raw, allow_zero=True) for raw in rpc.calls([
        (pool, 'getReserveAddressById(uint16)', format(i, '064x')) for i in range(count)], tag)]
    existing = [address for address in by_id if int(address, 16)]
    if len(set(existing)) != len(existing) or set(existing) != set(reserves):
        raise SourceError('Incomplete reserve enumeration: list and reserve IDs differ')
    for i, address in enumerate(by_id):
        if int(address, 16) == 0 and ((flags >> (2 * i)) & 3):
            raise SourceError('Unsupported account flags on a dropped reserve')
    emode = {'category_id': mode}
    if mode:
        config, bitmap = rpc.calls([(pool, 'getEModeCategoryCollateralConfig(uint8)', format(mode, '064x')),
                                   (pool, 'getEModeCategoryCollateralBitmap(uint8)', format(mode, '064x'))], tag)
        ltv, threshold, bonus = _words(config, 3)
        collateral_bitmap = _words(bitmap, 1)[0]
        if not 0 <= ltv <= threshold <= 10000 or not 10000 <= bonus <= 65535 or collateral_bitmap >= 2**128:
            raise SourceError('Unsupported or invalid eMode collateral configuration')
        emode.update(ltv_bps=ltv, liquidation_threshold_bps=threshold,
                     liquidation_bonus_bps=bonus, collateral_bitmap=collateral_bitmap)
    user_data = rpc.calls([(data_provider, 'getUserReserveData(address,address)',
                           address[2:].rjust(64, '0') + arg) for address in reserves], tag)
    assets, enumeration = [], []
    collateral_total = debt_total = 0
    for address, raw in zip(reserves, user_data):
        values = _words(raw, 9)
        supplied, stable, borrowed = values[:3]
        reserve_id = by_id.index(address)
        borrowing_flag, collateral_flag = bool(flags & (1 << (2 * reserve_id))), bool(flags & (2 << (2 * reserve_id)))
        if values[8] not in (0, 1) or bool(values[8]) != collateral_flag or borrowing_flag != bool(borrowed):
            raise SourceError('User reserve flags disagree with Pool configuration')
        if stable or values[3] or values[5] or values[7]:
            raise SourceError('Unsupported legacy stable-debt account features')
        enumeration.append({'reserve_address': address, 'reserve_id': reserve_id,
                            'supplied_raw': str(supplied), 'variable_debt_raw': str(borrowed),
                            'collateral_enabled': collateral_flag})
        if not supplied and not borrowed:
            continue
        underlying_arg = address[2:].rjust(64, '0')
        metadata = rpc.calls([(address, 'symbol()', ''), (address, 'decimals()', ''),
                              (data_provider, 'getReserveConfigurationData(address)', underlying_arg),
                              (data_provider, 'getReserveTokensAddresses(address)', underlying_arg),
                              (oracle, 'getAssetPrice(address)', underlying_arg)], tag)
        symbol, decimals = _symbol(metadata[0]), _words(metadata[1], 1)[0]
        reserve_config, tokens = _words(metadata[2], 10), _words(metadata[3], 3)
        if (decimals > 36 or reserve_config[0] != decimals or any(v not in (0, 1) for v in reserve_config[5:])
                or any(v > 10000 for v in (reserve_config[1], reserve_config[2], reserve_config[4]))):
            raise SourceError('Invalid or inconsistent reserve token metadata')
        token_addresses = [_decode_address('0x' + format(v, '064x'), allow_zero=(i == 1))
                           for i, v in enumerate(tokens)]
        for code in rpc.many([('eth_getCode', [target, tag]) for target in
                              (address, token_addresses[0], token_addresses[2])]):
            _contract_code(code)
        balances = rpc.calls([(token_addresses[0], 'balanceOf(address)', arg),
                              (token_addresses[2], 'balanceOf(address)', arg)], tag)
        if [_words(raw_balance, 1)[0] for raw_balance in balances] != [supplied, borrowed]:
            raise SourceError('Token balances disagree with user reserve data')
        price = _words(metadata[4], 1)[0]
        if price <= 0:
            raise SourceError('Invalid Aave oracle price for an active wallet asset')
        supplied_base = supplied * price // 10**decimals
        debt_base = (borrowed * price + 10**decimals - 1) // 10**decimals
        collateral_total += supplied_base if collateral_flag else 0
        debt_total += debt_base
        assets.append({'reserve_address': address, 'reserve_id': reserve_id, 'symbol': symbol,
                       'decimals': decimals, 'supplied_raw': str(supplied), 'variable_debt_raw': str(borrowed),
                       'stable_debt_raw': '0', 'supplied_units': _units(supplied, decimals),
                       'variable_debt_units': _units(borrowed, decimals), 'collateral_enabled': collateral_flag,
                       'a_token_address': token_addresses[0], 'variable_debt_token_address': token_addresses[2],
                       'oracle_price_base_raw': str(price), 'oracle_price_usd': _units(price, 8),
                       'supplied_value_base_raw': str(supplied_base), 'debt_value_base_raw': str(debt_base),
                       'reserve_configuration_raw': reserve_config, 'balances_cross_checked': True,
                       'provenance': {'chain_id': 1, 'block_number': block['number'], 'block_hash': block['hash'],
                                      'metadata_contract': address, 'balance_provider': data_provider,
                                      'price_oracle': oracle, 'rpc_host': rpc.host}})
    # Older deployments can round debt valuations down rather than up: allow only
    # two raw USD base units per active reserve, never a relative/USD-sized tolerance.
    tolerance = 2 * len(assets)
    if abs(account[0] - collateral_total) > tolerance or abs(account[1] - debt_total) > tolerance:
        raise SourceError('Account totals disagree with complete reserve balances and Aave oracle prices')
    if account[3] > 10000 or account[4] > 10000:
        raise SourceError('Invalid account liquidation threshold or LTV')
    if (account[1] == 0 and account[5] != MAX_UINT256) or (account[1] > 0 and account[5] == MAX_UINT256):
        raise SourceError('Inconsistent contract-reported health factor')
    if _header(rpc.read('eth_getBlockByNumber', [tag, False]), block['number']) != block:
        raise SourceError('Ethereum block changed during wallet discovery; retry')
    observation = {
        'wallet_address': normalized, 'chain_id': 1, 'block': block,
        'retrieved_at': datetime.now(timezone.utc).isoformat(), 'historical': block_number is not None,
        'rpc_host': rpc.host, 'addresses_provider': provider, 'pool': pool,
        'data_provider': data_provider, 'oracle': oracle,
        'registry_source': ADDRESS_BOOK_URL,
        'registry_sha256': hashlib.sha256(registry.text.encode('utf-8')).hexdigest(),
        'base_currency': {'address': base, 'label': 'USD', 'unit': unit, 'decimals': 8},
        'account': dict(zip(('total_collateral_base_raw', 'total_debt_base_raw', 'available_borrows_base_raw',
                             'liquidation_threshold_bps', 'ltv_bps', 'health_factor_raw'), map(str, account))),
        'collateral_usd': _units(account[0], 8), 'debt_usd': _units(account[1], 8),
        'health_factor': None if account[1] == 0 else _units(account[5], 18),
        'user_configuration_raw': str(flags), 'emode': emode,
        'reserve_count_including_dropped': count, 'enumerated_reserves': enumeration,
        'assets': assets, 'empty': not assets, 'complete': True,
        'reconciliation': {'collateral_base_raw': str(collateral_total), 'debt_base_raw': str(debt_total),
                           'tolerance_base_units': tolerance},
        'rpc_evidence': rpc.evidence,
        'transport': {'mode': 'sequential fallback' if rpc.sequential else 'bounded batches',
                      'http_requests': rpc.http_requests, 'transient_retries': rpc.retries,
                      'timeout_seconds': RPC_TIMEOUT_SECONDS, 'max_http_requests': MAX_HTTP_REQUESTS,
                      'max_import_seconds': MAX_IMPORT_SECONDS, 'diagnostics': rpc.diagnostics},
        'limitations': 'Read-only dated account discovery, not a wallet ownership claim, '
                       'scenario decision, liquidation prediction or executable withdrawal quote. '
                       'eMode is observed; its economic effects are not simulated. '
                       'Oracle answers are read at the pinned block; underlying feed freshness is not verified.',
    }
    for asset in assets:
        asset['provenance'].update(block_timestamp=block['timestamp'],
                                   retrieved_at=observation['retrieved_at'])
    # Supplementary discovery has a separate evidence log and time/request budget.
    # Its failure cannot rewrite or downgrade the reconciled account observation.
    from src.wallet_oracles import discover_oracle_sources
    source_rpc = _RPC(client, endpoint, max_seconds=30, max_requests=2*len(assets)+4,
                      timeout_seconds=5, max_retries=2)
    observation['oracle_sources'] = discover_oracle_sources(source_rpc, observation)
    return observation
