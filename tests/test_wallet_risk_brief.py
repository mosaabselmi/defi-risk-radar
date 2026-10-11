"""Offline applicability and navigation checks; no live incident claims."""
import copy
import hashlib
from pathlib import Path

import pytest

from src.wallet_dependencies import analyze_wallet_incident, build_wallet_graph
from src.wallet_replay import load_historical_example
from src.wallet_risk_brief import causal_trace, discover_scenarios
from tests.test_wallet import fetch, abi
from tests.test_wallet_oracles import SourceRPC
from tests.test_block14_website import demo_app
from tests.ui_navigation import navigate


def _historical():
    observation = load_historical_example()
    return observation, build_wallet_graph(observation)


def test_fixture_brief_respects_wallet_assets_roles_and_historical_coverage():
    observation, graph = _historical()
    scenarios = discover_scenarios(observation, graph, historical_replay=True)
    assert scenarios[0]['kind'] == 'collateral_price'
    assert scenarios[0]['exposures'][0]['asset'] == 'WETH'
    assert scenarios[0]['exposures'][0]['roles'] == ['collateral']
    assert scenarios[0]['quantification'] == 'SUPPORTED SCENARIO'
    assert scenarios[0]['evidence'] == 'VERIFIED PATH'
    assert {'USDT', 'GHO'} == {s['exposures'][0]['asset'] for s in scenarios if s['kind'] == 'asset_price'}
    assert all(s['exposures'][0]['roles'] == ['debt'] and s['quantification'] == 'SUPPORTED SCENARIO'
               for s in scenarios if s['kind'] == 'asset_price')
    assert {r['asset'] for s in scenarios if s['kind'] == 'aave_oracle' for r in s['exposures']} == {'WETH', 'USDT', 'GHO'}
    source_scenarios = [s for s in scenarios if s['kind'] == 'oracle_source']
    assert {s['component'] for s in source_scenarios} == set(graph.graph['source_nodes'].values())
    for source in source_scenarios:
        assert source['contract_address'] == graph.nodes[source['component']]['contract_address']
        assert {r['address'] for r in source['exposures']} == {
            address for address, node in graph.graph['source_nodes'].items() if node == source['component']}
        assert all(path[0] == 'position' and path[-1] == source['component'] for path in source['paths'])
    assert all('probability' not in s and 'loss_usd' not in s for s in scenarios)
    assert all(s['quantification'] != 'SUPPORTED SCENARIO' for s in scenarios
               if s['kind'] not in ('collateral_price', 'asset_price'))
    assert next(s for s in discover_scenarios(observation, graph) if s['kind'] == 'collateral_price')['quantification'] == 'UNSUPPORTED / UNKNOWN'  # stale by default


def test_verified_shared_source_deduplicates_without_losing_debt_roles():
    rpc = SourceRPC()
    rpc.sources = [rpc.sources[0]] * 3
    observation = fetch(rpc)
    scenarios = discover_scenarios(observation, build_wallet_graph(observation))
    sources = [s for s in scenarios if s['kind'] == 'oracle_source']
    assert len(sources) == 1
    assert {r['asset'] for r in sources[0]['exposures']} == {'WETH', 'USDT', 'GHO'}
    assert all(len(r['roles']) == 1 for r in sources[0]['exposures'])
    assert sources[0]['contract_address'] == rpc.sources[0]
    assert sources[0]['quantification'] == 'QUALITATIVE / OPERATIONAL'


def test_unresolved_source_is_an_unknown_boundary_not_a_verified_contract():
    rpc = SourceRPC()
    rpc.source_failures[1] = abi(0)
    observation = fetch(rpc)
    graph = build_wallet_graph(observation)
    scenarios = discover_scenarios(observation, graph)
    unknown = [s for s in scenarios if s['kind'] == 'unresolved_oracle_source']
    assert len(unknown) == 1
    assert unknown[0]['exposures'][0]['asset'] == 'USDT'
    assert unknown[0]['contract_address'] is None
    assert unknown[0]['evidence'] == 'UNRESOLVED'
    assert unknown[0]['quantification'] == 'UNSUPPORTED / UNKNOWN'
    assert 'UNQUANTIFIED' in ' '.join(causal_trace(unknown[0]))


def test_removed_dependency_removes_only_its_investigation():
    observation, graph = _historical()
    original = discover_scenarios(observation, graph, historical_replay=True)
    source = graph.graph['source_nodes'][observation['assets'][0]['reserve_address']]
    graph.remove_node(source)
    changed = discover_scenarios(observation, graph, historical_replay=True)
    assert len([s for s in original if s['component'] == source]) == 1
    assert not any(s['component'] == source for s in changed)
    assert any(s['kind'] == 'collateral_price' for s in changed)
    graph.remove_node('ethereum')
    assert not any(s['kind'] == 'network' for s in discover_scenarios(observation, graph, historical_replay=True))
    removed_reserve = observation['assets'][1]['reserve_address']
    graph.remove_edge('pool', 'reserve:' + removed_reserve)
    pool = next(s for s in discover_scenarios(observation, graph, historical_replay=True)
                if s['kind'] == 'pool_contract')
    assert removed_reserve not in {r['address'] for r in pool['exposures']}
    altered = copy.deepcopy(observation)
    altered['block']['hash'] = '0x' + 'ff' * 32
    assert discover_scenarios(altered, graph, historical_replay=True) == []


