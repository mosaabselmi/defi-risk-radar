"""Synthetic RPC fixtures only; no network or changes to the saved project snapshot."""
import copy
import json
from pathlib import Path

import pytest
import requests

from src.sources import (
    ASSETS, DEFAULT_ETH_RPC_URL, DEMO_SNAPSHOT, REQUIRED, SELECTORS,
    SourceError, fetch_market_snapshot, fetch_reserve_configuration,
    parse_reserve_configuration, validate_snapshot,
)


def abi(*words):
    return '0x' + ''.join(f'{word:064x}' for word in words)


@pytest.fixture
def addresses():
    result = {key: '0x' + f'{index + 1:040x}' for index, key in enumerate(REQUIRED)}
    for asset, (underlying, _) in ASSETS.items():
        result[asset + '_UNDERLYING'] = underlying
    return result


class Response:
    def __init__(self, payload=None, text=''):
        self.payload, self.text = payload, text

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class RPC:
    def __init__(self, addresses):
        self.addresses = addresses
        self.requests = []
        self.headers = 0
        self.block = {'number': '0x123', 'hash': '0x' + 'ab' * 32, 'timestamp': '0x65000000'}
        self.overrides = {}
        self.reorg = False
        self.failure = None

    def get(self, url, timeout):
        if 'raw.githubusercontent.com' in url:
            return Response(text='\n'.join(
                f'address internal constant {key} = {value};'
                for key, value in self.addresses.items()))
        return Response({'coins': {
            'ethereum:' + underlying: {'price': 2500 if asset == 'WETH' else 1,
                                       'timestamp': 1, 'confidence': 0.99}
            for asset, (underlying, _) in ASSETS.items()
        }})

    def post(self, url, json, timeout):
        self.requests.append((url, json, timeout))
        if self.failure:
            raise self.failure
        method, params = json['method'], json['params']
        if method == 'eth_chainId':
            result = '0x1'
        elif method == 'eth_getBlockByNumber':
            self.headers += 1
            result = dict(self.block)
            if self.reorg and self.headers > 1:
                result['hash'] = '0x' + 'cd' * 32
        elif method == 'eth_getCode':
            result = '0x6000'
        else:
            data = params[0]['data']
            targets = {
                '0x026b1d5f': 'POOL',
                '0xe860accb': 'AAVE_PROTOCOL_DATA_PROVIDER',
                '0x0542975c': 'POOL_ADDRESSES_PROVIDER',
                '0x7535d246': 'POOL',
            }
            if data[:10] in targets:
                result = abi(int(self.addresses[targets[data[:10]]], 16))
            else:
                weth = data[-40:] == ASSETS['WETH'][0][2:]
                # Intentionally synthetic, not claimed as live protocol values.
                result = abi(18 if weth else 6, 7500, 8000, 10500, 1000, 1, 1, 0, 1, 0)
        result = self.overrides.get(params[0]['data'] if method == 'eth_call' else method, result)
        return Response({'jsonrpc': '2.0', 'id': json['id'], 'result': result})


@pytest.fixture
def rpc(addresses):
    return RPC(addresses)


def test_verified_function_selectors():
    # Keccak-256 hashes checked using Ethereum web3_sha3 on 2026-10-10.
    assert SELECTORS == {
        'getPool()': '0x026b1d5f', 'getPoolDataProvider()': '0xe860accb',
        'ADDRESSES_PROVIDER()': '0x0542975c', 'POOL()': '0x7535d246',
        'getReserveConfigurationData(address)': '0x3e150141',
    }


def test_same_block_calls_and_provenance(addresses, rpc):
    result = fetch_reserve_configuration(addresses, rpc, 'https://user:secret@rpc.example/key?token=secret')
    assert result['block'] == {'number': 291, 'hash': '0x' + 'ab' * 32, 'timestamp': 1694498816}
    assert result['reserves']['WETH']['decimals'] == 18
    assert result['reserves']['USDC']['decimals'] == 6
    assert len(result['calls']) == 6
    calls = [request for _, request, _ in rpc.requests if request['method'] == 'eth_call']
    assert all(request['params'][1] == '0x123' for request in calls)
    assert calls[-2]['params'][0]['data'] == '0x3e150141' + ASSETS['WETH'][0][2:].zfill(64)
    assert calls[-1]['params'][0]['data'] == '0x3e150141' + ASSETS['USDC'][0][2:].zfill(64)
    assert 'secret' not in json.dumps(result)
    assert result['rpc_host'] == 'rpc.example'
    assert rpc.headers == 2
    assert all(timeout == 12 for _, _, timeout in rpc.requests)


