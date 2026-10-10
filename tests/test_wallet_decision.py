"""Deterministic conditional economics; no quotes, transactions or snapshots."""
import copy
from dataclasses import replace
from decimal import Decimal

import pytest

from src.wallet_decision import WalletDecisionAssumptions, compare_wallet_economics
from src.wallet_dependencies import analyze_wallet_incident
from tests.test_wallet import WalletRPC, fetch
from tests.test_wallet_risk import archived_observation


DEFAULT = WalletDecisionAssumptions(10,100,10000,5,20,5,15)


@pytest.fixture
def analysis():
    observed = fetch(WalletRPC())
    return analyze_wallet_incident(observed,observed['assets'][0]['reserve_address'],shock=-.3)


def test_expected_loss_probability_once_and_trade_notional_arithmetic(analysis):
    original = copy.deepcopy(analysis['financial'])
    result = compare_wallet_economics(analysis,DEFAULT)
    assert result['comparison_available']
    assert result['mark_to_market_downside_usd'] == 3000
    assert result['expected_scenario_downside_usd'] == 300
    assert result['expected_avoided_downside_usd'] == 300
    assert result['hypothetical_cost_usd'] == 45
    assert result['cost_components'] == {'Spread':5,'Slippage':20,'Trading fee':5,'Gas':15}
    assert result['conditional_margin_usd'] == 255
    assert result['breakeven_probability'] == Decimal('.015')
    assert result['required_avoidance_fraction'] == Decimal('.15')
    assert result['maximum_trade_notional_usd'] == 95000
    assert result['current_actionable'] is False and result['recommendation'].startswith('MONITOR')
    assert analysis['financial'] == original


@pytest.mark.parametrize('probability,expected',[ (0,0), (100,3000) ])
def test_probability_zero_and_one(analysis,probability,expected):
    result = compare_wallet_economics(analysis,replace(DEFAULT,probability_pct=probability))
    assert result['comparison_available'] and result['expected_scenario_downside_usd'] == expected
    assert result['expected_avoided_downside_usd'] == expected


def test_assumed_efficacy_scales_benefit_not_scenario_loss(analysis):
    result = compare_wallet_economics(analysis,replace(DEFAULT,avoided_downside_pct=25))
    assert result['expected_scenario_downside_usd'] == 300
    assert result['expected_avoided_downside_usd'] == 75
    assert result['breakeven_probability'] == Decimal('.06')


def test_severe_costs_break_even_outside_probability_range(analysis):
    result = compare_wallet_economics(analysis,replace(DEFAULT,gas_usd=1000000))
    assert result['comparison_available']
    assert not result['breakeven_probability_reachable']
    assert result['breakeven_probability'] > 1
    assert result['conclusion'] == 'ASSUMED COST EXCEEDS EXPECTED AVOIDED DOWNSIDE'
    assert not result['current_actionable']


def test_probability_break_even_is_equality_not_favorable_advice(analysis):
    result = compare_wallet_economics(analysis,replace(DEFAULT,probability_pct=Decimal('1.5')))
    assert result['conclusion'] == 'BREAK-EVEN UNDER ASSUMPTIONS'
    assert result['conditional_margin_usd'] == 0
    assert result['recommendation'] == 'MONITOR / EXECUTION NOT VERIFIED'


def test_debt_repayment_obligation_does_not_become_a_fee(analysis):
    result = compare_wallet_economics(analysis,DEFAULT)
    assert result['debt_repayment_required']
    assert result['pinned_debt_mark_usd'] == Decimal('6000.1')
    assert result['hypothetical_cost_usd'] == 45
    assert any('including tiny balances' in c for c in result['constraints'])
    assert result['execution_status'] == 'UNVERIFIED'
    assert any('repayment route' in c for c in result['constraints'])
    assert any('liquidation mechanics' in c for c in result['constraints'])


def test_negative_equity_does_not_create_negative_cost_or_loss():
    rpc = WalletRPC()
    rpc.debts[1] = 20000*10**6
    observed = fetch(rpc)
    linked = analyze_wallet_incident(observed,observed['assets'][0]['reserve_address'],shock=-.3)
    result = compare_wallet_economics(linked,DEFAULT)
    assert result['comparison_available'] and result['baseline_equity_usd'] < 0
    assert result['stressed_equity_usd'] < 0
    assert result['expected_scenario_downside_usd'] == 300
    assert result['hypothetical_cost_usd'] == 45
    assert any('Non-positive marked equity' in c for c in result['constraints'])
    assert result['current_actionable'] is False


def test_no_debt_does_not_claim_execution_feasible():
    rpc = WalletRPC()
    rpc.debts = [0,0,0]
    rpc.flags = 2
    observed = fetch(rpc)
    linked = analyze_wallet_incident(observed,observed['assets'][0]['reserve_address'],shock=-.3)
    result = compare_wallet_economics(linked,DEFAULT)
    assert result['comparison_available'] and not result['debt_repayment_required']
    assert result['execution_status'] == 'UNVERIFIED' and not result['current_actionable']


