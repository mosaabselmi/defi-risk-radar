from tests.ui_navigation import control, go_page
from tests.ui_navigation import analysis_app, navigate
"""Actual captured replay, tested offline; never rewrite the bundled fixture."""
import copy
import hashlib
import json
import socket
from pathlib import Path

import pytest
import requests
from streamlit.testing.v1 import AppTest

from src.sources import SourceError
from src import wallet_replay
from src.wallet_dependencies import analyze_wallet_incident, build_wallet_graph
from src.wallet_risk import analyze_wallet_stress


APP = str(Path(__file__).resolve().parents[1] / 'app.py')
BUTTON = 'Load verified historical example — NOT LIVE'
WETH = '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2'


@pytest.fixture
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Historical replay must not access any network')
    monkeypatch.setattr(requests.sessions.Session, 'request', forbidden)
    monkeypatch.setattr('socket.create_connection', forbidden)
    # Windows asyncio builds its internal socketpair over literal loopback.
    # Permit that test-harness plumbing, but reject every external connection.
    for name in ('connect', 'connect_ex'):
        original = getattr(socket.socket, name)
        def guarded(self, address, _original=original):
            if isinstance(address, tuple) and address[0] in ('127.0.0.1', '::1'):
                return _original(self, address)
            return forbidden()
        monkeypatch.setattr(socket.socket, name, guarded)
    monkeypatch.setattr('src.wallet_ui.fetch_wallet_position', forbidden)
    monkeypatch.setattr('src.sources.fetch_market_snapshot', forbidden)


@pytest.fixture
def observation(offline):
    return wallet_replay.load_historical_example()


@pytest.mark.parametrize('method', ['connect', 'connect_ex'])
def test_offline_guard_blocks_external_sockets(offline, method):
    with socket.socket() as connection, pytest.raises(AssertionError, match='must not access'):
        getattr(connection, method)(('203.0.113.1', 443))


def test_offline_capture_digest_provenance_and_full_enumeration(observation):
    raw = wallet_replay.FIXTURE_PATH.read_bytes()
    assert len(raw) == 364678
    assert hashlib.sha256(raw).hexdigest() == wallet_replay.EXPECTED_SHA256
    assert wallet_replay.FIXTURE_PATH.with_suffix('.sha256').read_text().split()[0] == wallet_replay.EXPECTED_SHA256
    assert observation['wallet_address'] == wallet_replay.EXAMPLE_ACCOUNT
    assert observation['block'] == wallet_replay.EXAMPLE_BLOCK
    assert observation['retrieved_at'] == wallet_replay.EXAMPLE_RETRIEVED_AT
    assert observation['rpc_host'] == 'ethereum-rpc.publicnode.com'
    assert len(observation['enumerated_reserves']) == 67
    assert len(observation['rpc_evidence']) == 185
    assert observation['complete'] and observation['emode']['category_id'] == 0
    assert observation['historical'] is False  # Original capture never rewritten.
    assert {a['symbol'] for a in observation['assets']} == {'WETH', 'USDT', 'GHO'}
    assert len(observation['oracle_sources']['header_evidence']) == 2


def test_replay_hf_reconciliation_market_shock_and_non_actionability(observation):
    before = copy.deepcopy(observation)
    result = analyze_wallet_stress(observation, WETH, -.3, historical_replay=True)
    assert result['coverage'] and result['historical_replay']
    assert abs(result['baseline']['health_factor'] - result['reported_health_factor']) <= result['hf_tolerance']
    assert float(result['baseline']['health_factor']) == pytest.approx(1.1949344231)
    assert float(result['stressed']['health_factor']) == pytest.approx(.8364540962)
    assert float(result['equity_change_usd']) == pytest.approx(-46357.79801041)
    assert result['stressed']['debt_usd'] == result['baseline']['debt_usd']
    assert result['stressed']['liquidation_eligible']
    assert result['source_label'] == 'HISTORICAL / NOT LIVE'
    assert not result['current_actionable'] and result['status'].startswith('MONITOR')
    assert observation == before


def test_old_capture_still_requires_explicit_replay(observation):
    from datetime import datetime, timezone
    later = datetime(2026, 10, 12, tzinfo=timezone.utc)
    assert not analyze_wallet_stress(observation, WETH, -.3, now=later)['coverage']
    assert analyze_wallet_stress(observation, WETH, -.3, now=later, historical_replay=True)['coverage']


def test_all_captured_source_addresses_verified_without_feed_health_claim(observation):
    graph = build_wallet_graph(observation)
    mappings = graph.graph['oracle_sources']
    assert {m['source_address'] for m in mappings.values()} == {
        '0x5424384b256154046e9667ddfaaa5e550145215e',
        '0x260326c220e469358846b187ee53328303efe19c',
        '0xd110cac5d8682a3b045d5524a9903e031d70fccd'}
    assert all(m['status'] == 'verified' for m in mappings.values())
    assert observation['oracle_sources']['feed_health_verified'] is False


