"""Distinct mechanisms over existing inspection paths; no new loss models."""
import copy
from decimal import Decimal
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.wallet_dependencies import analyze_wallet_incident
from src.wallet_failure import INCIDENTS, analyze_dependency_failure
from src.wallet_inspection import inspect_wallet_dependency
from src.wallet_replay import load_historical_example
from tests.test_wallet import fetch, addr
from tests.test_wallet_oracles import SourceRPC
from tests.test_wallet_risk import multiple_collateral


@pytest.fixture
def observed():
    return fetch(SourceRPC())


def investigate(observation, component='oracle', incident='market_price', asset=None,
                *, scenario='market_price', shock=-.3, replay=False):
    selected = observation['assets'][0]['reserve_address']
    analysis = analyze_wallet_incident(observation, selected, scenario, shock, historical_replay=replay)
    inspection = inspect_wallet_dependency(observation, component)
    result = analyze_dependency_failure(observation, inspection, analysis, incident, asset or selected)
    return result, analysis, inspection


@pytest.mark.parametrize('incident', list(INCIDENTS))
def test_all_incidents_classify_without_mutating_account_graph_or_scenario(observed, incident):
    original = copy.deepcopy(observed)
    result, analysis, inspection = investigate(observed, incident=incident)
    analysis_before = copy.deepcopy(analysis)
    inspection_before = copy.deepcopy(inspection)
    repeated = analyze_dependency_failure(observed, inspection, analysis, incident, observed['assets'][0]['reserve_address'])
    assert result == repeated and result['available']
    assert len(result['exposures']) == 3
    assert result['mechanism'] and result['limitation'] and result['consequence']
    assert set(result['dimensions']) == {'Collateral valuation','Debt valuation','Liquidation eligibility','Ability to exit','Access to funds'}
    assert not result['current_actionable']
    assert observed == original and inspection == inspection_before
    assert analysis['financial'] == analysis_before['financial']
    assert analysis['asset_address'] == analysis_before['asset_address']
    assert list(analysis['graph'].edges(data=True)) == list(analysis_before['graph'].edges(data=True))


@pytest.mark.parametrize('incident', ['oracle_disruption','contract_disruption','liquidity_disruption','network_disruption'])
@pytest.mark.parametrize('component', ['oracle','pool','ethereum'])
def test_technical_failures_never_copy_price_loss_or_call_an_engine(observed, incident, component, monkeypatch):
    result, analysis, inspection = investigate(observed, component, incident)
    assert analysis['financial']['equity_change_usd'] == -3000
    def forbidden(*args, **kwargs):
        raise AssertionError('Failure classification may not invoke a stress model or rebuild a graph')
    monkeypatch.setattr('src.wallet_dependencies.analyze_wallet_stress', forbidden)
    monkeypatch.setattr('src.wallet_inspection.build_wallet_graph', forbidden)
    result = analyze_dependency_failure(observed, inspection, analysis, incident)
    assert result['quantification'] == 'UNQUANTIFIED'
    assert result['financial'] is None and not result['can_apply_market']
    assert '$' not in result['consequence']
    assert 'Liquidation eligibility' in result['dimensions']


@pytest.mark.parametrize('shock', [-.3,.1,0])
def test_supported_price_scenario_reuses_exact_existing_result(observed, shock):
    result, analysis, _ = investigate(observed, shock=shock)
    assert result['quantification'] == 'SUPPORTED SCENARIO'
    assert result['financial'] == analysis['financial']
    assert result['financial'] is not analysis['financial']
    assert result['financial']['stressed']['debt_usd'] == Decimal('6000.1')
    assert 'hypothetical' in result['limitation'] and 'causation' in result['limitation']


def test_debt_stablecoin_depeg_is_explicit_asset_match_and_remains_unquantified(observed):
    debt = observed['assets'][1]['reserve_address']
    result, analysis, _ = investigate(observed, 'token:' + debt, 'stablecoin_depeg', debt)
    assert result['selected_asset'] == debt
    assert [r['asset'] for r in result['exposures']] == ['USDT']
    assert result['quantification'] == 'UNQUANTIFIED' and result['financial'] is None
    assert not result['can_apply_market']
    assert 'lower USD price reduces' in result['dimensions']['Debt valuation']
    assert 'higher price increases' in result['dimensions']['Debt valuation']
    assert 'Token units remain owed' in result['dimensions']['Debt valuation']
    assert analysis['asset_address'] != debt