def test_causal_trace_uses_existing_stress_and_never_prices_outage():
    observation, graph = _historical()
    scenarios = discover_scenarios(observation, graph, historical_replay=True)
    weth = scenarios[0]
    financial = analyze_wallet_incident(observation, weth['exposures'][0]['address'],
                                        'market_price', -.3, historical_replay=True)['financial']
    assert float(financial['stressed']['health_factor']) == pytest.approx(.8365, abs=.0001)
    assert float(financial['equity_change_usd']) == pytest.approx(-46357.80, abs=.02)
    trace = ' → '.join(causal_trace(weth, financial))
    assert '0.8365' in trace and '$-46,357.80' in trace and 'definite eligibility' in trace
    positive = analyze_wallet_incident(observation, weth['exposures'][0]['address'],
                                       'market_price', .1, historical_replay=True)['financial']
    positive_trace = ' → '.join(causal_trace(weth, positive))
    assert 'rises' in positive_trace and 'falls' not in positive_trace
    assert 'selected market-price change +10%' in positive_trace
    oracle = next(s for s in scenarios if s['kind'] == 'oracle_source')
    assert 'UNQUANTIFIED' in ' '.join(causal_trace(oracle, financial))
    assert '0.8365' not in ' '.join(causal_trace(oracle, financial))
    assert all(path[0] == 'position' and path[-1] == oracle['component'] for path in oracle['paths'])


def test_offline_demo_brief_handoff_preserves_user_assumptions_and_evidence(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Offline replay tried external network')
    monkeypatch.setattr('requests.sessions.Session.request', forbidden)
    protected = [Path(__file__).resolve().parents[1] / p for p in
                 ('data/latest_source_snapshot.json', 'data/demo/aave_wallet_v1.json')]
    hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in protected]
    app = demo_app()
    assert any('Automatic risk brief' in x.value for x in app.subheader)
    assert any('HISTORICAL / NOT LIVE' in x.value for x in app.warning)
    navigate(app, 'Scenarios')
    app.slider(key='wallet_stress_shock').set_value(-25).run(timeout=20)
    navigate(app, 'Decision')
    app.number_input(key='wallet_decision_probability').set_value(37.0).run(timeout=20)
    app.number_input(key='wallet_decision_notional').set_value(25000.0).run(timeout=20)
    navigate(app, 'Overview')
    featured = next(button for button in app.button if button.label == 'Investigate this scenario')
    featured.click().run(timeout=20)
    assert not app.exception
    assert app.session_state.analysis_section == 'Scenarios'
    assert app.session_state.wallet_stress_shock == -25
    assert app.session_state.wallet_decision_probability == 37.0
    assert app.session_state.wallet_decision_notional == 25000.0
    assert any('Selected investigation: WETH collateral-price decline' in x.value for x in app.markdown)
    navigate(app, 'Decision')
    assert any('CONDITIONAL COMPARISON' in x.value for x in app.info)
    assert any('HISTORICAL / NOT LIVE' in x.value for x in app.warning)
    navigate(app, 'Evidence')
    assert app.session_state.wallet_observation['block']['number'] == 26165176
    assert any('Wallet evidence' in x.label for x in app.expander)
    assert [hashlib.sha256(path.read_bytes()).hexdigest() for path in protected] == hashes


def test_operational_suggestion_is_unquantified_and_keeps_market_inputs(monkeypatch):
    monkeypatch.setattr('requests.sessions.Session.request', lambda *a, **k: pytest.fail('network call'))
    app = demo_app()
    navigate(app, 'Scenarios')
    app.slider(key='wallet_stress_shock').set_value(-21).run(timeout=20)
    navigate(app, 'Overview')
    button = next(b for b in app.button if b.label == 'Investigate Aave Pool contract interruption')
    button.click().run(timeout=20)
    assert not app.exception and app.session_state.analysis_section == 'Scenarios'
    assert app.session_state.wallet_stress_shock == -21
    assert app.session_state.wallet_incident == 'market_price'  # No hidden incident mutation.
    assert not app.metric
    assert any('Economic effect: UNQUANTIFIED' in x.value for x in app.warning)
    navigate(app, 'Decision')
    assert not app.metric and not app.number_input
    assert any('Economic effect: UNQUANTIFIED' in x.value for x in app.warning)
    navigate(app, 'Scenarios')
    app.slider(key='wallet_stress_shock').set_value(-22).run(timeout=20)
    assert app.metric and app.session_state.get('wallet_brief_choice') is None


def test_oracle_source_suggestion_selects_its_actual_node_without_a_price_crash(monkeypatch):
    monkeypatch.setattr('requests.sessions.Session.request', lambda *a, **k: pytest.fail('network call'))
    app = demo_app()
    scenario = next(s for s in discover_scenarios(app.session_state.wallet_observation,
                                                  build_wallet_graph(app.session_state.wallet_observation),
                                                  historical_replay=True) if s['kind'] == 'oracle_source')
    key = 'wallet_brief_' + hashlib.sha256(scenario['id'].encode()).hexdigest()[:12]
    app.button(key=key).click().run(timeout=20)
    assert not app.exception
    assert app.session_state.analysis_section == 'Scenarios'
    assert app.session_state.wallet_incident == 'oracle_source_outage'
    assert app.session_state.wallet_stress_asset == scenario['exposures'][0]['address']
    assert app.session_state.wallet_brief_choice == scenario['id']
    assert app.session_state.wallet_analysis_result['focus'] == scenario['component']
    assert app.session_state.wallet_analysis_result['financial'] is None
    assert not app.metric
    assert any('UNQUANTIFIED' in x.value for x in app.warning)
    navigate(app, 'Decision')
    assert not app.metric and not app.number_input