def test_favorable_scenario_and_no_avoidance_are_separate():
    observed = fetch(WalletRPC())
    linked = analyze_wallet_incident(observed,observed['assets'][0]['reserve_address'],shock=.1)
    result = compare_wallet_economics(linked,DEFAULT)
    assert result['mark_to_market_downside_usd'] == 0
    assert result['expected_scenario_downside_usd'] == 0
    assert result['favorable_stress_usd'] == 1000 and result['expected_favorable_usd'] == 100
    assert result['breakeven_probability'] is None
    linked = analyze_wallet_incident(observed,observed['assets'][0]['reserve_address'],shock=-.3)
    result = compare_wallet_economics(linked,replace(DEFAULT,avoided_downside_pct=0))
    assert result['expected_scenario_downside_usd'] == 300 and result['expected_avoided_downside_usd'] == 0
    assert result['breakeven_probability'] is None


@pytest.mark.parametrize('field,value',[
    ('probability_pct',-1),('probability_pct',101),('probability_pct','NaN'),
    ('avoided_downside_pct',-1),('avoided_downside_pct',101),
    ('trade_notional_usd',0),('trade_notional_usd',-1),('trade_notional_usd','Infinity'),
    ('spread_bps',-1),('spread_bps',10001),('slippage_bps',None),('fee_bps',True),('gas_usd',-1),
])
def test_invalid_or_missing_assumptions_fail_closed(analysis,field,value):
    result = compare_wallet_economics(analysis,replace(DEFAULT,**{field:value}))
    assert not result['comparison_available'] and result['hypothetical_cost_usd'] is None
    assert result['expected_scenario_downside_usd'] is None
    assert result['recommendation'].startswith('MONITOR')


@pytest.mark.parametrize('incident',['oracle_source_outage','oracle_outage','rpc_outage','unsupported'])
def test_unsupported_incidents_have_no_economic_comparison(incident):
    observed = fetch(WalletRPC())
    linked = analyze_wallet_incident(observed,observed['assets'][0]['reserve_address'],incident,-.3)
    result = compare_wallet_economics(linked,DEFAULT)
    assert not result['comparison_available'] and result['expected_scenario_downside_usd'] is None


def test_incomplete_evidence_has_no_economic_comparison():
    observed = fetch(WalletRPC())
    observed['complete'] = False
    linked = analyze_wallet_incident(observed,observed['assets'][0]['reserve_address'],shock=-.3)
    assert not compare_wallet_economics(linked,DEFAULT)['comparison_available']


def test_historical_comparison_requires_replay_and_never_promotes_actionability():
    observed = archived_observation()
    original = copy.deepcopy(observed)
    asset = observed['assets'][0]['reserve_address']
    linked = analyze_wallet_incident(observed,asset,shock=-.3)
    assert not compare_wallet_economics(linked,DEFAULT)['comparison_available']
    linked = analyze_wallet_incident(observed,asset,shock=-.3,historical_replay=True)
    result = compare_wallet_economics(linked,DEFAULT)
    assert result['comparison_available'] and result['source_label'] == 'HISTORICAL / NOT LIVE'
    assert not result['current_actionable'] and result['recommendation'].startswith('MONITOR')
    assert observed == original


def test_zero_bps_and_gas_cost_no_division_by_zero(analysis):
    result = compare_wallet_economics(analysis,replace(DEFAULT,spread_bps=0,slippage_bps=0,fee_bps=0,gas_usd=0))
    assert result['comparison_available'] and result['hypothetical_cost_usd'] == 0
    assert result['breakeven_probability'] == 0 and result['maximum_trade_notional_usd'] is None


def test_ui_conditional_conclusion_and_debt_constraints(analysis):
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_string('import streamlit as st\nfrom src.wallet_decision_ui import render_wallet_decision\nrender_wallet_decision(st.session_state.analysis)')
    app.session_state.analysis = analysis
    app.run()
    assert not app.exception
    assert any('positive aggregate' in c.value for c in app.caption)
    app.number_input(key='wallet_decision_notional').set_value(10000.0).run()
    assert not app.exception
    assert any('CONDITIONAL COMPARISON' in i.value for i in app.info)
    assert any('EXECUTION NOT VERIFIED' in w.value for w in app.warning)
    assert any('DEBT REPAYMENT CONSTRAINT' in w.value for w in app.warning)
    assert any('1.50%' in c.value for c in app.caption)
    assert any('LIQUIDATION MECHANICS NOT PRICED' in w.value for w in app.warning)


def test_ui_historical_comparison_is_explicitly_retrospective():
    from streamlit.testing.v1 import AppTest
    observed = archived_observation()
    analysis = analyze_wallet_incident(observed,observed['assets'][0]['reserve_address'],shock=-.3,historical_replay=True)
    app = AppTest.from_string('import streamlit as st\nfrom src.wallet_decision_ui import render_wallet_decision\nrender_wallet_decision(st.session_state.analysis)')
    app.session_state.analysis = analysis
    app.run()
    app.number_input(key='wallet_decision_notional').set_value(10000.0).run()
    assert not app.exception
    assert any('HISTORICAL / NOT LIVE' in w.value for w in app.warning)
    assert any('EXECUTION NOT VERIFIED' in w.value for w in app.warning)