def test_collateral_depeg_is_user_assumed_peg_and_uses_only_explicit_price_scenario():
    observation = multiple_collateral()
    target = observation['assets'][1]['reserve_address']
    analysis = analyze_wallet_incident(observation, target, 'market_price', -.1)
    inspection = inspect_wallet_dependency(observation, 'token:' + target)
    result = analyze_dependency_failure(observation, inspection, analysis, 'stablecoin_depeg', target)
    assert result['quantification'] == 'SUPPORTED SCENARIO'
    assert result['financial'] == analysis['financial']
    assert 'Peg/stablecoin identity is user-assumed' in result['limitation']


def test_asset_outside_dependency_scope_cannot_borrow_another_assets_loss(observed):
    debt = observed['assets'][1]['reserve_address']
    result, _, _ = investigate(observed, 'token:' + debt)
    assert result['quantification'] == 'UNQUANTIFIED'
    assert result['financial'] is None and result['selected_asset'] is None
    assert 'select an observed asset connected' in result['limitation']


def test_other_collateral_requires_explicit_application_and_copies_no_results():
    observation = multiple_collateral()
    second = observation['assets'][1]['reserve_address']
    result, analysis, _ = investigate(observation, 'token:' + second, asset=second)
    assert result['quantification'] == 'ASSUMPTION-DEPENDENT'
    assert result['financial'] is None and result['can_apply_market']
    assert analysis['asset_address'] != second


@pytest.mark.parametrize('scenario', ['oracle_source_outage','oracle_outage','rpc_outage'])
def test_price_inspection_over_outage_never_converts_it_into_a_crash(observed, scenario):
    result, analysis, _ = investigate(observed, scenario=scenario)
    assert result['quantification'] == 'ASSUMPTION-DEPENDENT'
    assert result['financial'] is None and analysis['financial'] is None
    assert analysis['incident'] == scenario


def test_shared_source_contains_all_debts_and_collateral_once():
    rpc = SourceRPC()
    rpc.sources = [addr(500)] * 3
    observation = fetch(rpc)
    analysis = analyze_wallet_incident(observation, rpc.reserves[0], shock=-.3)
    source = analysis['graph'].graph['source_nodes'][rpc.reserves[0]]
    inspection = inspect_wallet_dependency(observation, source)
    result = analyze_dependency_failure(observation, inspection, analysis, 'oracle_disruption')
    assert {r['asset'] for r in result['exposures']} == {'WETH','USDT','GHO'}
    assert len(result['exposures']) == 3
    assert result['exposures'][2]['debt_units'] == '0.1'
    assert result['contract_address'] == addr(500)
    assert result['quantification'] == 'UNQUANTIFIED'


def test_unknown_source_preserves_unresolved_limitation_and_no_health_claim():
    rpc = SourceRPC()
    rpc.source_failures[0] = {'code': -32601}
    observation = fetch(rpc)
    result, _, _ = investigate(observation, 'source:' + rpc.reserves[0], 'oracle_disruption')
    assert result['available'] and result['evidence_status'] == 'UNRESOLVED'
    assert 'unresolved' in result['limitation']
    assert result['financial'] is None and result['quantification'] == 'UNQUANTIFIED'


def test_source_compromise_does_not_invent_a_custody_relationship(observed):
    address = observed['assets'][0]['reserve_address']
    result, _, _ = investigate(observed, 'source:' + address, 'contract_disruption')
    assert 'No direct custody relationship' in result['consequence']
    assert 'no direct custody' in result['dimensions']['Access to funds']
    assert result['financial'] is None


def test_debt_token_compromise_does_not_claim_an_observed_supply_balance(observed):
    address = observed['assets'][1]['reserve_address']
    result, _, _ = investigate(observed, 'debt_token:' + address, 'contract_disruption')
    assert 'No supplied balance' in result['consequence']
    assert all(row['supplied_units'] == '0' for row in result['exposures'])
    assert result['quantification'] == 'UNQUANTIFIED'


@pytest.mark.parametrize('incident', [None, [], {}, 3])
def test_malformed_incident_types_fail_closed(observed, incident):
    _, analysis, inspection = investigate(observed)
    result = analyze_dependency_failure(observed, inspection, analysis, incident)
    assert not result['available'] and result['financial'] is None


