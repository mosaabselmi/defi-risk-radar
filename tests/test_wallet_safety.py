from tests.ui_navigation import control, go_page
from tests.ui_navigation import analysis_app, navigate
"""Deterministic safety search regressions; never network or fixture writes."""
import copy
from decimal import Decimal, ROUND_FLOOR, localcontext
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.wallet_safety import analyze_wallet_safety, MAX_DECLINE
from src.wallet_risk import analyze_wallet_stress
from src.wallet_mitigation import analyze_wallet_mitigation
from src.wallet_replay import load_historical_example
from tests.test_wallet import WalletRPC, fetch
from tests.test_wallet_risk import multiple_collateral, ambiguous_boundary_observation, archived_observation


@pytest.fixture
def observed():
    return fetch(WalletRPC())


def safety(observation, target='1.2', shock='-.3', selected=None, **kwargs):
    return analyze_wallet_safety(observation, selected or observation['assets'][0]['reserve_address'],
                                 shock, target, **kwargs)


def test_distance_matches_single_collateral_boundary_and_preserves_evidence(observed):
    before = copy.deepcopy(observed)
    result = safety(observed)
    assert result['coverage']
    expected = Decimal(1) - Decimal('6000.1') / 8300
    assert float(result['distance']['possible_boundary']) == pytest.approx(float(expected), abs=1e-10)
    assert not result['current_actionable'] and result['execution_status'] == 'UNVERIFIED'
    for entry in result['distance']['by_convention'].values():
        assert entry['lower_hf'] > 1 and entry['upper_hf'] <= 1
        with localcontext() as context:
            context.prec = 120
            assert entry['upper_decline'] - entry['lower_decline'] <= result['distance']['resolution']
    assert observed == before


@pytest.mark.parametrize('target', ['1','1.2','1.5','100'])
def test_minimum_sufficient_repayment_reuses_all_debts_and_engine(observed, target):
    result = safety(observed, target)
    minimum = result['repayment']
    modeled = minimum['result']
    assert result['coverage'] and minimum['target_met']
    post = modeled['post_repayment']
    assert post['health_factor_min'] is None or post['health_factor_min'] >= Decimal(target)
    assert [row['asset'] for row in modeled['debt_obligations']] == ['USDT','GHO']
    assert all(row['repayment_raw'] == int((Decimal(row['debt_raw']) * modeled['repayment_fraction']).to_integral_value(rounding=ROUND_FLOOR))
               for row in modeled['debt_obligations'])
    expected = analyze_wallet_mitigation(observed, result['selected_reserve'], '-.3', 0,
                                         modeled['repayment_fraction']*100)
    assert modeled['post_repayment'] == expected['post_repayment']
    assert modeled['repayment_requirement_usd'] == expected['repayment_requirement_usd']
    assert minimum['lower_bound_hf'] < Decimal(target)
    assert minimum['iterations'] <= 264
    with localcontext() as context:
        context.prec = 120
        assert minimum['upper_fraction'] - minimum['lower_fraction'] == minimum['fraction_resolution']
    assert not modeled['withdrawal_modeled'] and not modeled['current_actionable']


def test_target_already_met_requires_no_external_repayment(observed):
    result = safety(observed, 1.2, shock=0)
    assert result['repayment']['status'] == 'TARGET_ALREADY_MET'
    assert result['repayment']['result']['repayment_requirement_usd'] == 0
    assert result['repayment']['result']['post_repayment']['debt_usd'] == Decimal('6000.1')


@pytest.mark.parametrize('funding', [0, 10000])
def test_funding_does_not_change_required_repayment_or_create_action(observed, funding):
    result = safety(observed, external_funding_usd=funding)
    repayment = result['repayment']['result']
    assert repayment['repayment_funding_sufficient'] == (funding == 10000)
    assert not result['current_actionable']
    assert float(repayment['repayment_requirement_usd']) == pytest.approx(float(Decimal('6000.1') - Decimal(5810)/Decimal('1.2')), abs=1e-6)


def test_zero_debt_no_finite_boundary_or_repayment():
    rpc = WalletRPC()
    rpc.debts = [0,0,0]
    rpc.flags = 2
    result = safety(fetch(rpc))
    assert result['coverage'] and result['distance']['status'] == 'NO_DEBT'
    assert result['distance']['possible_boundary'] is None
    assert result['repayment']['result']['repayment_requirement_usd'] == 0
    assert result['repayment']['result']['post_repayment']['health_factor'] is None


def test_at_hf_one_boundary_is_not_definite_liquidation():
    rpc = WalletRPC()
    rpc.debts = [0,8300*10**6,0]
    rpc.flags = 2 | 4
    result = safety(fetch(rpc), 1, shock=0)
    assert result['coverage'] and result['distance']['possible_boundary'] == 0
    assert result['distance']['baseline_severity'] == 'NO_MODELED_LIQUIDATION_ELIGIBILITY'
    assert result['repayment']['status'] == 'TARGET_ALREADY_MET'