def test_unknown_optional_source_remains_unknown_and_financials_independent(observation):
    entry = observation['oracle_sources']['entries'][0]
    entry.update(status='unavailable', source_address=None, rpc_evidence=None)
    wallet_replay._validate_observation(observation)
    graph = build_wallet_graph(observation)
    assert graph.graph['oracle_sources'][entry['asset']]['status'] == 'unavailable'
    assert analyze_wallet_stress(observation, WETH, -.3, historical_replay=True)['coverage']


@pytest.mark.parametrize('incident', ['oracle_source_outage', 'oracle_outage', 'rpc_outage'])
def test_replay_outages_never_invent_price_loss(observation, incident):
    analysis = analyze_wallet_incident(observation, WETH, incident, -.3, historical_replay=True)
    assert analysis['financial'] is None and analysis['status'].startswith('MONITOR')


@pytest.mark.parametrize('mutation', ['truncated', 'changed', 'bad_digest', 'missing', 'oversize'])
def test_fixture_integrity_failure_closed(tmp_path, monkeypatch, mutation, offline):
    path = tmp_path / 'capture.json'
    raw = wallet_replay.FIXTURE_PATH.read_bytes()
    if mutation == 'truncated':
        raw = raw[:-10]
    elif mutation == 'changed':
        raw = raw.replace(b'WETH', b'FAKE', 1)
    elif mutation == 'bad_digest':
        monkeypatch.setattr(wallet_replay, 'EXPECTED_SHA256', '0' * 64)
    elif mutation == 'oversize':
        raw = b' ' * (wallet_replay.MAX_FIXTURE_BYTES + 1)
    if mutation != 'missing':
        path.write_bytes(raw)
    monkeypatch.setattr(wallet_replay, 'FIXTURE_PATH', path)
    with pytest.raises(SourceError):
        wallet_replay.load_historical_example()


@pytest.mark.parametrize('mutation', [
    'account', 'block', 'timestamp', 'no_reads', 'missing_read', 'no_code',
    'partial', 'emode', 'wrong_price', 'wrong_balance', 'wrong_symbol',
    'wrong_units', 'source_contradiction', 'source_block', 'chain', 'market',
])
def test_even_digest_matching_unsupported_or_contradictory_capture_fails_closed(
        observation, tmp_path, monkeypatch, mutation):
    if mutation == 'account':
        observation['wallet_address'] = '0x' + '1'*40
    elif mutation == 'block':
        observation['block']['hash'] = '0x' + '1'*64
    elif mutation == 'timestamp':
        observation['retrieved_at'] = '2026-10-10T23:00:00+00:00'
    elif mutation == 'no_reads':
        observation['rpc_evidence'] = []
    elif mutation == 'missing_read':
        observation['rpc_evidence'] = [e for e in observation['rpc_evidence']
                                      if e['method'] != 'eth_getBlockByNumber']
    elif mutation == 'no_code':
        observation['rpc_evidence'] = [e for e in observation['rpc_evidence'] if e['method'] != 'eth_getCode']
    elif mutation == 'partial':
        observation['assets'].pop()
    elif mutation == 'emode':
        observation['emode']['category_id'] = 1
    elif mutation == 'wrong_price':
        observation['assets'][0]['oracle_price_base_raw'] = '1'
    elif mutation == 'wrong_balance':
        observation['assets'][0]['supplied_raw'] = '1'
    elif mutation == 'wrong_symbol':
        observation['assets'][0]['symbol'] = 'FAKE'
    elif mutation == 'wrong_units':
        observation['assets'][0]['supplied_units'] = '1'
    elif mutation == 'source_contradiction':
        observation['oracle_sources']['entries'][0]['source_address'] = '0x' + '1'*40
    elif mutation == 'source_block':
        observation['oracle_sources']['block']['number'] += 1
    elif mutation == 'chain':
        next(e for e in observation['rpc_evidence'] if e['method'] == 'eth_chainId')['result'] = '0x2'
    elif mutation == 'market':
        observation['addresses_provider'] = '0x' + '1'*40
    raw = json.dumps(observation).encode()
    path = tmp_path / 'capture.json'
    path.write_bytes(raw)
    monkeypatch.setattr(wallet_replay, 'FIXTURE_PATH', path)
    monkeypatch.setattr(wallet_replay, 'EXPECTED_SHA256', hashlib.sha256(raw).hexdigest())
    with pytest.raises(SourceError):
        wallet_replay.load_historical_example()


