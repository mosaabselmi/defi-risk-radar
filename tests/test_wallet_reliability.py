"""Local transport regressions based on observed provider failures; no network."""
import copy

import pytest
import requests

import src.wallet as wallet
from src.sources import SourceError
from test_wallet import Response, WALLET, abi, fetch, rpc


class HTTPResponse(Response):
    def __init__(self, status, payload=None):
        super().__init__(payload)
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError('sensitive URL or token must not escape')


@pytest.fixture(autouse=True)
def no_retry_wait(monkeypatch):
    monkeypatch.setattr(wallet.time, 'sleep', lambda seconds: None)


def intercept(rpc, monkeypatch, handler):
    original = rpc.post
    def post(url, json, timeout):
        reply = handler(json)
        if reply is not None:
            rpc.requests.append(('POST', url, copy.deepcopy(json), timeout))
            return reply
        return original(url, json, timeout)
    monkeypatch.setattr(rpc, 'post', post)


@pytest.mark.parametrize('status', [400, 413, 415, 422, 500, 200])
def test_explicit_batch_rejection_falls_back_without_changing_block(rpc, monkeypatch, status):
    def reject(payload):
        if isinstance(payload, list):
            return HTTPResponse(status, [
                {'jsonrpc': '2.0', 'id': item['id'], 'error': {
                    'code': 31, 'message': 'Batch of more than 3 requests are not allowed on free plan'}}
                for item in payload])
    intercept(rpc, monkeypatch, reject)
    result = fetch(rpc, block_number=291)
    assert result['complete'] and len(result['enumerated_reserves']) == 3
    assert result['transport']['mode'] == 'sequential fallback'
    assert result['transport']['transient_retries'] == 0
    batches = [payload for method, _, payload, _ in rpc.requests if method == 'POST' and isinstance(payload, list)]
    assert len(batches) == 1  # Do not repeatedly probe an unsupported batch mode.
    for method, _, payload, _ in rpc.requests:
        if method != 'POST':
            continue
        for request in payload if isinstance(payload, list) else [payload]:
            if request['method'] in ('eth_call', 'eth_getCode'):
                assert request['params'][1] == '0x123'
            if request['method'] == 'eth_getBlockByNumber':
                assert request['params'][0] == '0x123'
    assert all(item['params'][1] == '0x123' for item in result['rpc_evidence']
               if item['method'] in ('eth_call', 'eth_getCode'))


def test_actual_drpc_three_request_limit(rpc, monkeypatch):
    def reject(payload):
        if isinstance(payload, list) and len(payload) > 3:
            return HTTPResponse(500, [{'jsonrpc': '2.0', 'id': item['id'],
                                      'error': {'code': 31, 'message': 'Batch of more than 3 requests are not allowed'}}
                                     for item in payload])
    intercept(rpc, monkeypatch, reject)
    assert fetch(rpc)['transport']['mode'] == 'sequential fallback'


def test_batch_level_invalid_request_fallback(rpc, monkeypatch):
    intercept(rpc, monkeypatch, lambda payload: HTTPResponse(200, {
        'jsonrpc': '2.0', 'id': None, 'error': {'code': -32600, 'message': 'Invalid request'}})
        if isinstance(payload, list) else None)
    assert fetch(rpc)['complete']


@pytest.mark.parametrize('status', [403, 429])
def test_access_denial_and_rate_limit_are_not_batch_fallback(rpc, monkeypatch, status):
    intercept(rpc, monkeypatch, lambda payload: HTTPResponse(status, {
        'jsonrpc': '2.0', 'id': None, 'error': {'code': -32602,
        'message': 'Archive requests require a personal token at https://user:secret@rpc.example/private'}}))
    with pytest.raises(wallet.WalletProviderError) as error:
        fetch(rpc)
    assert f'HTTP {status}' in str(error.value)
    assert 'secret' not in str(error.value) and 'private' not in str(error.value)
    if status == 403:
        assert 'archive access' in str(error.value)
    posts = [item for item in rpc.requests if item[0] == 'POST']
    assert len(posts) == (1 if status == 403 else 2)