def test_tiny_gho_obligation_remains_in_all_results():
    rpc = WalletRPC()
    rpc.debts = [0,0,1]
    rpc.flags = 2 | 16
    result = safety(fetch(rpc))
    assert result['coverage']
    assert result['repayment']['result']['debt_obligations'][0]['debt_raw'] == 1
    assert result['repayment']['result']['post_repayment']['debt_usd'] == Decimal('1e-8')


@pytest.mark.parametrize('funding', [-1, float('inf'),float('nan'),True,'bad'])
def test_invalid_funding_fail_closed(observed, funding):
    assert not safety(observed, external_funding_usd=funding)['coverage']


@pytest.mark.parametrize('shock', [-1, 'nan',True])
def test_invalid_shocks_fail_closed(observed, shock):
    assert not safety(observed, shock=shock)['coverage']


def test_disabled_collateral_cannot_supply_a_distance_or_repayment(observed):
    result = safety(observed, selected=observed['assets'][1]['reserve_address'])
    assert not result['coverage'] and result['distance'] is None


def test_search_read_coverage_failure_never_returns_partial_results(observed, monkeypatch):
    from src import wallet_safety
    original = wallet_safety.analyze_wallet_stress
    calls = []
    def failing(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs) if len(calls) == 1 else {'coverage':False,'reason':'Search coverage failed'}
    monkeypatch.setattr(wallet_safety, 'analyze_wallet_stress', failing)
    result = safety(observed)
    assert not result['coverage'] and result['distance'] is None and result['repayment'] is None


def test_both_searches_are_bounded(observed, monkeypatch):
    from src import wallet_safety
    stress, repayment = wallet_safety.analyze_wallet_stress, wallet_safety.analyze_wallet_mitigation
    counts = {'stress':0, 'repayment':0}
    def stress_call(*a, **k):
        counts['stress'] += 1
        return stress(*a, **k)
    def repayment_call(*a, **k):
        counts['repayment'] += 1
        return repayment(*a, **k)
    monkeypatch.setattr(wallet_safety, 'analyze_wallet_stress', stress_call)
    monkeypatch.setattr(wallet_safety, 'analyze_wallet_mitigation', repayment_call)
    assert safety(observed)['coverage']
    assert counts['stress'] <= 1 + 128 + 2*40 + 2
    assert counts['repayment'] <= 264 + 3


def test_already_liquidatable_baseline_has_zero_buffer():
    rpc = WalletRPC()
    rpc.debts[1] = 9000 * 10**6
    result = safety(fetch(rpc))
    assert result['coverage'] and result['distance']['possible_boundary'] == 0
    assert result['distance']['definite_boundary'] == 0
    assert result['distance']['baseline_severity'] == 'DEFINITE_LIQUIDATION_ELIGIBILITY'


@pytest.mark.parametrize('convention', ['weighted thresholds','truncated average threshold'])
def test_multiple_collaterals_hold_other_prices_fixed(convention):
    observation = multiple_collateral(convention)
    result = safety(observation)
    assert result['coverage']
    boundary = result['distance']['definite_boundary']
    check = analyze_wallet_stress(observation, result['selected_reserve'], -boundary)
    assert check['stressed']['assets'][1]['price_usd'] == 1
    assert check['stressed']['health_factor'] <= 1
    assert result['repayment']['result']['post_repayment']['collateral_usd'] == check['baseline']['collateral_usd'] - 3000


def test_shocked_asset_is_also_debt_and_no_crossing_is_not_safety_proof():
    observation = multiple_collateral()
    selected = observation['assets'][1]['reserve_address']
    result = safety(observation, selected=selected)
    assert result['coverage'] and result['distance']['status'] == 'NO_CROSSING_FOUND'
    assert result['distance']['possible_boundary'] is None
    assert result['distance']['search_max_decline'] == MAX_DECLINE
    assert 'not a proof' in result['distance']['limitation']
    assert result['repayment']['result']['scenario_full_debt_usd'] == Decimal('4200.1')
    assert result['repayment']['status'] == 'TARGET_ALREADY_MET'


def test_ambiguous_conventions_have_distinct_boundaries_and_all_meet_target():
    result = safety(ambiguous_boundary_observation(), 1)
    assert result['coverage']
    distance = result['distance']
    assert distance['possible_boundary'] < Decimal('.30') < distance['definite_boundary']
    assert len(distance['by_convention']) == 2
    assert distance['boundary_checks']['possible_boundary']['health_factor_min'] <= 1
    assert distance['boundary_checks']['possible_boundary']['health_factor_max'] > 1
    assert distance['boundary_checks']['definite_boundary']['health_factor_max'] <= 1
    post = result['repayment']['result']['post_repayment']
    assert post['health_factor_min'] >= 1 and not post['liquidation_possible']


