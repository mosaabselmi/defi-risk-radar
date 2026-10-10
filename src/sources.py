"""Public, no-key source adapters with explicit validation and timestamps.

The Aave Address Book is a published *registry*, not proof of current on-chain state.
DefiLlama gives market-price observations, not native oracle answers.
"""
import hashlib
import math
import os
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import requests

ADDRESS_BOOK_URL = (
    'https://raw.githubusercontent.com/aave-dao/aave-address-book/main/src/'
    'AaveV3Ethereum.sol'
)
PRICE_API = 'https://coins.llama.fi/prices/current/'
REQUIRED = (
    'POOL', 'ORACLE', 'WETH_UNDERLYING', 'USDC_UNDERLYING',
    'WETH_ORACLE', 'USDC_ORACLE', 'POOL_ADDRESSES_PROVIDER',
    'AAVE_PROTOCOL_DATA_PROVIDER',
)
DEFAULT_ETH_RPC_URL = 'https://ethereum-rpc.publicnode.com'
# First four bytes of Ethereum Keccak-256(signature), verified via web3_sha3.
# Interfaces: aave-dao/aave-v3-origin/src/contracts/interfaces/
# IPoolAddressesProvider.sol and IPoolDataProvider.sol.
SELECTORS = {
    'getPool()': '0x026b1d5f',
    'getPoolDataProvider()': '0xe860accb',
    'ADDRESSES_PROVIDER()': '0x0542975c',
    'POOL()': '0x7535d246',
    'getReserveConfigurationData(address)': '0x3e150141',
}
ASSETS = {
    'WETH': ('0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2', 18),
    'USDC': ('0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48', 6),
}
CONFIG_FIELDS = (
    'decimals', 'ltv_bps', 'liquidation_threshold_bps',
    'liquidation_bonus_bps', 'reserve_factor_bps',
    'usage_as_collateral_enabled', 'borrowing_enabled',
    'stable_borrow_rate_enabled', 'is_active', 'is_frozen',
)
ADDRESS_PATTERN = re.compile(
    r'\b([A-Za-z][A-Za-z0-9_]*)\s*=\s*'
    r'(?:[A-Za-z_][A-Za-z0-9_]*\s*\(\s*)?'
    r'(0x[0-9a-fA-F]{40})\b', re.MULTILINE
)


class SourceError(RuntimeError):
    """A remote source failed or returned invalid data."""


def _words(raw: str, count: int) -> list[int]:
    if not isinstance(raw, str) or not re.fullmatch(r'0x[0-9a-fA-F]{' + str(64 * count) + '}', raw):
        raise SourceError(f'Invalid ABI response: expected {count} 32-byte words')
    return [int(raw[i:i + 64], 16) for i in range(2, len(raw), 64)]


def _address(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'0x[0-9a-fA-F]{40}', value) or int(value, 16) == 0:
        raise SourceError('Invalid or zero contract address')
    return value.lower()


def _decode_address(raw: str) -> str:
    word = _words(raw, 1)[0]
    if word >= 2 ** 160:
        raise SourceError('Invalid ABI address padding')
    return _address(f'0x{word:040x}')


def _quantity(raw: str) -> int:
    if not isinstance(raw, str) or not re.fullmatch(r'0x(?:0|[1-9a-fA-F][0-9a-fA-F]*)', raw):
        raise SourceError('Invalid RPC quantity')
    return int(raw, 16)


def parse_reserve_configuration(raw: str, asset: str) -> dict[str, Any]:
    """Decode the fixed ten-word tuple; percentages remain raw basis points."""
    values = _words(raw, 10)
    decimals, ltv, threshold, bonus, factor = values[:5]
    if asset not in ASSETS or decimals != ASSETS[asset][1]:
        raise SourceError('Unexpected reserve decimals or asset')
    if not 0 <= ltv <= threshold <= 10_000 or factor > 10_000:
        raise SourceError('Invalid reserve percentages')
    if bonus > 65_535 or (threshold > 0 and bonus < 10_000):
        raise SourceError('Invalid liquidation bonus')
    if any(value not in (0, 1) for value in values[5:]):
        raise SourceError('Invalid ABI boolean')
    if bool(values[5]) != (threshold != 0):
        raise SourceError('Inconsistent collateral configuration')
    return dict(zip(CONFIG_FIELDS, values[:5] + [bool(v) for v in values[5:]]))


