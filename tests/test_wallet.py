"""Deterministic wallet evidence tests; never fetch or overwrite project snapshots."""
import copy
import json
from datetime import datetime, timedelta, timezone

import pytest
import requests

from src.sources import REQUIRED, SourceError
from src.wallet import (MAX_UINT256, WALLET_SELECTORS, fetch_wallet_position,
                        validate_wallet_address, wallet_quality_issues)


def addr(number):
    return '0x' + format(number, '040x')


def abi(*values):
    return '0x' + ''.join(format(value, '064x') for value in values)


def symbol_abi(symbol):
    encoded = symbol.encode('utf-8')
    return abi(32, len(encoded)) + encoded.hex().ljust(((len(encoded) + 31) // 32) * 64, '0')


class Response:
    def __init__(self, payload=None, text=''):
        self.payload, self.text = payload, text

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class WalletRPC:
    def __init__(self):
        self.registry = {name: addr(i + 1) for i, name in enumerate(REQUIRED)}
        self.pool = self.registry['POOL']
        self.provider = self.registry['POOL_ADDRESSES_PROVIDER']
        self.data_provider = self.registry['AAVE_PROTOCOL_DATA_PROVIDER']
        self.oracle = self.registry['ORACLE']
        self.reserves = [addr(100), addr(101), addr(102)]
        self.symbols = ['WETH', 'USDT', 'GHO']
        self.decimals = [18, 6, 18]
        self.supplied = [4 * 10**18, 0, 0]
        self.debts = [0, 6000 * 10**6, 10**17]
        self.prices = [2500 * 10**8, 10**8, 10**8]
        self.flags, self.mode = 2 | 4 | 16, 0
        self.block = {'number': '0x123', 'hash': '0x' + 'ab' * 32,
                      'timestamp': hex(int(datetime.now(timezone.utc).timestamp()))}
        self.overrides, self.requests = {}, []
        self.failure = None
        self.batch_failure = None
        self.reorg = False
        self.headers = 0
        self.digest = '0x' + '0' * 64

    def get(self, url, timeout):
        self.requests.append(('GET', url, None, timeout))
        return Response(text='\n'.join(f'address constant {key} = {value};' for key, value in self.registry.items()))

    def post(self, url, json, timeout):
        self.requests.append(('POST', url, copy.deepcopy(json), timeout))
        if self.failure:
            raise self.failure
        if isinstance(json, list):
            bodies = [self.reply(item) for item in json]
            if self.batch_failure == 'missing':
                bodies.pop()
            elif self.batch_failure == 'duplicate':
                bodies[-1] = bodies[0]
            elif self.batch_failure == 'error':
                bodies[-1] = {'jsonrpc': '2.0', 'id': bodies[-1]['id'], 'error': {'code': -32000}}
            return Response(list(reversed(bodies)))  # RPC batches need not arrive in order.
        return Response(self.reply(json))

    def reply(self, request):
        method, params = request['method'], request['params']
        key = method
        if method == 'eth_call':
            target, data = params[0]['to'], params[0]['data']
            signature = next(name for name, selector in WALLET_SELECTORS.items() if data[:10] == selector)
            key = (target, signature)
            result = self.contract(target, signature, data[10:])
        elif method == 'eth_chainId':
            result = '0x1'
        elif method == 'eth_getCode':
            result = '0x6000'
        elif method == 'eth_getBlockByNumber':
            self.headers += 1
            result = dict(self.block)
            if self.reorg and self.headers > 1:
                result['hash'] = '0x' + 'cd' * 32
        elif method == 'web3_sha3':
            result = self.digest
        else:
            raise AssertionError(method)
        result = self.overrides.get(key, self.overrides.get(key[1] if isinstance(key, tuple) else key, result))
        return {'jsonrpc': '2.0', 'id': request['id'], 'result': result}

    def contract(self, target, signature, argument):
        if signature == 'getPool()' or signature == 'POOL()':
            return abi(int(self.pool, 16))
        if signature == 'getPoolDataProvider()':
            return abi(int(self.data_provider, 16))
        if signature == 'getPriceOracle()':
            return abi(int(self.oracle, 16))
        if signature == 'ADDRESSES_PROVIDER()':
            return abi(int(self.provider, 16))
        if signature == 'BASE_CURRENCY()':
            return abi(0)
        if signature == 'BASE_CURRENCY_UNIT()':
            return abi(10**8)
        if signature == 'getUserAccountData(address)':
            collateral = sum(s * p // 10**d for i, (s, p, d) in enumerate(zip(self.supplied, self.prices, self.decimals)) if self.flags & (2 << (2 * i)))
            debt = sum((b * p + 10**d - 1) // 10**d for b, p, d in zip(self.debts, self.prices, self.decimals))
            return abi(collateral, debt, max(collateral * 8000 // 10000 - debt, 0),
                       8300 if collateral else 0, 8000 if collateral else 0,
                       collateral * 8300 * 10**18 // (10000 * debt) if debt else MAX_UINT256)
        if signature == 'getUserEMode(address)':
            return abi(self.mode)
        if signature == 'getUserConfiguration(address)':
            return abi(self.flags)
        if signature == 'getReservesList()':
            return abi(32, len(self.reserves), *(int(a, 16) for a in self.reserves))
        if signature == 'getReservesCount()':
            return abi(len(self.reserves))
        if signature == 'getReserveAddressById(uint16)':
            return abi(int(self.reserves[int(argument, 16)], 16))
        if signature == 'getEModeCategoryCollateralConfig(uint8)':
            return abi(9300, 9500, 10100)
        if signature == 'getEModeCategoryCollateralBitmap(uint8)':
            return abi(1)
        if signature in ('symbol()', 'decimals()'):
            index = self.reserves.index(target)
            return symbol_abi(self.symbols[index]) if signature == 'symbol()' else abi(self.decimals[index])
        if signature == 'balanceOf(address)':
            index, debt_token = divmod(int(target, 16) - 1000, 2)
            return abi(self.debts[index] if debt_token else self.supplied[index])
        index = self.reserves.index(addr(int(argument[:64], 16)))
        if signature == 'getUserReserveData(address,address)':
            return abi(self.supplied[index], 0, self.debts[index], 0, self.debts[index], 0, 0, 0,
                       int(bool(self.flags & (2 << (2 * index)))))
        if signature == 'getReserveConfigurationData(address)':
            return abi(self.decimals[index], 8000, 8300, 10500, 1000, 1, 1, 0, 1, 0)
        if signature == 'getReserveTokensAddresses(address)':
            return abi(1000 + 2 * index, 0, 1001 + 2 * index)
        if signature == 'getAssetPrice(address)':
            return abi(self.prices[index])
        raise AssertionError(signature)


@pytest.fixture
def rpc():
    return WalletRPC()


WALLET = addr(999)


def fetch(rpc, **kwargs):
    return fetch_wallet_position(WALLET, rpc, 'https://rpc.example/private-secret', **kwargs)


def test_complete_multiple_debt_assets_and_evidence(rpc):
    result = fetch(rpc)
    assert result['complete'] and not result['empty']
    assert [a['symbol'] for a in result['assets']] == ['WETH', 'USDT', 'GHO']
    assert [a['variable_debt_units'] for a in result['assets']] == ['0', '6000', '0.1']
    assert result['collateral_usd'] == '10000' and result['debt_usd'] == '6000.1'
    assert float(result['health_factor']) == pytest.approx(8300 / 6000.1)
    assert result['base_currency']['unit'] == 10**8
    assert len(result['enumerated_reserves']) == 3
    assert all(a['balances_cross_checked'] for a in result['assets'])
    assert result['assets'][0]['provenance']['metadata_contract'] == rpc.reserves[0]
    assert 'private-secret' not in json.dumps(result)
    assert all(record['params'][1] == '0x123' for record in result['rpc_evidence'] if record['method'] == 'eth_call')
    assert all(record['params'][1] == '0x123' for record in result['rpc_evidence'] if record['method'] == 'eth_getCode')
    assert all(timeout == 12 for _, _, _, timeout in rpc.requests)
    assert not wallet_quality_issues(result)


def test_empty_wallet_and_debt_free_supplies(rpc):
    rpc.supplied = [0, 0, 0]
    rpc.debts = [0, 0, 0]
    rpc.flags = 0
    result = fetch(rpc)
    assert result['empty'] and result['assets'] == []
    assert result['health_factor'] is None and result['account']['health_factor_raw'] == str(MAX_UINT256)
    rpc.supplied[0] = 10**18
    result = fetch(rpc)
    assert not result['empty'] and result['collateral_usd'] == '0'
    assert result['assets'][0]['collateral_enabled'] is False


@pytest.mark.parametrize('wallet', ['', 'vitalik.eth', ' ' + WALLET, WALLET + ' ',
                                    addr(0), '0X' + 'a' * 40, '0x' + 'g' * 40, '0x123', None])
def test_invalid_wallet_rejected_before_network(rpc, wallet):
    with pytest.raises(SourceError):
        fetch_wallet_position(wallet, rpc)
    assert not rpc.requests


def test_checksum_validation(rpc):
    wallet = '0xA' + 'a' * 39
    with pytest.raises(SourceError, match='checksum'):
        fetch_wallet_position(wallet, rpc)
    rpc.digest = '0x8' + '0' * 63
    assert fetch_wallet_position(wallet, rpc)['wallet_address'] == wallet.lower()
    assert validate_wallet_address('0x' + 'A' * 40) == '0x' + 'a' * 40


@pytest.mark.parametrize('signature,raw', [
    ('getUserAccountData(address)', '0x'), ('getUserReserveData(address,address)', abi(*range(8))),
    ('getReservesList()', abi(64, 3, 100, 101, 102)),
    ('getReservesList()', abi(32, 3, 100, 101)),
    ('getReservesList()', abi(32, 3, 100, 100, 102)),
    ('getReservesCount()', abi(2)), ('getReservesCount()', abi(129)),
    ('getReserveAddressById(uint16)', abi(100)),
    ('getUserEMode(address)', abi(256)), ('getUserConfiguration(address)', abi(1 << 200)),
    ('BASE_CURRENCY()', abi(100)), ('BASE_CURRENCY_UNIT()', abi(10**18)),
    ('decimals()', abi(37)), ('symbol()', abi(123)),
    ('symbol()', symbol_abi('\n')), ('getAssetPrice(address)', abi(0)),
    ('getReserveTokensAddresses(address)', abi(0, 0, 0)),
    ('balanceOf(address)', abi(1)), ('eth_chainId', '0xa'), ('eth_getCode', '0x'),
])
def test_malformed_or_inconsistent_data_fails_closed(rpc, signature, raw):
    rpc.overrides[signature] = raw
    with pytest.raises(SourceError):
        fetch(rpc)


def test_mismatched_totals_not_relative_tolerance(rpc):
    rpc.overrides['getUserAccountData(address)'] = abi(10000 * 10**8, 6001 * 10**8, 0, 8300, 8000, 10**18)
    with pytest.raises(SourceError, match='totals disagree'):
        fetch(rpc)


def test_unsupported_legacy_features_and_invalid_flags(rpc):
    rpc.overrides['getUserReserveData(address,address)'] = abi(0, 1, 0, 0, 0, 0, 0, 0, 1)
    with pytest.raises(SourceError, match='legacy'):
        fetch(rpc)
    rpc.overrides['getUserReserveData(address,address)'] = abi(0, 0, 0, 0, 0, 0, 0, 0, 2)
    with pytest.raises(SourceError, match='flags'):
        fetch(rpc)


@pytest.mark.parametrize('failure', ['missing', 'duplicate', 'error'])
def test_partial_or_invalid_batches_fail_closed(rpc, failure):
    rpc.batch_failure = failure
    with pytest.raises(SourceError):
        fetch(rpc)


@pytest.mark.parametrize('failure', [requests.Timeout('secret'), requests.HTTPError('secret'), ValueError('secret')])
def test_transport_error_redaction(rpc, failure):
    rpc.failure = failure
    with pytest.raises(SourceError) as error:
        fetch(rpc)
    assert 'secret' not in str(error.value)


def test_emode_is_observed_without_scenario_integration(rpc):
    rpc.mode = 1
    result = fetch(rpc)
    assert result['emode']['category_id'] == 1
    assert result['emode']['collateral_bitmap'] == 1
    assert result['emode']['liquidation_threshold_bps'] == 9500
    assert 'recommendation' not in result
    rpc.overrides['getEModeCategoryCollateralConfig(uint8)'] = '0x'
    with pytest.raises(SourceError):
        fetch(rpc)


def test_historical_stale_and_reorg_evidence(rpc):
    rpc.block['timestamp'] = hex(int((datetime.now(timezone.utc) - timedelta(hours=2)).timestamp()))
    result = fetch(rpc, block_number=291)
    assert result['historical'] and len(wallet_quality_issues(result)) == 2
    original_timestamp = result['retrieved_at']
    assert wallet_quality_issues(result, datetime.now(timezone.utc) + timedelta(hours=3))
    assert result['retrieved_at'] == original_timestamp
    rpc.reorg = True
    rpc.headers = 0
    with pytest.raises(SourceError, match='block changed'):
        fetch(rpc)


def test_wallet_ui_isolated_and_failed_refresh_clears_wallet(tmp_path, rpc, monkeypatch):
    from test_block04 import dashboard, click, rendered
    import src.wallet_ui as ui
    result = fetch(rpc)
    monkeypatch.setattr(ui, 'fetch_wallet_position', lambda wallet: result)
    app, saved = dashboard(tmp_path)
    before = copy.deepcopy(app.session_state['snapshot'])
    next(field for field in app.text_input if field.label == 'Ethereum wallet address').set_value(WALLET)
    click(app, 'Inspect Aave wallet')
    assert app.session_state['snapshot'] == before and not saved.exists()
    assert any('READ-ONLY WALLET OBSERVATION' in info.value for info in app.info)
    assert any(metric.label == 'Contract-reported HF' for metric in app.metric)
    result['historical'] = True
    app.run()
    assert 'HISTORICAL WALLET OBSERVATION / NOT LIVE' in rendered(app)
    def fail(wallet):
        raise SourceError('partial reserve failure')
    monkeypatch.setattr(ui, 'fetch_wallet_position', fail)
    click(app, 'Inspect Aave wallet')
    assert app.session_state['wallet_observation'] is None
    assert app.session_state['snapshot'] == before and not saved.exists()
    assert not any(metric.label == 'Contract-reported HF' for metric in app.metric)


def test_empty_wallet_ui_has_no_wallet_score(tmp_path, rpc, monkeypatch):
    from test_block04 import dashboard, click
    import src.wallet_ui as ui
    rpc.supplied = rpc.debts = [0, 0, 0]
    rpc.flags = 0
    result = fetch(rpc)
    monkeypatch.setattr(ui, 'fetch_wallet_position', lambda wallet: result)
    app, saved = dashboard(tmp_path)
    click(app, 'Inspect Aave wallet')
    assert any('No nonzero supplied or borrowed' in info.value for info in app.info)
    assert not any(metric.label == 'Contract-reported HF' for metric in app.metric)
    assert not saved.exists()


@pytest.mark.parametrize('endpoint', ['', None, 123, 'ftp://rpc.example', 'https://rpc.example:bad'])
def test_invalid_rpc_endpoint(rpc, endpoint):
    # None deliberately means configured/default endpoint in the public API.
    if endpoint is None:
        from src.wallet import _RPC
        with pytest.raises(SourceError):
            _RPC(rpc, None)
    else:
        with pytest.raises(SourceError):
            fetch_wallet_position(WALLET, rpc, endpoint)
    assert not rpc.requests


def test_metadata_decimals_are_contract_sourced_and_checked(rpc):
    rpc.overrides[(rpc.reserves[0], 'decimals()')] = abi(6)
    with pytest.raises(SourceError, match='metadata'):
        fetch(rpc)


def test_inactive_reserve_failure_prevents_complete_import(rpc):
    rpc.supplied = [0, 0, 0]
    rpc.debts = [0, 0, 0]
    rpc.flags = 0
    rpc.overrides[(rpc.data_provider, 'getUserReserveData(address,address)')] = None
    with pytest.raises(SourceError):
        fetch(rpc)


def test_wallet_age_changes_without_refreshing_evidence(rpc):
    result = fetch(rpc)
    assert not wallet_quality_issues(result)
    original = copy.deepcopy(result)
    assert wallet_quality_issues(result, datetime.now(timezone.utc) + timedelta(hours=2))
    assert result == original


def test_bounded_batches_and_complete_id_enumeration(rpc):
    # Repeat zero positions with distinct addresses to exercise multiple batches.
    rpc.reserves = [addr(100 + i) for i in range(25)]
    rpc.symbols = ['TOKEN'] * 25
    rpc.decimals = [18] * 25
    rpc.supplied = rpc.debts = [0] * 25
    rpc.prices = [10**8] * 25
    rpc.flags = 0
    result = fetch(rpc)
    assert result['complete'] and len(result['enumerated_reserves']) == 25
    batches = [payload for method, _, payload, _ in rpc.requests if method == 'POST' and isinstance(payload, list)]
    assert max(map(len, batches)) <= 20