@pytest.mark.parametrize('change', ['incomplete','wrong_account','wrong_block','wrong_asset','wrong_shock','unknown_incident','unknown_dependency','bad_path'])
def test_inconsistent_or_unsupported_inputs_never_quantify(observed, change):
    result, analysis, inspection = investigate(observed)
    incident = 'market_price'
    if change == 'incomplete': observed['complete'] = False
    if change == 'wrong_account': analysis['financial']['provenance']['wallet_address'] = addr(900)
    if change == 'wrong_block': analysis['financial']['provenance']['block']['number'] += 1
    if change == 'wrong_asset': analysis['financial']['selected_reserve'] = observed['assets'][1]['reserve_address']
    if change == 'wrong_shock': analysis['financial']['shock'] = Decimal('-.2')
    if change == 'unknown_incident': incident = 'fabricated_issuer_default'
    if change == 'unknown_dependency': inspection['component'] = 'fabricated_issuer'
    if change == 'bad_path': inspection['exposures'][0]['dependency_path'] = ['fabricated_issuer','oracle']
    result = analyze_dependency_failure(observed, inspection, analysis, incident, observed['assets'][0]['reserve_address'])
    assert result['quantification'] != 'SUPPORTED SCENARIO' and result['financial'] is None


def test_historical_verified_replay_is_supported_but_never_current_actionable():
    observation = load_historical_example()
    original = copy.deepcopy(observation)
    result, analysis, _ = investigate(observation, replay=True)
    assert result['quantification'] == 'SUPPORTED SCENARIO'
    assert result['historical'] and result['source_label'] == 'HISTORICAL / NOT LIVE'
    assert result['provenance']['block'] == observation['block']
    assert not result['current_actionable'] and not result['financial']['current_actionable']
    assert observation == original
    blocked, _, _ = investigate(observation)
    assert blocked['quantification'] == 'UNQUANTIFIED' and blocked['financial'] is None


def test_failed_model_coverage_does_not_hide_trace_or_claim_quantified_loss(observed):
    result, analysis, inspection = investigate(observed)
    analysis['financial']['coverage'] = False
    analysis['financial']['reason'] = 'Unsupported eMode'
    result = analyze_dependency_failure(observed, inspection, analysis, 'market_price', observed['assets'][0]['reserve_address'])
    assert result['available'] and len(result['exposures']) == 3
    assert result['financial'] is None and result['quantification'] == 'UNQUANTIFIED'
    assert 'Unsupported eMode' in result['limitation']


APP = str(Path(__file__).resolve().parents[1] / 'app.py')


@pytest.mark.parametrize('incident', list(INCIDENTS))
def test_ui_failure_selection_leaves_price_mitigation_and_observation_unchanged(incident):
    app = AppTest.from_file(APP).run(timeout=20)
    app.button(key='wallet_load_historical_example').click().run(timeout=20)
    original = copy.deepcopy(app.session_state.wallet_observation)
    inputs = (app.selectbox(key='wallet_incident').value, app.selectbox(key='wallet_stress_asset').value,
              app.slider(key='wallet_stress_shock').value)
    metrics = {m.label:m.value for m in app.metric}
    app.selectbox(key='wallet_failure_incident').set_value(incident).run(timeout=20)
    assert not app.exception
    assert inputs == (app.selectbox(key='wallet_incident').value, app.selectbox(key='wallet_stress_asset').value,
                      app.slider(key='wallet_stress_shock').value)
    assert metrics == {m.label:m.value for m in app.metric}
    assert app.session_state.wallet_observation == original
    assert any('HISTORICAL / NOT LIVE' in c.value for c in app.caption)
    if incident not in ('market_price','stablecoin_depeg'):
        assert any('UNQUANTIFIED' in i.value for i in app.info)
        assert not any(b.key=='wallet_dependency_apply_market' for b in app.button)
    assert all(not e.proto.expanded for e in app.expander)


def test_ui_debt_depeg_choice_never_switches_the_enabled_collateral_scenario():
    app = AppTest.from_file(APP).run(timeout=20)
    app.button(key='wallet_load_historical_example').click().run(timeout=20)
    app.selectbox(key='wallet_dependency_inspection').set_value('oracle').run(timeout=20)
    app.selectbox(key='wallet_failure_incident').set_value('stablecoin_depeg').run(timeout=20)
    address = app.session_state.wallet_observation['assets'][1]['reserve_address']
    selected = app.selectbox(key='wallet_stress_asset').value
    app.selectbox(key='wallet_dependency_market_target').set_value(address).run(timeout=20)
    assert not app.exception
    assert app.selectbox(key='wallet_stress_asset').value == selected
    assert any('UNQUANTIFIED' in i.value for i in app.info)
    assert not any(b.key=='wallet_dependency_apply_market' for b in app.button)
