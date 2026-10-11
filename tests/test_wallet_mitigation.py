from tests.ui_navigation import control, go_page
from tests.ui_navigation import analysis_app, navigate
"""External-funded nominal repayment math; no network, writes or executable advice."""
import copy
import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.wallet_mitigation import analyze_wallet_mitigation
from src.wallet_risk import analyze_wallet_stress
from src.wallet_dependencies import analyze_wallet_incident
from src.wallet_decision import WalletDecisionAssumptions, compare_wallet_economics
from src.wallet_replay import load_historical_example
from tests.test_wallet import WalletRPC, fetch
from tests.test_wallet_risk import ambiguous_boundary_observation, archived_observation


@pytest.fixture
def observed():
    return fetch(WalletRPC())


def repay(observation, pct=25, funding=0, shock='-.3', **kwargs):
    return analyze_wallet_mitigation(observation, observation['assets'][0]['reserve_address'],
                                     shock, funding, pct, **kwargs)


def test_complete_debt_by_token_and_full_repayment_are_not_fees(observed):
    before = copy.deepcopy(observed)
    result = repay(observed, 100, '6000.1')
    assert result['coverage'] and result['pinned_full_debt_usd'] == Decimal('6000.1')
    assert result['scenario_full_debt_usd'] == Decimal('6000.1')
    assert result['repayment_requirement_usd'] == Decimal('6000.1')
    assert result['full_funding_sufficient'] and result['repayment_funding_sufficient']
    assert result['all_debt_nominally_repaid']
    assert [(r['asset'], r['debt_units']) for r in result['debt_obligations']] == [('USDT', Decimal(6000)), ('GHO', Decimal('.1'))]
    assert result['post_repayment']['debt_usd'] == 0
    assert result['post_repayment']['health_factor'] is None
    assert not result['post_repayment']['liquidation_possible']
    assert not result['fees_included'] and not result['withdrawal_modeled']
    assert result['execution_status'] == 'UNVERIFIED' and not result['current_actionable']
    assert observed == before


def test_partial_repayment_preserves_collateral_and_all_debt_units(observed):
    result = repay(observed, 25, '1500.025')
    assert result['coverage'] and result['repayment_funding_sufficient']
    assert not result['full_funding_sufficient']
    assert result['repayment_requirement_usd'] == Decimal('1500.025')
    post = result['post_repayment']
    assert post['debt_usd'] == Decimal('4500.075')
    assert post['collateral_usd'] == result['before_repayment']['collateral_usd'] == 7000
    assert post['supplied_usd'] == result['before_repayment']['supplied_usd']
    assert float(post['health_factor']) == pytest.approx(5810 / 4500.075)
    assert not post['liquidation_possible']
    rows = result['debt_obligations']
    assert [r['repayment_units'] for r in rows] == [Decimal(1500), Decimal('.025')]
    assert [r['remaining_units'] for r in rows] == [Decimal(4500), Decimal('.075')]
    assert len(post['assets']) == len(observed['assets'])


@pytest.mark.parametrize('funding,sufficient,shortfall', [('0', False, '1500.025'), ('1500.024', False, '.001'), ('1500.025', True, '0'), ('2000', True, '0')])
def test_external_funding_boundaries_never_use_equity_or_collateral(observed, funding, sufficient, shortfall):
    result = repay(observed, 25, funding)
    assert result['repayment_funding_sufficient'] == sufficient
    assert result['repayment_funding_shortfall_usd'] == Decimal(shortfall)
    assert result['post_repayment']['health_factor'] > 1  # Hypothetical even if unfunded.
    assert result['before_repayment']['collateral_usd'] > result['repayment_requirement_usd']
    assert not result['current_actionable']


def test_zero_repayment_is_exactly_existing_stress_result(observed):
    result = repay(observed, 0)
    stress = analyze_wallet_stress(observed, observed['assets'][0]['reserve_address'], '-.3')
    assert result['post_repayment'] == stress['stressed']
    assert result['repayment_requirement_usd'] == 0
    assert result['post_repayment']['liquidation_eligible']


def test_severe_shock_and_partial_repayment_do_not_claim_liquidation_cured(observed):
    result = repay(observed, 25, 2000, shock='-.95')
    assert result['coverage'] and result['repayment_funding_sufficient']
    assert result['post_repayment']['health_factor'] < 1
    assert result['post_repayment']['liquidation_eligible']
    assert not result['current_actionable']


def test_all_supplies_disabled_collateral_and_enabled_collateral_preserved():
    rpc = WalletRPC()
    rpc.supplied[1] = 1000 * 10**6  # Disabled supply still exists in portfolio.
    rpc.supplied[2] = 100 * 10**18
    rpc.flags |= 2 << 4  # Enable GHO as additional collateral.
    observed = fetch(rpc)
    result = repay(observed, 50, 4000)
    assert result['coverage']
    post = result['post_repayment']
    assert post['collateral_usd'] == 7100
    assert post['supplied_usd'] == 8100
    assert post['debt_usd'] == Decimal('3000.05')
    assert len(post['assets']) == 3
    assert post['assets'][1]['collateral_enabled'] is False
    assert post['assets'][2]['collateral_enabled'] is True
    assert float(post['health_factor']) == pytest.approx(7100*.83/3000.05)