def _load_app():
    app = analysis_app(APP).run(timeout=20)
    assert not app.exception and not app.metric
    app.button(key='wallet_load_historical_example').click().run(timeout=20)
    assert not app.exception
    return app


def test_one_click_offline_ui_has_forced_historical_labels_and_real_risk(offline):
    app = _load_app()
    assert app.button(key='wallet_load_historical_example').label == BUTTON
    assert len(app.get_by_key('wallet_overview').metric) == 4
    assert app.metric[0].value == '1.1949' and app.metric[1].value == '0.8365'
    navigate(app, 'Scenarios')
    assert app.checkbox(key='wallet_stress_replay').value
    assert app.checkbox(key='wallet_stress_replay').disabled
    assert any('HISTORICAL / NOT LIVE' in w.value and 'EXAMPLE ACCOUNT' in w.value for w in app.warning)
    assert any('HIGH MODELED RISK' in e.value for e in app.error)
    assert any('26,165,176' in c.value and wallet_replay.EXAMPLE_RETRIEVED_AT in c.value for c in app.caption)
    navigate(app, 'Evidence')
    assert any('integrity, not independent proof' in c.value for c in app.caption)
    navigate(app, 'Dependencies')
    assert app.get('plotly_chart')
    navigate(app, 'Scenarios')
    app.slider(key='wallet_stress_shock').set_value(-10).run(timeout=20)
    navigate(app, 'Overview')
    assert not app.exception and app.metric[1].value == '1.0754'


def test_offline_conditional_economics_remains_non_actionable(offline):
    app = _load_app()
    navigate(app, 'Decision')
    app.number_input(key='wallet_decision_notional').set_value(100000.0).run(timeout=20)
    assert not app.exception
    assert any('EXECUTION NOT VERIFIED' in w.value for w in app.warning)
    assert any('HISTORICAL' in w.value for w in app.warning)
    assert app.session_state.wallet_observation['retrieved_at'] == wallet_replay.EXAMPLE_RETRIEVED_AT


def test_replay_isolated_from_synthetic_and_never_changes_snapshot(offline):
    snapshot = Path(APP).parent / 'data/latest_source_snapshot.json'
    before = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    app = _load_app()
    captured = copy.deepcopy(app.session_state.wallet_observation)
    control(app, 'application_mode').set_value('SYNTHETIC PLAYGROUND').run(timeout=20)
    assert not app.exception
    assert not any(b.key == 'wallet_load_historical_example' for b in app.button)
    app.number_input(key='synthetic_collateral').set_value(8.0).run(timeout=20)
    control(app, 'application_mode').set_value('REAL PORTFOLIO ANALYSIS').run(timeout=20)
    navigate(app, 'Scenarios')
    assert not app.exception and app.checkbox(key='wallet_stress_replay').disabled
    assert app.session_state.wallet_observation == captured
    assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == before


def test_live_failure_does_not_load_or_retain_replay(offline, monkeypatch):
    app = _load_app()
    def failed(*args, **kwargs):
        raise SourceError('Explicit live read failed')
    monkeypatch.setattr('src.wallet_ui.fetch_wallet_position', failed)
    next(b for b in app.button if b.label == 'Inspect Aave wallet').click().run(timeout=20)
    assert not app.exception and not app.metric
    assert app.session_state.wallet_observation is None
    assert app.session_state.wallet_observation_origin is None
    assert any('Explicit live read failed' in e.value for e in app.error)


def test_explicit_successful_live_import_leaves_forced_example_mode(offline, monkeypatch):
    from tests.test_wallet import WalletRPC, fetch
    fresh = fetch(WalletRPC())
    app = _load_app()
    monkeypatch.setattr('src.wallet_ui.fetch_wallet_position', lambda address: copy.deepcopy(fresh))
    app.text_input(key='wallet_address_input').set_value(fresh['wallet_address'])
    next(b for b in app.button if b.label == 'Inspect Aave wallet').click().run(timeout=20)
    assert not app.exception and len(app.get_by_key('wallet_overview').metric) == 4
    assert app.session_state.wallet_observation_origin is None
    navigate(app, 'Scenarios')
    assert not app.checkbox(key='wallet_stress_replay').value
    assert not app.checkbox(key='wallet_stress_replay').disabled
    assert not any('VERIFIED EXAMPLE ACCOUNT' in w.value for w in app.warning)


def test_corrupt_fixture_ui_never_keeps_previous_wallet(offline, monkeypatch):
    app = _load_app()
    monkeypatch.setattr(wallet_replay, 'EXPECTED_SHA256', '0'*64)
    app.button(key='wallet_load_historical_example').click().run(timeout=20)
    assert not app.exception and not app.metric
    assert app.session_state.wallet_observation is None
    assert any('integrity check failed' in e.value for e in app.error)
