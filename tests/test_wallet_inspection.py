from tests.ui_navigation import control, go_page
from tests.ui_navigation import analysis_app, navigate
"""Dependency reachability and explicit incident linkage without financial duplication."""
import copy
import hashlib
from pathlib import Path

import networkx as nx
import pytest
from streamlit.testing.v1 import AppTest

from src.wallet_inspection import inspect_wallet_dependency
from src.wallet_dependencies import build_wallet_graph
from src.wallet_risk import analyze_wallet_stress
from src.wallet_mitigation import analyze_wallet_mitigation
from src.wallet_replay import load_historical_example
from src.wallet_viz import plot_wallet_graph
from tests.test_wallet import fetch, addr
from tests.test_wallet_oracles import SourceRPC
from tests.test_wallet_risk import multiple_collateral, archived_observation


@pytest.fixture
def observed():
    return fetch(SourceRPC())


@pytest.mark.parametrize('prefix', ['exposure', 'reserve', 'token', 'valuation', 'source', 'debt_token'])
def test_inspect_actual_debt_asset_components_and_paths(observed, prefix):
    address = observed['assets'][1]['reserve_address']
    graph = build_wallet_graph(observed)
    result = inspect_wallet_dependency(observed, prefix + ':' + address)
    assert result['available'] and result['economic_impact'] == 'UNQUANTIFIED'
    assert result['component_evidence_status'] == 'VERIFIED ASSOCIATION'
    assert [(r['asset'], r['debt_units']) for r in result['exposures']] == [('USDT', '6000')]
    assert result['exposures'][0]['enabled_collateral_units'] == '0'
    assert all(graph.has_edge(u, v) for path in result['position_paths'] for u, v in zip(path, path[1:]))
    assert result['exposures'][0]['dependency_path'][0] == 'exposure:' + address
    assert result['provenance']['wallet_address'] == observed['wallet_address']
    assert not result['current_actionable']


def test_all_nodes_in_real_capture_can_be_inspected_offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Inspection must not contact RPC')
    monkeypatch.setattr('requests.sessions.Session.request', forbidden)
    observation = load_historical_example()
    graph = build_wallet_graph(observation)
    for component in graph:
        result = inspect_wallet_dependency(observation, component)
        assert result['available'], result
        assert result['provenance']['block'] == observation['block']
        assert result['provenance']['retrieved_at'] == observation['retrieved_at']
        assert result['economic_impact'] == 'UNQUANTIFIED'


def test_shared_source_all_potential_exposures_once_despite_multiple_paths():
    rpc = SourceRPC()
    rpc.sources = [addr(500)] * 3
    observation = fetch(rpc)
    graph = build_wallet_graph(observation)
    source = graph.graph['source_nodes'][rpc.reserves[0]]
    result = inspect_wallet_dependency(observation, source)
    assert result['contract_address'] == addr(500)
    assert len(result['exposures']) == 3
    assert {r['asset'] for r in result['exposures']} == {'WETH', 'USDT', 'GHO'}
    assert len({r['reserve_address'] for r in result['exposures']}) == 3
    assert len(result['position_paths']) == 6  # Membership and direct exposure paths.
    assert len({(e['dependent'], e['dependency']) for e in result['relationships']}) == len(result['relationships'])
    assert len([e for e in result['relationships'] if e['dependency'] == source]) == 3
    assert result['exposures'][2]['debt_units'] == '0.1'


def test_shared_subset_never_inflates_unrelated_asset_exposure():
    rpc = SourceRPC()
    rpc.sources[2] = rpc.sources[1]
    observation = fetch(rpc)
    graph = build_wallet_graph(observation)
    result = inspect_wallet_dependency(observation, graph.graph['source_nodes'][rpc.reserves[1]])
    assert [r['asset'] for r in result['exposures']] == ['USDT', 'GHO']
    assert 'exposure:' + rpc.reserves[0] not in result['highlighted_nodes']