def test_selected_collateral_also_borrowed_uses_scenario_price_consistently():
    rpc = WalletRPC()
    rpc.debts[0] = 10**18
    rpc.flags |= 1
    result = repay(fetch(rpc), 50, 10000)
    assert result['coverage']
    row = result['debt_obligations'][0]
    assert row['asset'] == 'WETH' and row['pinned_debt_usd'] == 2500
    assert row['scenario_debt_usd'] == 1750 and row['repayment_usd'] == 875
    assert result['pinned_full_debt_usd'] == Decimal('8500.1')
    assert result['scenario_full_debt_usd'] == Decimal('7750.1')
    assert result['repayment_requirement_usd'] == Decimal('3875.05')
    assert result['post_repayment']['debt_usd'] == Decimal('3875.05')


def test_zero_debt_is_no_obligation_not_verified_withdrawal():
    rpc = WalletRPC()
    rpc.debts = [0, 0, 0]
    rpc.flags = 2
    result = repay(fetch(rpc), 50, 0)
    assert result['coverage'] and result['debt_obligations'] == []
    assert result['pinned_full_debt_usd'] == result['repayment_requirement_usd'] == 0
    assert result['post_repayment']['health_factor'] is None
    assert result['full_funding_sufficient'] and not result['withdrawal_modeled']
    assert not result['current_actionable']


@pytest.mark.parametrize('pct,remaining', [(0, 1), (50, 1), (99, 1), (100, 0)])
def test_tiny_debt_cannot_disappear_through_fractional_rounding(pct, remaining):
    rpc = WalletRPC()
    rpc.debts = [0, 0, 1]
    rpc.flags = 2 | 16
    result = repay(fetch(rpc), pct, '.00000001')
    assert result['coverage']
    assert len(result['debt_obligations']) == 1
    assert result['debt_obligations'][0]['remaining_raw'] == remaining
    assert result['all_debt_nominally_repaid'] == (remaining == 0)
    assert (result['post_repayment']['health_factor'] is None) == (remaining == 0)


def test_token_base_unit_floor_and_conservative_usd_rounding():
    rpc = WalletRPC()
    rpc.debts[1] += 1
    result = repay(fetch(rpc), 50, 4000)
    usdt = result['debt_obligations'][0]
    assert usdt['repayment_raw'] == 3000 * 10**6
    assert usdt['remaining_raw'] == 3000 * 10**6 + 1
    assert result['post_repayment']['debt_usd'] == Decimal('3000.050001')


def test_separate_conservative_usd_rounding_does_not_drop_dust_or_change_token_conservation():
    rpc = WalletRPC()
    rpc.debts = [0, 0, 101]
    rpc.flags = 2 | 16
    result = repay(fetch(rpc), 50, '.00000001')
    row = result['debt_obligations'][0]
    assert row['repayment_raw'] == 50 and row['remaining_raw'] == 51
    assert row['repayment_raw'] + row['remaining_raw'] == row['debt_raw']
    # Independent conservative USD-base ceilings can sum to one extra base unit.
    # Token quantities are exact; these marks are not transaction fees or quotes.
    assert row['repayment_usd'] == row['remaining_debt_usd'] == Decimal('.00000001')
    assert result['scenario_full_debt_usd'] == Decimal('.00000001')
    assert not result['fees_included']


def test_negative_equity_never_supplies_external_repayment_funding():
    rpc = WalletRPC()
    rpc.debts[1] = 20000 * 10**6
    result = repay(fetch(rpc), 25, 0)
    assert result['coverage'] and result['before_repayment']['equity_usd'] < 0
    assert result['repayment_requirement_usd'] == Decimal('5000.025')
    assert not result['repayment_funding_sufficient']
    assert not result['full_funding_sufficient'] and not result['current_actionable']


def test_hf_ambiguity_range_and_possible_severity_are_preserved():
    result = repay(ambiguous_boundary_observation(), 0, 0)
    post = result['post_repayment']
    assert result['coverage']
    assert post['health_factor_min'] < 1 < post['health_factor_max']
    assert not post['liquidation_eligible'] and post['liquidation_possible']


@pytest.mark.parametrize('field,value', [
    ('funding', -1), ('funding', True), ('funding', 'NaN'), ('funding', 'Infinity'), ('funding', None),
    ('pct', -1), ('pct', 101), ('pct', True), ('pct', 'NaN'), ('pct', 'Infinity'), ('pct', None),
    ('shock', -1), ('shock', True), ('shock', 'NaN'),
])
def test_invalid_inputs_fail_closed(observed, field, value):
    inputs = {'funding': 2000, 'pct': 25, 'shock': '-.3'}
    inputs[field] = value
    result = repay(observed, **inputs)
    assert not result['coverage'] and result['post_repayment'] is None
    assert result['status'] == 'MONITOR / INSUFFICIENT MODEL COVERAGE'
    assert not result['current_actionable']


