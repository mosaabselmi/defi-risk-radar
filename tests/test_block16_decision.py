"""Same-horizon STAY/EXIT comparison; execution is never certified."""
from dataclasses import replace
from decimal import Decimal

import pytest
from streamlit.testing.v1 import AppTest

from src.wallet_decision import WalletDecisionAssumptions, compare_wallet_economics
from src.wallet_dependencies import analyze_wallet_incident
from src.wallet_replay import load_historical_example
from tests.test_wallet import WalletRPC, fetch


ASSUMED = WalletDecisionAssumptions(10, 100, 10000, 5, 20, 5, 15)


@pytest.fixture
def analysis():
    observation = fetch(WalletRPC())
    return analyze_wallet_incident(observation, observation['assets'][0]['reserve_address'], shock=-.3)


def test_conditional_exit_and_same_horizon_identity(analysis):
    result = compare_wallet_economics(analysis, ASSUMED)
    assert result['model_preference'] == 'EXIT'
    assert result['stay_modeled_cost_usd'] == 300
    assert result['exit_modeled_cost_usd'] == 45
    assert result['residual_expected_downside_usd'] == 0
    assert result['stay_modeled_cost_usd'] - result['exit_modeled_cost_usd'] == result['conditional_margin_usd']
    assert result['breakeven_probability'] == Decimal('.015')
    assert result['execution_status'] == 'UNVERIFIED' and not result['current_actionable']


def test_partial_avoidance_keeps_residual_loss_in_exit_branch(analysis):
    result = compare_wallet_economics(analysis, replace(ASSUMED, avoided_downside_pct=25))
    assert result['stay_modeled_cost_usd'] == 300
    assert result['residual_expected_downside_usd'] == 225
    assert result['exit_modeled_cost_usd'] == 270
    assert result['model_preference'] == 'EXIT'


def test_stay_equality_and_cost_monotonicity(analysis):
    low = compare_wallet_economics(analysis, ASSUMED)
    equal = compare_wallet_economics(analysis, replace(ASSUMED, probability_pct=Decimal('1.5')))
    high = compare_wallet_economics(analysis, replace(ASSUMED, gas_usd=1000))
    assert equal['model_preference'] == 'INDIFFERENT'
    assert equal['stay_modeled_cost_usd'] == equal['exit_modeled_cost_usd']
    assert high['model_preference'] == 'STAY'
    assert low['exit_modeled_cost_usd'] < high['exit_modeled_cost_usd']
    assert low['conditional_margin_usd'] > high['conditional_margin_usd']
    zero = compare_wallet_economics(analysis, replace(ASSUMED, probability_pct=0))
    assert zero['model_preference'] == 'STAY'


def test_incomplete_trade_cost_has_no_preference_or_fake_quote(analysis):
    result = compare_wallet_economics(analysis, replace(ASSUMED, trade_notional_usd=0))
    assert result['model_preference'] == 'INSUFFICIENT DATA'
    assert not result['comparison_available']
    assert result['hypothetical_cost_usd'] is None
    assert result['execution_status'] == 'UNVERIFIED'


def test_historical_example_stays_retrospective_and_preserves_financial_values():
    observation = load_historical_example()
    address = next(a['reserve_address'] for a in observation['assets'] if a['symbol'] == 'WETH')
    linked = analyze_wallet_incident(observation, address, shock=-.3, historical_replay=True)
    financial = linked['financial']
    result = compare_wallet_economics(linked, replace(ASSUMED, trade_notional_usd=100000))
    assert float(financial['reported_health_factor']) == pytest.approx(1.1949, abs=.0001)
    assert float(financial['stressed']['health_factor']) == pytest.approx(.8365, abs=.0001)
    assert float(financial['equity_change_usd']) == pytest.approx(-46357.80, abs=.02)
    assert result['stay_modeled_cost_usd'] == pytest.approx(Decimal('4635.78'), abs=Decimal('.01'))
    assert result['exit_modeled_cost_usd'] == 315
    assert float(result['breakeven_probability'] * 100) == pytest.approx(.68, abs=.01)
    assert result['model_preference'] == 'EXIT'
    assert result['historical_replay'] and not result['current_actionable']


def test_ui_leads_with_conditional_alternatives_not_executable_advice(analysis):
    app = AppTest.from_string('import streamlit as st\nfrom src.wallet_decision_ui import render_wallet_decision\nrender_wallet_decision(st.session_state.analysis)')
    app.session_state.analysis = analysis
    app.session_state.wallet_decision_notional = 10000.0
    app.run()
    assert not app.exception
    assert [m.label for m in app.metric] == ['STAY · modeled 24h downside', 'EXIT · hypothetical total']
    assert [m.value for m in app.metric] == ['$300.00', '$45.00']
    assert any('MODELED PREFERENCE: EXIT — CONDITIONAL' in w.value for w in app.warning)
    assert any('EXECUTION NOT VERIFIED' in c.value for c in app.caption)
    assert any('DEBT REPAYMENT CONSTRAINT' in w.value for w in app.warning)
    app.number_input(key='wallet_decision_gas').set_value(1000.0).run()
    assert any('MODELED PREFERENCE: STAY — CONDITIONAL' in w.value for w in app.warning)