def test_shared_oracle_reaches_multiple_debts_and_collateral_with_modeled_path(observed):
    result = inspect_wallet_dependency(observed, 'oracle')
    assert result['available'] and len(result['exposures']) == 3
    assert result['component_evidence_status'] == 'VERIFIED ASSOCIATION'
    assert result['trace_evidence_levels'] == ['modeled', 'verified']
    assert result['exposures'][0]['enabled_collateral_units'] == '4'
    assert [r['debt_units'] for r in result['exposures']] == ['0', '6000', '0.1']


def test_shared_ethereum_is_modeled_infrastructure_not_verified_causal_damage(observed):
    result = inspect_wallet_dependency(observed, 'ethereum')
    assert result['component_evidence_status'] == 'MODELED'
    assert result['contract_address'] is None and len(result['exposures']) == 3
    assert 'modeled' in result['trace_evidence_levels']
    assert result['economic_impact'] == 'UNQUANTIFIED'
    for e in result['relationships']:
        if e['dependency'] == 'ethereum':
            assert e['evidence_level'] == 'modeled' and e['rpc_reads'] == []


def test_pool_account_scope_is_explicitly_modeled_without_fabricated_reverse_edges(observed):
    graph = build_wallet_graph(observed)
    edges = list(graph.edges)
    result = inspect_wallet_dependency(observed, 'pool')
    assert len(result['exposures']) == 3
    for row in result['exposures']:
        assert row['dependency_path'] is None
        assert 'impact scope is modeled' in row['scope']
        assert not nx.has_path(graph, 'exposure:' + row['reserve_address'], 'pool')
        assert all(graph.has_edge(u, v) for u, v in zip(row['association_path'], row['association_path'][1:]))
    assert list(graph.edges) == edges


def test_position_contents_are_not_reverse_reachability(observed):
    result = inspect_wallet_dependency(observed, 'position')
    assert len(result['exposures']) == 3
    assert all('account contents' in row['scope'] for row in result['exposures'])


@pytest.mark.parametrize('condition', ['unavailable', 'zero', 'contradictory'])
def test_unknown_connections_keep_unknown_status_and_never_verify_health(condition):
    rpc = SourceRPC()
    if condition == 'unavailable':
        rpc.source_failures[0] = {'code': -32601}
    elif condition == 'zero':
        rpc.sources[0] = addr(0)
    observation = fetch(rpc)
    if condition == 'contradictory':
        observation['oracle_sources']['entries'][0]['source_address'] = addr(999)
    result = inspect_wallet_dependency(observation, 'source:' + rpc.reserves[0])
    assert result['available'] and result['component_evidence_status'] == 'UNRESOLVED'
    assert result['contract_address'] is None
    assert [r['asset'] for r in result['exposures']] == ['WETH']
    assert 'unknown' in result['trace_evidence_levels']
    assert result['economic_impact'] == 'UNQUANTIFIED'


def test_verified_relationship_receipts_have_exact_target_calldata_and_block(observed):
    graph = build_wallet_graph(observed)
    for node in graph:
        result = inspect_wallet_dependency(observed, node)
        for edge in result['relationships']:
            if edge['evidence_level'] == 'verified':
                assert edge['rpc_reads']
                assert edge['block_number'] == observed['block']['number']
                assert edge['block_hash'] == observed['block']['hash']
                for read in edge['rpc_reads']:
                    assert read['method'] == 'eth_call' and read['params'][1] == hex(observed['block']['number'])
                    assert read['params'][0]['to'].startswith('0x')
                    assert read['params'][0]['data'].startswith('0x')
            else:
                assert edge['rpc_reads'] == []