def fetch_reserve_configuration(addresses: dict[str, str], session=None,
                                rpc_url: str | None = None) -> dict[str, Any]:
    """Read both reserves at one block; reject reorgs and market mismatches."""
    client = session or requests
    endpoint = rpc_url if rpc_url is not None else os.getenv('ETH_RPC_URL', DEFAULT_ETH_RPC_URL)
    try:
        if not isinstance(endpoint, str):
            raise ValueError('Invalid endpoint type')
        parsed_url = urlsplit(endpoint)
        parsed_url.port  # Validate malformed port numbers before making a request.
    except ValueError as exc:
        raise SourceError('ETH_RPC_URL must be a valid HTTP(S) endpoint') from exc
    if parsed_url.scheme not in ('http', 'https') or not parsed_url.hostname:
        raise SourceError('ETH_RPC_URL must be an HTTP(S) endpoint')
    evidence = []
    request_id = 0

    def rpc(method, params):
        nonlocal request_id
        request_id += 1
        try:
            response = client.post(endpoint, json={
                'jsonrpc': '2.0', 'id': request_id, 'method': method, 'params': params,
            }, timeout=12)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            # Do not expose endpoint credentials through exception URLs.
            raise SourceError(f'Ethereum RPC {method} request failed ({type(exc).__name__})') from exc
        if (not isinstance(payload, dict) or payload.get('jsonrpc') != '2.0'
                or type(payload.get('id')) is not int or payload['id'] != request_id
                or 'error' in payload or payload.get('result') is None):
            raise SourceError(f'Ethereum RPC {method} returned an error or invalid response')
        return payload['result']

    def block_header(raw, expected_number=None):
        if not isinstance(raw, dict):
            raise SourceError('Missing Ethereum block header')
        number = _quantity(raw.get('number'))
        timestamp = _quantity(raw.get('timestamp'))
        block_hash = raw.get('hash')
        if (number <= 0 or timestamp <= 0 or not isinstance(block_hash, str)
                or not re.fullmatch(r'0x[0-9a-fA-F]{64}', block_hash)
                or (expected_number is not None and number != expected_number)):
            raise SourceError('Invalid Ethereum block header')
        return {'number': number, 'hash': block_hash.lower(), 'timestamp': timestamp}

    if _quantity(rpc('eth_chainId', [])) != 1:
        raise SourceError('ETH_RPC_URL is not Ethereum mainnet (chain ID 1)')
    block = block_header(rpc('eth_getBlockByNumber', ['latest', False]))
    tag = hex(block['number'])

    def call(target, signature, argument=''):
        data = SELECTORS[signature] + argument
        raw = rpc('eth_call', [{'to': target, 'data': data}, tag])
        evidence.append({'to': target, 'signature': signature, 'data': data,
                         'block_tag': tag, 'result': raw})
        return raw

    def contract(target):
        code = rpc('eth_getCode', [target, tag])
        if not isinstance(code, str) or not re.fullmatch(r'0x(?:[0-9a-fA-F]{2})+', code):
            raise SourceError('Expected deployed contract code at snapshot block')

    try:
        provider = _address(addresses['POOL_ADDRESSES_PROVIDER'])
        contract(provider)
        pool = _decode_address(call(provider, 'getPool()'))
        data_provider = _decode_address(call(provider, 'getPoolDataProvider()'))
        if (pool != _address(addresses['POOL'])
                or data_provider != _address(addresses['AAVE_PROTOCOL_DATA_PROVIDER'])):
            raise SourceError('On-chain market contracts differ from published registry')
        contract(pool)
        contract(data_provider)
        if (_decode_address(call(data_provider, 'ADDRESSES_PROVIDER()')) != provider
                or _decode_address(call(data_provider, 'POOL()')) != pool):
            raise SourceError('Data provider belongs to a different Aave market')
        reserves = {}
        for asset, (expected_address, _) in ASSETS.items():
            underlying = _address(addresses[asset + '_UNDERLYING'])
            if underlying != expected_address:
                raise SourceError(f'Unexpected Ethereum {asset} underlying address')
            raw = call(data_provider, 'getReserveConfigurationData(address)', underlying[2:].zfill(64))
            reserves[asset] = {'underlying_address': underlying,
                               **parse_reserve_configuration(raw, asset)}
    except (KeyError, TypeError) as exc:
        raise SourceError('Incomplete reserve registry data') from exc
    if block_header(rpc('eth_getBlockByNumber', [tag, False]), block['number']) != block:
        raise SourceError('Ethereum block changed during retrieval; retry refresh')
    return {
        'chain_id': 1, 'block': block, 'addresses_provider': provider,
        'pool': pool, 'data_provider': data_provider,
        # Host only: never persist RPC URL userinfo, path or query credentials.
        'rpc_host': parsed_url.hostname,
        'retrieved_at': datetime.now(timezone.utc).isoformat(),
        'reserves': reserves, 'calls': evidence,
    }


