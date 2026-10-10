"""Demo hardening: synthetic data only; saved project evidence is never written."""
import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

from src.sources import (ASSETS, DEMO_SNAPSHOT, SourceError, fetch_market_snapshot,
                         parse_defillama_prices, save_source_snapshot)
from src.risk import ScenarioType
from test_reserve_configuration import rpc, addresses, Response
from test_block03 import fresh_snapshot


def dashboard(tmp_path, snapshot=None):
    from streamlit.testing.v1 import AppTest
    saved = tmp_path / 'snapshot.json'
    code = (Path(__file__).resolve().parents[1] / 'app.py').read_text(encoding='utf-8')
    code = code.replace("SAVED = BASE_DIR / 'data' / 'latest_source_snapshot.json'",
                        f'SAVED = Path({str(saved)!r})')
    app = AppTest.from_string(code)
    if snapshot is not None:
        app.session_state['snapshot'] = copy.deepcopy(snapshot)
    return app.run(timeout=20), saved


def click(app, label):
    next(button for button in app.button if button.label == label).click().run(timeout=20)
    assert not app.exception


def rendered(app):
    return ' '.join(item.value for kind in (app.markdown, app.caption, app.warning)
                    for item in kind)


@pytest.mark.parametrize('payload', [None, [], 'error', {}, {'coins': []},
                                     {'coins': None}, {'coins': {1: {}}}])
def test_invalid_price_response_shapes(payload, addresses):
    with pytest.raises(SourceError):
        parse_defillama_prices(payload, addresses)


@pytest.mark.parametrize('price', [True, float('nan'), float('inf'), -1, 0])
def test_invalid_remote_prices(price, addresses):
    payload = {'coins': {'ethereum:' + underlying: {'price': price}
                         for underlying, _ in ASSETS.values()}}
    with pytest.raises(SourceError):
        parse_defillama_prices(payload, addresses)


@pytest.mark.parametrize('failure', [requests.Timeout('timeout'),
                                    requests.ConnectionError('offline'),
                                    requests.HTTPError('HTTP 503'),
                                    ValueError('invalid JSON')])
def test_public_source_failures_are_source_errors(rpc, monkeypatch, failure):
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(rpc, 'get', fail)
    with pytest.raises(SourceError):
        fetch_market_snapshot(rpc)


def test_malformed_remote_json_retrieval(rpc, monkeypatch):
    get = rpc.get
    monkeypatch.setattr(rpc, 'get', lambda url, timeout:
                        get(url, timeout) if 'github' in url else Response([]))
    with pytest.raises(SourceError, match='coins object'):
        fetch_market_snapshot(rpc)


@pytest.mark.parametrize('incomplete', ['demo', 'legacy', 'no_sources', 'invalid_price'])
def test_incomplete_refresh_never_replaces_snapshot(tmp_path, fresh_snapshot, incomplete):
    saved = tmp_path / 'snapshot.json'
    save_source_snapshot(fresh_snapshot, saved)
    original = saved.read_bytes()
    candidate = copy.deepcopy(fresh_snapshot)
    if incomplete == 'demo':
        candidate = DEMO_SNAPSHOT
    elif incomplete == 'legacy':
        del candidate['reserve_configuration']
    elif incomplete == 'no_sources':
        del candidate['reserve_configuration']['oracle_sources']
    else:
        candidate['prices']['WETH']['usd'] = float('nan')
    with pytest.raises(SourceError):
        save_source_snapshot(candidate, saved)
    assert saved.read_bytes() == original


def test_atomic_replacement_failure_preserves_evidence(tmp_path, fresh_snapshot, monkeypatch):
    import src.sources as sources
    saved = tmp_path / 'snapshot.json'
    save_source_snapshot(fresh_snapshot, saved)
    original = saved.read_bytes()
    def fail(*args):
        raise OSError('disk unavailable')
    monkeypatch.setattr(sources.os, 'replace', fail)
    with pytest.raises(OSError):
        save_source_snapshot(fresh_snapshot, saved)
    assert saved.read_bytes() == original
    assert list(tmp_path.iterdir()) == [saved]