@pytest.mark.parametrize('change', [lambda o: o.update(complete=False), lambda o: o['assets'].pop(),
    lambda o: o.update(rpc_evidence=[]), lambda o: o['assets'][0].update(supplied_raw='1'),
    lambda o: o['assets'][0]['provenance'].update(block_hash='0x'+'ff'*32),
    lambda o: o.update(rpc_evidence=[e for e in o['rpc_evidence'] if e['method'] != 'eth_getBlockByNumber']),
    lambda o: next(e for e in o['rpc_evidence'] if e['method'] == 'eth_getBlockByNumber')['result'].update(hash='0x'+'ff'*32),
    lambda o: next(e for e in o['rpc_evidence'] if e['method'] == 'eth_chainId').update(result='0x2')])
def test_incomplete_observations_fail_closed(observed, change):
    change(observed)
    result = inspect_wallet_dependency(observed, 'oracle')
    assert not result['available'] and not result['exposures']
    assert result['economic_impact'] == 'UNQUANTIFIED'


def test_unknown_component_fails_closed(observed):
    result = inspect_wallet_dependency(observed, 'fabricated_external_issuer')
    assert not result['available'] and not result['exposures']


def test_historical_inspection_preserves_original_dates_and_model_opt_in():
    observation = archived_observation()
    original = copy.deepcopy(observation)
    result = inspect_wallet_dependency(observation, 'oracle')
    assert result['available'] and result['provenance']['retrieved_at'] == original['retrieved_at']
    address = observation['assets'][0]['reserve_address']
    assert not analyze_wallet_stress(observation, address, -.3)['coverage']
    assert analyze_wallet_stress(observation, address, -.3, historical_replay=True)['coverage']
    assert observation == original


def test_inspection_does_not_invoke_or_mutate_any_economic_engine(observed, monkeypatch):
    original = copy.deepcopy(observed)
    def forbidden(*args, **kwargs):
        raise AssertionError('Inspection must not simulate a market shock or repayment')
    monkeypatch.setattr('src.wallet_dependencies.analyze_wallet_stress', forbidden)
    monkeypatch.setattr('src.wallet_mitigation.analyze_wallet_stress', forbidden)
    for component in ('oracle', 'ethereum', 'source:' + observed['assets'][1]['reserve_address']):
        result = inspect_wallet_dependency(observed, component)
        assert result['available'] and result['economic_impact'] == 'UNQUANTIFIED'
    assert observed == original


@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_visual_highlights_preserve_topology_arrows_and_evidence(observed, theme):
    graph = build_wallet_graph(observed)
    component = graph.graph['source_nodes'][observed['assets'][1]['reserve_address']]
    inspection = inspect_wallet_dependency(observed, component)
    figure = plot_wallet_graph(graph, component, inspection['highlighted_nodes'], theme=theme)
    assert len([a for a in figure.layout.annotations if a.showarrow]) == graph.number_of_edges()
    assert {(t.meta['source'], t.meta['target']) for t in figure.data if t.meta} == set(graph.edges)
    assert next(t for t in figure.data if t.name == component).line.width == 2.5
    assert graph['reserve:' + observed['assets'][1]['reserve_address']]['valuation:' + observed['assets'][1]['reserve_address']]['evidence_level'] == 'modeled'


APP = str(Path(__file__).resolve().parents[1] / 'app.py')


def _historical_app():
    app = analysis_app(APP).run(timeout=20)
    app.button(key='wallet_load_historical_example').click().run(timeout=20)
    assert not app.exception
    navigate(app, 'Scenarios')
    return app