@pytest.mark.parametrize('mutation', ['partial', 'incomplete', 'missing_reads', 'price', 'emode', 'stale'])
def test_incomplete_unsupported_or_stale_evidence_fail_closed(observed, mutation):
    if mutation == 'partial':
        observed['assets'].pop()
    elif mutation == 'incomplete':
        observed['complete'] = False
    elif mutation == 'missing_reads':
        observed['rpc_evidence'] = []
    elif mutation == 'price':
        observed['assets'][0]['oracle_price_base_raw'] = '0'
    elif mutation == 'emode':
        observed['emode']['category_id'] = 1
    elif mutation == 'stale':
        observed['retrieved_at'] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    result = repay(observed, 100, 1000000)
    assert not result['coverage'] and not result['current_actionable']


@pytest.mark.parametrize('incident', ['oracle_source_outage', 'oracle_outage', 'rpc_outage'])
def test_outages_never_create_quantified_repayment_stress(observed, incident):
    result = repay(observed, incident=incident)
    assert not result['coverage'] and result['post_repayment'] is None


def test_historical_replay_explicit_preserves_provenance():
    observed = archived_observation()
    before = copy.deepcopy(observed)
    assert not repay(observed)['coverage']
    result = repay(observed, historical_replay=True)
    assert result['coverage'] and result['historical_replay']
    assert result['source_label'] == 'HISTORICAL / NOT LIVE'
    assert result['provenance']['retrieved_at'] == observed['retrieved_at']
    assert result['status'] == 'MONITOR / EXECUTION NOT VERIFIED'
    assert not result['current_actionable'] and observed == before


def test_real_capture_repayment_runs_offline_preserving_every_debt(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('No external requests in mitigation')
    monkeypatch.setattr('requests.sessions.Session.request', forbidden)
    observed = load_historical_example()
    result = repay(observed, 25, 30000, historical_replay=True)
    assert result['coverage'] and result['repayment_funding_sufficient']
    assert [r['asset'] for r in result['debt_obligations']] == ['USDT', 'GHO']
    assert float(result['post_repayment']['health_factor']) == pytest.approx(1.1152721282)
    assert not result['full_funding_sufficient'] and not result['current_actionable']


def test_existing_stress_and_conditional_economics_results_do_not_change(observed):
    linked = analyze_wallet_incident(observed, observed['assets'][0]['reserve_address'], shock=-.3)
    assumptions = WalletDecisionAssumptions(10, 100, 10000, 5, 20, 5, 15)
    financial = copy.deepcopy(linked['financial'])
    economics = compare_wallet_economics(linked, assumptions)
    repay(observed, 100, 100000)
    assert compare_wallet_economics(linked, assumptions) == economics
    assert linked['financial'] == financial


def test_compact_ui_funding_hf_and_historical_labels_keep_original_snapshot():
    app_path = Path(__file__).resolve().parents[1] / 'app.py'
    snapshot = app_path.parent / 'data/latest_source_snapshot.json'
    fixture = app_path.parent / 'data/demo/aave_wallet_v1.json'
    before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (snapshot, fixture)]
    app = analysis_app(str(app_path)).run(timeout=20)
    app.button(key='wallet_load_historical_example').click().run(timeout=20)
    navigate(app, 'Decision')
    assert not app.exception
    assert any('ASSUMED FUNDING INSUFFICIENT' in w.value for w in app.warning)
    assert any('HISTORICAL / NOT LIVE' in c.value for c in app.caption)
    assert any('USDT, GHO' in c.value for c in app.caption)
    assert any(m.label == 'Post-repay HF' and m.value == '1.1153' for m in app.metric)
    app.number_input(key='wallet_mitigation_funding').set_value(30000.0).run(timeout=20)
    assert not app.exception
    assert any('MATHEMATICALLY COVERED' in i.value for i in app.info)
    assert any('MONITOR / EXECUTION NOT VERIFIED' in w.value for w in app.warning)
    app.slider(key='wallet_mitigation_pct').set_value(100).run(timeout=20)
    assert any(m.label == 'Post-repay HF' and m.value == 'N/A' for m in app.metric)
    assert any('ASSUMED FUNDING INSUFFICIENT' in w.value for w in app.warning)
    control(app, 'application_mode').set_value('SYNTHETIC PLAYGROUND').run(timeout=20)
    assert not app.exception and not any(s.key == 'wallet_mitigation_pct' for s in app.slider)
    assert not any(n.key == 'wallet_mitigation_funding' for n in app.number_input)
    assert [hashlib.sha256(p.read_bytes()).hexdigest() for p in (snapshot, fixture)] == before