def validate_snapshot(snapshot: dict[str, Any]) -> None:
    """Accept legacy price/registry snapshots; fully validate optional reserve evidence."""
    try:
        if (not isinstance(snapshot, dict) or snapshot.get('mode') != 'sourced'
                or not isinstance(snapshot.get('addresses'), dict)
                or not isinstance(snapshot.get('retrieved_at'), str)):
            raise SourceError('Invalid sourced snapshot')
        for asset in ASSETS:
            price = snapshot['prices'][asset]['usd']
            if type(price) not in (int, float) or not math.isfinite(price) or price <= 0:
                raise SourceError('Invalid snapshot price')
        if 'reserve_configuration' not in snapshot:
            return
        config = snapshot['reserve_configuration']
        if type(config['chain_id']) is not int or config['chain_id'] != 1:
            raise SourceError('Invalid reserve chain ID')
        block = config['block']
        if (type(block['number']) is not int or block['number'] <= 0
                or type(block['timestamp']) is not int or block['timestamp'] <= 0
                or not re.fullmatch(r'0x[0-9a-fA-F]{64}', block['hash'])
                or not isinstance(config['retrieved_at'], str)
                or not isinstance(config['rpc_host'], str)):
            raise SourceError('Invalid saved reserve provenance')
        known = snapshot['addresses']
        for field, key in [('addresses_provider', 'POOL_ADDRESSES_PROVIDER'),
                           ('pool', 'POOL'), ('data_provider', 'AAVE_PROTOCOL_DATA_PROVIDER')]:
            if _address(config[field]) != _address(known[key]):
                raise SourceError('Saved reserve market mismatch')
        calls = config['calls']
        if not isinstance(calls, list) or len(calls) != 6:
            raise SourceError('Incomplete saved contract evidence')
        expected_calls = [
            (config['addresses_provider'], 'getPool()', '', config['pool']),
            (config['addresses_provider'], 'getPoolDataProvider()', '', config['data_provider']),
            (config['data_provider'], 'ADDRESSES_PROVIDER()', '', config['addresses_provider']),
            (config['data_provider'], 'POOL()', '', config['pool']),
        ]
        for call_record, (target, signature, argument, result) in zip(calls, expected_calls):
            if (_address(call_record['to']) != _address(target)
                    or call_record['signature'] != signature
                    or call_record['data'] != SELECTORS[signature] + argument
                    or call_record['block_tag'] != hex(block['number'])
                    or _decode_address(call_record['result']) != _address(result)):
                raise SourceError('Inconsistent saved provider evidence')
        for index, (asset, (underlying, _)) in enumerate(ASSETS.items(), 4):
            reserve = config['reserves'][asset]
            record = calls[index]
            signature = 'getReserveConfigurationData(address)'
            if (_address(reserve['underlying_address']) != underlying
                    or _address(known[asset + '_UNDERLYING']) != underlying
                    or _address(record['to']) != _address(config['data_provider'])
                    or record['signature'] != signature
                    or record['data'] != SELECTORS[signature] + underlying[2:].zfill(64)
                    or record['block_tag'] != hex(block['number'])):
                raise SourceError('Inconsistent saved reserve evidence')
            decoded = parse_reserve_configuration(record['result'], asset)
            if any(type(reserve[k]) is not type(v) or reserve[k] != v for k, v in decoded.items()):
                raise SourceError('Saved configuration differs from raw contract response')
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise SourceError('Incomplete or invalid saved source snapshot') from exc