@pytest.mark.parametrize('failure', ['rpc_timeout', 'rpc_error', 'invalid_rpc', 'incomplete'])
def test_failed_fetch_preserves_file_and_offers_fallback(tmp_path, fresh_snapshot, rpc,
                                                        monkeypatch, failure):
    import src.sources as sources
    app, saved = dashboard(tmp_path, fresh_snapshot)
    save_source_snapshot(fresh_snapshot, saved)
    original = saved.read_bytes()
    if failure == 'rpc_timeout':
        rpc.failure = requests.Timeout('RPC timeout')
    elif failure == 'rpc_error':
        monkeypatch.setattr(rpc, 'post', lambda *args, **kwargs:
                            Response({'jsonrpc': '2.0', 'id': kwargs['json']['id'],
                                      'error': {'code': -32000, 'message': 'unavailable'}}))
    elif failure == 'invalid_rpc':
        monkeypatch.setattr(rpc, 'post', lambda *args, **kwargs: Response([]))
    if failure == 'incomplete':
        candidate = copy.deepcopy(fresh_snapshot)
        del candidate['reserve_configuration']
        monkeypatch.setattr(sources, 'fetch_market_snapshot', lambda: candidate)
    else:
        monkeypatch.setattr(sources, 'fetch_market_snapshot', lambda: fetch_market_snapshot(rpc))
    click(app, '↻  Fetch public data')
    assert saved.read_bytes() == original
    assert app.session_state['snapshot'] == fresh_snapshot
    assert app.error
    assert 'No replacement data was loaded or saved' in rendered(app)
    assert 'MONITOR / INSUFFICIENT CURRENT DATA' in rendered(app)
    click(app, 'Load saved source snapshot')
    assert 'HISTORICAL SNAPSHOT / NOT LIVE' in rendered(app)
    click(app, 'Use illustrative demo')
    assert app.session_state['snapshot'] == DEMO_SNAPSHOT
    assert 'ILLUSTRATIVE MODE' in rendered(app)
    assert 'SCENARIO RESULT · MONITOR' in rendered(app)
    assert saved.read_bytes() == original


@pytest.mark.parametrize('stale', [False, True])
def test_archive_keeps_arithmetic_and_original_evidence(tmp_path, fresh_snapshot, stale):
    snapshot = copy.deepcopy(fresh_snapshot)
    if stale:
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        snapshot['retrieved_at'] = old.isoformat()
        for price in snapshot['prices'].values():
            price['timestamp'] = old.timestamp()
        snapshot['reserve_configuration']['block']['timestamp'] = int(old.timestamp())
    app, saved = dashboard(tmp_path)
    save_source_snapshot(snapshot, saved)
    original = saved.read_bytes()
    app.run(timeout=20)  # File appeared after the initial render; expose its load control.
    click(app, 'Load saved source snapshot')
    app.slider(key='WETH_shock').set_value(-30).run(timeout=20)
    assert not app.exception
    text = rendered(app)
    assert 'HISTORICAL SNAPSHOT / NOT LIVE' in text
    assert snapshot['retrieved_at'] in text
    assert 'Ethereum block 291' in text
    assert 'SCENARIO RESULT · MONITOR' in text
    assert 'MONITOR / INSUFFICIENT CURRENT DATA' in text
    assert 'SCENARIO RESULT · EXIT' not in text
    assert float(app.metric[3].value) < float(app.metric[2].value)
    assert '$3,000.00' in app.table[0].value['USD'].values
    assert app.session_state['snapshot'] == snapshot
    assert saved.read_bytes() == original
    assert any('Verified' in str(frame.value) for frame in app.dataframe)
    if stale:
        assert 'stale or future-dated (1h freshness policy)' in text


def test_successful_fetch_resets_archive_origin(tmp_path, fresh_snapshot, monkeypatch):
    import src.sources as sources
    app, saved = dashboard(tmp_path)
    save_source_snapshot(fresh_snapshot, saved)
    app.run(timeout=20)
    click(app, 'Load saved source snapshot')
    assert app.session_state['snapshot_origin'] == 'archive'
    monkeypatch.setattr(sources, 'fetch_market_snapshot', lambda: copy.deepcopy(fresh_snapshot))
    click(app, '↻  Fetch public data')
    assert app.session_state['snapshot_origin'] == 'fetch'
    assert 'HISTORICAL SNAPSHOT / NOT LIVE' not in rendered(app)
    assert 'SCENARIO RESULT · EXIT' in rendered(app)
    assert json.loads(saved.read_text(encoding='utf-8')) == fresh_snapshot


def test_short_metrics_and_outage_focus_separate_from_inspection(tmp_path, fresh_snapshot):
    app, _ = dashboard(tmp_path, fresh_snapshot)
    assert not app.exception
    assert all(len(metric.label) <= 18 for metric in app.metric)
    app.selectbox(key='graph_component').set_value('WETH Feed').run(timeout=20)
    app.selectbox(key='economic_scenario').set_value(ScenarioType.USDC_MARKET.value).run(timeout=20)
    assert app.selectbox(key='graph_component').value == 'WETH Feed'
    assert 'no oracle malfunction' in rendered(app)
    app.selectbox(key='economic_scenario').set_value(ScenarioType.USDC_OUTAGE.value).run(timeout=20)
    assert not app.exception
    assert app.metric[4].value == 'N/A'
    assert 'Not quantified' in rendered(app)
    assert 'outage scenario focus: USDC Feed' in rendered(app)
    assert 'Economic outage scenario:** USDC Feed' in rendered(app)
    assert app.selectbox(key='graph_component').value == 'WETH Feed'
    assert 'SCENARIO RESULT · MONITOR' in rendered(app)


def test_demo_is_explicit_and_not_live(tmp_path):
    app, saved = dashboard(tmp_path)
    assert not app.exception
    assert 'ILLUSTRATIVE MODE' in rendered(app)
    assert 'MONITOR / INSUFFICIENT CURRENT DATA' in rendered(app)
    assert not saved.exists()