def test_exact_token_rounding_in_tiny_debt_repayment():
    rpc = WalletRPC()
    rpc.supplied[0] = 2 * 10**18
    rpc.prices = [1, 1, 1]
    rpc.debts = [0, 3000000, 0]
    rpc.flags = 2 | 4
    result = safety(fetch(rpc), 1, shock=0)
    assert result['coverage']
    row = result['repayment']['result']['debt_obligations'][0]
    # Conservative ceil USD rounding requires remaining debt <= one base unit.
    assert row['repayment_raw'] == 2000000 and row['remaining_raw'] == 1000000
    assert result['repayment']['result']['post_repayment']['health_factor_min'] >= 1


@pytest.mark.parametrize('shock', ['-.95','-.999999999999','1'])
def test_severe_and_positive_scenarios_are_supported_without_snapshot_changes(observed, shock):
    original = copy.deepcopy(observed)
    result = safety(observed, shock=shock)
    assert result['coverage']
    assert result['repayment']['result']['post_repayment']['health_factor_min'] is None or result['repayment']['result']['post_repayment']['health_factor_min'] >= Decimal('1.2')
    assert observed == original


@pytest.mark.parametrize('target', [0, .9, 101, -1, float('nan'), float('inf'), True, None, 'bad'])
def test_invalid_targets_fail_closed(observed, target):
    result = safety(observed, target)
    assert not result['coverage'] and result['repayment'] is None and result['distance'] is None


@pytest.mark.parametrize('change', [
    lambda o:o.update(complete=False), lambda o:o.update(rpc_evidence=[]),
    lambda o:o['assets'].pop(), lambda o:o['emode'].update(category_id=1),
    lambda o:o['assets'][0].update(oracle_price_base_raw='0'),
    lambda o:o['account'].update(health_factor_raw='100'),
])
def test_incomplete_or_unsupported_evidence_is_not_quantified(observed, change):
    change(observed)
    assert not safety(observed)['coverage']


@pytest.mark.parametrize('incident', ['oracle_source_outage','oracle_outage','rpc_outage'])
def test_unsupported_incidents_do_not_create_safety_math(observed, incident):
    result = safety(observed, incident=incident)
    assert not result['coverage'] and result['distance'] is None


def test_historical_replay_requires_opt_in_and_retains_provenance():
    observation = archived_observation()
    original = copy.deepcopy(observation)
    assert not safety(observation)['coverage']
    replay = safety(observation, historical_replay=True)
    assert replay['coverage'] and replay['source_label'] == 'HISTORICAL / NOT LIVE'
    assert replay['provenance']['retrieved_at'] == observation['retrieved_at']
    assert not replay['current_actionable'] and observation == original
    assert not safety(observation)['coverage']


def test_genuine_example_offline_results_and_preserved_debt_obligations(monkeypatch):
    import requests
    monkeypatch.setattr(requests, 'get', lambda *a,**k:pytest.fail('No network in replay'))
    monkeypatch.setattr(requests, 'post', lambda *a,**k:pytest.fail('No network in replay'))
    observation = load_historical_example()
    original = copy.deepcopy(observation)
    result = safety(observation, historical_replay=True)
    assert result['coverage'] and not result['current_actionable']
    assert float(result['distance']['possible_boundary']*100) == pytest.approx(16.3133992427)
    modeled = result['repayment']['result']
    assert float(modeled['repayment_requirement_usd']) == pytest.approx(32517.23232103)
    assert [r['asset'] for r in modeled['debt_obligations']] == ['USDT','GHO']
    assert observation == original


def test_real_ui_target_updates_independently_and_remains_historical():
    app = analysis_app(str(Path(__file__).resolve().parents[1]/'app.py')).run(timeout=20)
    app.button(key='wallet_load_historical_example').click().run(timeout=20)
    navigate(app, 'Scenarios')
    assert not app.exception
    original = copy.deepcopy(app.session_state.wallet_observation)
    selected = app.selectbox(key='wallet_stress_asset').value
    shock = app.slider(key='wallet_stress_shock').value
    app.number_input(key='wallet_safety_target').set_value(1.5).run(timeout=20)
    assert not app.exception and app.session_state.wallet_observation == original
    assert app.selectbox(key='wallet_stress_asset').value == selected and app.slider(key='wallet_stress_shock').value == shock
    assert any('HISTORICAL / NOT LIVE' in c.value for c in app.caption)
    assert any('MONITOR / EXECUTION NOT VERIFIED' in c.value for c in app.caption)
    control(app, 'application_mode').set_value('SYNTHETIC PLAYGROUND').run(timeout=20)
    assert not any(n.key=='wallet_safety_target' for n in app.number_input)