def parse_address_book(solidity_source: str) -> dict[str, str]:
    addresses = dict(ADDRESS_PATTERN.findall(solidity_source))
    missing = set(REQUIRED) - addresses.keys()
    if missing:
        raise SourceError(f'Aave registry missing fields: {sorted(missing)}')
    return {name: addresses[name] for name in REQUIRED}


def parse_defillama_prices(payload: dict[str, Any], addresses: dict[str, str]) -> dict[str, Any]:
    coins = payload.get('coins', {})
    parsed = {}
    for asset in ('WETH', 'USDC'):
        lookup = f"ethereum:{addresses[asset + '_UNDERLYING']}".lower()
        normalized = {k.lower(): v for k, v in coins.items()}
        item = normalized.get(lookup)
        if not isinstance(item, dict) or not isinstance(item.get('price'), (int, float)):
            raise SourceError(f'Missing valid {asset} spot-price observation')
        if item['price'] <= 0:
            raise SourceError(f'Nonpositive {asset} price')
        parsed[asset] = {
            'usd': float(item['price']),
            'timestamp': item.get('timestamp'),
            'confidence': item.get('confidence'),
        }
    return parsed


def fetch_market_snapshot(session=None, rpc_url: str | None = None) -> dict[str, Any]:
    client = session or requests
    try:
        registry_res = client.get(ADDRESS_BOOK_URL, timeout=12)
        registry_res.raise_for_status()
        addresses = parse_address_book(registry_res.text)
        coin_ids = ','.join(
            f"ethereum:{addresses[asset + '_UNDERLYING']}"
            for asset in ('WETH', 'USDC')
        )
        price_url = PRICE_API + coin_ids
        prices_res = client.get(price_url, timeout=12)
        prices_res.raise_for_status()
        prices = parse_defillama_prices(prices_res.json(), addresses)
        reserve_configuration = fetch_reserve_configuration(addresses, client, rpc_url)
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        raise SourceError(f'Live market-data retrieval failed: {exc}') from exc
    snapshot = {
        'mode': 'sourced',
        'retrieved_at': datetime.now(timezone.utc).isoformat(),
        'network': 'Ethereum mainnet',
        'protocol': 'Aave V3',
        'registry_source': ADDRESS_BOOK_URL,
        'registry_sha256': hashlib.sha256(registry_res.text.encode('utf-8')).hexdigest(),
        'price_source': price_url,
        'data_limitations': (
            'Addresses come from a published registry, not direct on-chain calls. '
            'Prices come from DefiLlama, not the Aave protocol oracle. '
            'Base reserve configuration was read on-chain at the recorded block. '
            'Holdings, graph edges and scenario outcomes remain modeled; eMode is not modeled.'
        ),
        'addresses': addresses,
        'prices': prices,
        'reserve_configuration': reserve_configuration,
    }
    validate_snapshot(snapshot)
    return snapshot


DEMO_SNAPSHOT = {
    'mode': 'illustrative',
    'retrieved_at': None,
    'network': 'Ethereum mainnet (illustrative)',
    'protocol': 'Aave V3 (illustrative topology)',
    'registry_source': None,
    'price_source': None,
    'data_limitations': 'Offline demo only: no verified live records or current market prices.',
    'addresses': {},
    'prices': {'WETH': {'usd': 2500.0}, 'USDC': {'usd': 1.0}},
}