def test_ui_inspection_does_not_change_market_incident_shock_or_repayment():
    app = _historical_app()
    navigate(app, 'Decision')
    app.number_input(key='wallet_mitigation_funding').set_value(30000.0).run(timeout=20)
    repayment_metrics = {m.label: m.value for m in app.metric}
    navigate(app, 'Scenarios')
    market = (app.selectbox(key='wallet_incident').value, app.selectbox(key='wallet_stress_asset').value,
              app.slider(key='wallet_stress_shock').value)
    before_metrics = {m.label: m.value for m in app.metric}
    observation = copy.deepcopy(app.session_state.wallet_observation)
    address = observation['assets'][1]['reserve_address']
    app.selectbox(key='wallet_dependency_inspection').set_value('source:' + address).run(timeout=20)
    assert not app.exception
    assert market == (app.selectbox(key='wallet_incident').value, app.selectbox(key='wallet_stress_asset').value,
                      app.slider(key='wallet_stress_shock').value)
    assert {m.label: m.value for m in app.metric} == before_metrics
    assert not any(b.key == 'wallet_dependency_apply_market' for b in app.button)  # Debt-only dependency.
    assert any('UNQUANTIFIED' in i.value for i in app.info)
    assert app.session_state.wallet_observation == observation
    navigate(app, 'Dependencies')
    assert any('outside this inspected dependency scope' in c.value for c in app.caption)
    navigate(app, 'Decision')
    assert {m.label: m.value for m in app.metric} == repayment_metrics
    assert app.number_input(key='wallet_mitigation_funding').value == 30000.0


def test_explicit_apply_links_to_existing_market_and_repayment_engines():
    observation = multiple_collateral()
    first, second = [a['reserve_address'] for a in observation['assets'][:2]]
    app = analysis_app(APP)
    app.session_state.wallet_observation = observation
    app.run(timeout=20)
    navigate(app, 'Scenarios')
    app.selectbox(key='wallet_incident').set_value('oracle_source_outage').run(timeout=20)
    app.selectbox(key='wallet_dependency_inspection').set_value('token:' + second).run(timeout=20)
    assert app.selectbox(key='wallet_stress_asset').value == first
    assert app.selectbox(key='wallet_incident').value == 'oracle_source_outage'
    assert not app.metric
    app.button(key='wallet_dependency_apply_market').click().run(timeout=20)
    assert not app.exception
    assert app.selectbox(key='wallet_stress_asset').value == second
    assert app.selectbox(key='wallet_incident').value == 'market_price'
    assert app.slider(key='wallet_stress_shock').value == -30
    stress = analyze_wallet_stress(observation, second, -.3)
    mitigation = analyze_wallet_mitigation(observation, second, -.3, 0, 25)
    assert next(m.value for m in app.metric if m.label == 'Change in marked equity') == f"${stress['equity_change_usd']:,.2f}"
    navigate(app, 'Decision')
    assert next(m.value for m in app.metric if m.label == 'Post-repay HF') == f"{mitigation['post_repayment']['health_factor']:.4f}"


@pytest.mark.parametrize('incident', ['oracle_source_outage', 'oracle_outage', 'rpc_outage'])
def test_ui_unsupported_outages_keep_trace_but_no_financial_metrics(incident):
    app = _historical_app()
    app.selectbox(key='wallet_incident').set_value(incident).run(timeout=20)
    app.selectbox(key='wallet_dependency_inspection').set_value('oracle').run(timeout=20)
    assert not app.exception and not app.metric
    assert app.selectbox(key='wallet_incident').value == incident
    assert any('UNQUANTIFIED' in i.value for i in app.info)
    navigate(app, 'Dependencies')
    assert not app.metric
    assert any('Dependency trace:' in c.value for c in app.caption)
    assert app.get('plotly_chart')


def test_mode_isolation_and_snapshot_fixture_untouched():
    paths = [Path(APP).parent / 'data/latest_source_snapshot.json', Path(APP).parent / 'data/demo/aave_wallet_v1.json']
    before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
    app = _historical_app()
    app.selectbox(key='wallet_dependency_inspection').set_value('ethereum').run(timeout=20)
    control(app, 'application_mode').set_value('SYNTHETIC PLAYGROUND').run(timeout=20)
    assert not app.exception and not any(s.key == 'wallet_dependency_inspection' for s in app.selectbox)
    control(app, 'application_mode').set_value('REAL PORTFOLIO ANALYSIS').run(timeout=20)
    assert not app.exception and app.selectbox(key='wallet_dependency_inspection').value == 'ethereum'
    assert app.checkbox(key='wallet_stress_replay').disabled
    assert [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths] == before