def test_configurable_endpoint_and_default(addresses, rpc, monkeypatch):
    monkeypatch.setenv('ETH_RPC_URL', 'https://configured.example')
    fetch_reserve_configuration(addresses, rpc)
    assert {url for url, _, _ in rpc.requests} == {'https://configured.example'}
    monkeypatch.delenv('ETH_RPC_URL')
    rpc.requests.clear()
    fetch_reserve_configuration(addresses, rpc)
    assert {url for url, _, _ in rpc.requests} == {DEFAULT_ETH_RPC_URL}


@pytest.mark.parametrize('endpoint', ['', 'ftp://example.org', 'https://[bad', 'https://rpc.example:bad'])
def test_invalid_rpc_endpoint(addresses, rpc, endpoint):
    with pytest.raises(SourceError):
        fetch_reserve_configuration(addresses, rpc, endpoint)
    assert not rpc.requests


@pytest.mark.parametrize('raw', ['0x', '0x12', abi(*range(9)), abi(*range(11)), '0x' + 'z' * 640, None])
def test_bad_abi_is_rejected(raw):
    with pytest.raises(SourceError):
        parse_reserve_configuration(raw, 'WETH')


@pytest.mark.parametrize('index,value', [(0, 6), (1, 8001), (2, 10001), (3, 9999),
                                         (3, 65536), (4, 10001), (5, 0), (6, 2)])
def test_invalid_configuration_is_rejected(index, value):
    values = [18, 7500, 8000, 10500, 1000, 1, 1, 0, 1, 0]
    values[index] = value
    with pytest.raises(SourceError):
        parse_reserve_configuration(abi(*values), 'WETH')


def test_disabled_collateral_and_inactive_frozen_are_observations():
    result = parse_reserve_configuration(abi(18, 0, 0, 0, 1000, 0, 0, 0, 0, 1), 'WETH')
    assert result['liquidation_threshold_bps'] == 0
    assert not result['is_active']
    assert result['is_frozen']


@pytest.mark.parametrize('method,result', [
    ('eth_chainId', '0xa'), ('eth_chainId', '0x01'), ('eth_getCode', '0x'),
    ('eth_getBlockByNumber', None), ('eth_getBlockByNumber', {}),
    ('0x026b1d5f', abi(0)), ('0x026b1d5f', abi(2 ** 160)),
    ('0xe860accb', abi(123)), ('0x0542975c', abi(123)), ('0x7535d246', abi(123)),
])
def test_invalid_rpc_market_results(addresses, rpc, method, result):
    rpc.overrides[method] = result
    with pytest.raises(SourceError):
        fetch_reserve_configuration(addresses, rpc)


@pytest.mark.parametrize('failure', [requests.Timeout('secret-url'), requests.HTTPError('secret-url'), ValueError('bad json')])
def test_transport_errors_are_safe(addresses, rpc, failure):
    rpc.failure = failure
    with pytest.raises(SourceError) as error:
        fetch_reserve_configuration(addresses, rpc, 'https://rpc.example/secret-url')
    assert 'secret-url' not in str(error.value)


@pytest.mark.parametrize('payload', [{'error': {'code': -32000}}, [],
                                     {'jsonrpc': '2.0', 'id': 99, 'result': '0x1'},
                                     {'jsonrpc': '2.0', 'id': True, 'result': '0x1'}])
def test_rpc_envelope_validation(addresses, rpc, monkeypatch, payload):
    monkeypatch.setattr(rpc, 'post', lambda *args, **kwargs: Response(payload))
    with pytest.raises(SourceError):
        fetch_reserve_configuration(addresses, rpc)


def test_reorg_rejected(addresses, rpc):
    rpc.reorg = True
    with pytest.raises(SourceError, match='block changed'):
        fetch_reserve_configuration(addresses, rpc)


def test_wrong_asset_and_partial_reserve_rejected(addresses, rpc):
    addresses['WETH_UNDERLYING'] = ASSETS['USDC'][0]
    with pytest.raises(SourceError, match='Unexpected Ethereum WETH'):
        fetch_reserve_configuration(addresses, rpc)
    addresses['WETH_UNDERLYING'] = ASSETS['WETH'][0]
    rpc.overrides['0x3e150141' + ASSETS['USDC'][0][2:].zfill(64)] = '0x'
    with pytest.raises(SourceError):
        fetch_market_snapshot(rpc)


def test_enriched_snapshot_roundtrip_and_legacy(rpc):
    snapshot = fetch_market_snapshot(rpc)
    validate_snapshot(json.loads(json.dumps(snapshot)))
    assert len(snapshot['registry_sha256']) == 64
    assert snapshot['mode'] == 'sourced'
    assert len(snapshot['reserve_configuration']['reserves']) == 2
    legacy = copy.deepcopy(snapshot)
    del legacy['reserve_configuration']
    validate_snapshot(legacy)