def test_transient_429_retries_once_then_succeeds(rpc, monkeypatch):
    count = 0
    def once(payload):
        nonlocal count
        count += 1
        if count == 1:
            return HTTPResponse(429)
    intercept(rpc, monkeypatch, once)
    result = fetch(rpc)
    assert result['transport']['transient_retries'] == 1
    assert result['transport']['diagnostics'][0]['kind'] == 'rate_limited'
    posts = [item[2] for item in rpc.requests if item[0] == 'POST']
    assert posts[0] == posts[1]


@pytest.mark.parametrize('failure', [requests.Timeout, requests.ConnectionError])
def test_transient_network_failure_retries_same_read(rpc, monkeypatch, failure):
    original = rpc.post
    calls = []
    def post(url, json, timeout):
        calls.append(copy.deepcopy(json))
        if len(calls) == 1:
            raise failure('sensitive endpoint')
        return original(url, json, timeout)
    monkeypatch.setattr(rpc, 'post', post)
    result = fetch(rpc, block_number=291)
    assert result['transport']['transient_retries'] == 1
    assert calls[0] == calls[1]


def test_permanent_timeout_is_bounded_and_redacted(rpc):
    rpc.failure = requests.Timeout('secret-endpoint')
    with pytest.raises(wallet.WalletProviderError, match='Timeout') as error:
        fetch(rpc)
    assert 'secret' not in str(error.value)
    assert len([item for item in rpc.requests if item[0] == 'POST']) == 2


def test_partial_batch_error_never_triggers_fallback(rpc, monkeypatch):
    def partial(payload):
        if isinstance(payload, list):
            bodies = [rpc.reply(item) for item in payload]
            bodies[-1] = {'jsonrpc': '2.0', 'id': payload[-1]['id'],
                          'error': {'code': 31, 'message': 'Batch limit exceeded'}}
            return HTTPResponse(500, bodies)
    intercept(rpc, monkeypatch, partial)
    with pytest.raises(wallet.WalletProviderError):
        fetch(rpc)
    assert len([item for item in rpc.requests if item[0] == 'POST' and isinstance(item[2], list)]) == 1


def test_sequential_fallback_still_rejects_partial_reserve_data(rpc, monkeypatch):
    intercept(rpc, monkeypatch, lambda payload: HTTPResponse(413) if isinstance(payload, list) else None)
    rpc.overrides['getUserReserveData(address,address)'] = abi(*range(8))
    with pytest.raises(SourceError, match='ABI'):
        fetch(rpc)


def test_global_request_and_time_budget(rpc, monkeypatch):
    monkeypatch.setattr(wallet, 'MAX_HTTP_REQUESTS', 1)
    with pytest.raises(wallet.WalletProviderError, match='budget'):
        fetch(rpc)
    client = wallet._RPC(rpc, 'https://rpc.example')
    client.started -= wallet.MAX_IMPORT_SECONDS + 1
    with pytest.raises(wallet.WalletProviderError, match='budget'):
        client.read('eth_chainId', [])


def test_historical_ui_passes_explicit_block_and_explains_provider_failure(tmp_path, monkeypatch):
    from test_block04 import dashboard, click, rendered
    import src.wallet_ui as ui
    blocks = []
    def fail(address, block_number=None):
        blocks.append(block_number)
        raise wallet.WalletProviderError('Provider requires archive access (HTTP 403, RPC -32602)',
                                         {'kind': 'archive_access_required'})
    monkeypatch.setattr(ui, 'fetch_wallet_position', fail)
    app, saved = dashboard(tmp_path)
    before = copy.deepcopy(app.session_state['snapshot'])
    next(field for field in app.text_input if field.label == 'Ethereum wallet address').set_value(WALLET)
    next(box for box in app.checkbox if box.key == 'wallet_historical').check()
    click(app, 'Inspect Aave wallet')
    assert blocks == [26163470]
    assert 'Provider/network failure' in rendered(app)
    assert 'unknown balances are not zero balances' in rendered(app)
    assert app.session_state['wallet_observation'] is None
    assert app.session_state['snapshot'] == before and not saved.exists()