@pytest.mark.parametrize('field,value', [('liquidation_threshold_bps', 9500), ('is_active', 1)])
def test_saved_values_must_match_raw_evidence(rpc, field, value):
    snapshot = fetch_market_snapshot(rpc)
    snapshot['reserve_configuration']['reserves']['WETH'][field] = value
    with pytest.raises(SourceError):
        validate_snapshot(snapshot)


def test_saved_missing_or_mismatched_evidence(rpc):
    snapshot = fetch_market_snapshot(rpc)
    snapshot['reserve_configuration']['calls'][-1]['block_tag'] = 'latest'
    with pytest.raises(SourceError):
        validate_snapshot(snapshot)
    snapshot['reserve_configuration'] = {}
    with pytest.raises(SourceError):
        validate_snapshot(snapshot)


@pytest.mark.parametrize('price', [float('nan'), float('inf'), True, 0, -1])
def test_invalid_saved_prices(rpc, price):
    snapshot = fetch_market_snapshot(rpc)
    snapshot['prices']['WETH']['usd'] = price
    with pytest.raises(SourceError):
        validate_snapshot(snapshot)


def test_reserve_reads_do_not_verify_graph(rpc):
    from src.dependencies import build_dependency_graph
    graph = build_dependency_graph(fetch_market_snapshot(rpc))
    assert not any(edge['verified_onchain'] for _, _, edge in graph.edges(data=True))


def test_dashboard_demo_sourced_and_failed_refresh(rpc, monkeypatch):
    from streamlit.testing.v1 import AppTest
    import src.sources as sources

    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run(timeout=20)
    assert not app.exception
    assert app.metric[2].value == '1.33'
    assert any('Assumed liquidation threshold' in widget.label for widget in app.slider)
    snapshot = fetch_market_snapshot(rpc)
    # No filesystem writes or live requests: seed a previously retrieved snapshot.
    app.session_state['snapshot'] = snapshot
    app.run(timeout=20)
    assert not app.exception
    assert not any('Assumed liquidation threshold' in widget.label for widget in app.slider)
    assert any('WETH base liquidation threshold' in caption.value for caption in app.caption)
    assert any('Liquidation bonus (%)' in frame.value.columns for frame in app.dataframe)

    def fail():
        raise SourceError('mock RPC unavailable')

    monkeypatch.setattr(sources, 'fetch_market_snapshot', fail)
    app.button[0].click().run(timeout=20)
    assert not app.exception
    assert app.session_state['snapshot'] == snapshot
    assert any('mock RPC unavailable' in error.value for error in app.error)
    next(button for button in app.button if button.label == 'Use illustrative demo').click().run(timeout=20)
    assert not app.exception
    assert app.session_state['snapshot'] == DEMO_SNAPSHOT


def test_dashboard_fetch_save_load_legacy_and_invalid(rpc, monkeypatch, tmp_path):
    from streamlit.testing.v1 import AppTest
    import src.sources as sources

    snapshot = fetch_market_snapshot(rpc)
    monkeypatch.setattr(sources, 'fetch_market_snapshot', lambda: copy.deepcopy(snapshot))
    saved = tmp_path / 'snapshot.json'
    # Redirect only the app's saved-file location; never overwrite project data.
    code = (Path(__file__).resolve().parents[1] / 'app.py').read_text(encoding='utf-8')
    code = code.replace("SAVED = BASE_DIR / 'data' / 'latest_source_snapshot.json'",
                        f'SAVED = Path({str(saved)!r})')
    app = AppTest.from_string(code).run(timeout=20)
    app.button[0].click().run(timeout=20)
    assert not app.exception
    assert json.loads(saved.read_text(encoding='utf-8')) == snapshot
    next(button for button in app.button if button.label == 'Use illustrative demo').click().run(timeout=20)
    next(button for button in app.button if button.label == 'Load saved source snapshot').click().run(timeout=20)
    assert not app.exception
    assert app.session_state['snapshot'] == snapshot
    assert app.metric[2].value == '1.33'

    legacy = copy.deepcopy(snapshot)
    del legacy['reserve_configuration']
    saved.write_text(json.dumps(legacy), encoding='utf-8')
    next(button for button in app.button if button.label == 'Load saved source snapshot').click().run(timeout=20)
    assert not app.exception
    assert any('Assumed liquidation threshold' in widget.label for widget in app.slider)
    saved.write_text(json.dumps({'mode': 'sourced', 'reserve_configuration': {}}), encoding='utf-8')
    next(button for button in app.button if button.label == 'Load saved source snapshot').click().run(timeout=20)
    assert not app.exception
    assert app.session_state['snapshot'] == legacy
    assert app.error
