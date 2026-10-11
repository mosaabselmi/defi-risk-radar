"""Debt-asset market shocks reuse the complete pinned Aave wallet stress engine."""
from decimal import Decimal
from hashlib import sha256

import pytest

from src.wallet_decision import WalletDecisionAssumptions, compare_wallet_economics
from src.wallet_dependencies import analyze_wallet_incident, build_wallet_graph
from src.wallet_replay import load_historical_example
from src.wallet_risk import analyze_wallet_stress
from src.wallet_risk_brief import causal_trace, discover_scenarios
from tests.test_wallet import WalletRPC, fetch


def _asset(observation, symbol):
    return next(a['reserve_address'] for a in observation['assets'] if a['symbol'] == symbol)


@pytest.mark.parametrize('symbol,shock,expected_debt_change', [
    ('USDT', Decimal('0.1'), Decimal('600')),
    ('USDT', Decimal('-0.1'), Decimal('-600')),
    ('GHO', Decimal('0.1'), Decimal('0.01')),
    ('GHO', Decimal('-0.1'), Decimal('-0.01')),
])
def test_borrowed_asset_repricing_changes_debt_equity_and_hf(symbol, shock, expected_debt_change):
    observation = fetch(WalletRPC())
    result = analyze_wallet_stress(observation, _asset(observation, symbol), shock)
    assert result['coverage'], result.get('reason')
    before, after = result['baseline'], result['stressed']
    assert after['debt_usd'] - before['debt_usd'] == expected_debt_change
    assert result['equity_change_usd'] == -expected_debt_change
    assert after['collateral_usd'] == before['collateral_usd']
    assert (after['health_factor'] < before['health_factor']) == (shock > 0)
    unchanged = {row['asset']: row['debt_usd'] for row in before['assets'] if row['asset'] != symbol}
    assert unchanged == {row['asset']: row['debt_usd'] for row in after['assets'] if row['asset'] != symbol}


def test_same_underlying_supplied_and_borrowed_reprices_both_roles():
    rpc = WalletRPC()
    rpc.supplied[1] = 1000 * 10**6  # Supplied USDT remains collateral-disabled.
    observation = fetch(rpc)
    result = analyze_wallet_stress(observation, rpc.reserves[1], Decimal('0.1'))
    assert result['coverage']
    before, after = result['baseline'], result['stressed']
    assert after['supplied_usd'] - before['supplied_usd'] == 100
    assert after['debt_usd'] - before['debt_usd'] == 600
    assert result['equity_change_usd'] == -500
    assert after['collateral_usd'] == before['collateral_usd']


def test_zero_and_invalid_debt_shocks():
    observation = fetch(WalletRPC())
    address = _asset(observation, 'USDT')
    zero = analyze_wallet_stress(observation, address, 0)
    assert zero['coverage'] and zero['baseline'] == zero['stressed']
    for shock in (-1, -1.01, float('nan')):
        invalid = analyze_wallet_stress(observation, address, shock)
        assert not invalid['coverage'] and invalid['stressed'] is None


def test_debt_appreciation_can_cross_liquidation_boundary_without_assuming_liquidation():
    observation = fetch(WalletRPC())
    result = analyze_wallet_stress(observation, _asset(observation, 'USDT'), Decimal('0.5'))
    assert result['coverage']
    assert result['baseline']['health_factor'] > 1
    assert result['stressed']['health_factor'] < 1
    assert result['stressed']['liquidation_possible']
    assert result['execution_decision'] == 'UNAVAILABLE'


def test_favorable_debt_shock_cannot_trigger_exit_preference():
    observation = fetch(WalletRPC())
    linked = analyze_wallet_incident(observation, _asset(observation, 'USDT'), 'market_price', -0.1)
    assert linked['financial']['coverage']
    comparison = compare_wallet_economics(linked, WalletDecisionAssumptions(100, 100, 10000, 5, 20, 5, 15))
    assert comparison['comparison_available']
    assert comparison['mark_to_market_downside_usd'] == 0
    assert comparison['favorable_stress_usd'] == 600
    assert comparison['expected_avoided_downside_usd'] == 0
    assert comparison['model_preference'] == 'STAY'
    assert not comparison['current_actionable']


def test_historical_debt_brief_and_causal_trace_preserve_collateral_regression():
    observation = load_historical_example()
    graph = build_wallet_graph(observation)
    brief = discover_scenarios(observation, graph, historical_replay=True)
    for symbol in ('USDT', 'GHO'):
        scenario = next(s for s in brief if s['kind'] == 'asset_price' and s['exposures'][0]['asset'] == symbol)
        assert scenario['quantification'] == 'SUPPORTED SCENARIO'
        assert scenario['exposures'][0]['roles'] == ['debt']
        result = analyze_wallet_incident(observation, _asset(observation, symbol), 'market_price', .1,
                                         historical_replay=True)['financial']
        trace = ' '.join(causal_trace(scenario, result))
        assert result['coverage'] and result['historical_replay']
        assert 'total debt' in trace and 'Account HF' in trace
        assert 'UNQUANTIFIED' not in trace
    weth = analyze_wallet_stress(observation, _asset(observation, 'WETH'), -.3, historical_replay=True)
    assert float(weth['baseline']['health_factor']) == pytest.approx(1.1949, abs=.0001)
    assert float(weth['stressed']['health_factor']) == pytest.approx(.8365, abs=.0001)
    assert float(weth['equity_change_usd']) == pytest.approx(-46357.80, abs=.02)


def test_debt_brief_handoff_preserves_shock_and_decision_assumptions(monkeypatch):
    from tests.test_block14_website import demo_app
    from tests.ui_navigation import navigate
    monkeypatch.setattr('requests.sessions.Session.request', lambda *a, **k: pytest.fail('network call'))
    app = demo_app()
    navigate(app, 'Scenarios')
    app.slider(key='wallet_stress_shock').set_value(12).run(timeout=20)
    navigate(app, 'Decision')
    app.number_input(key='wallet_decision_probability').set_value(37.0).run(timeout=20)
    navigate(app, 'Overview')
    observation = app.session_state.wallet_observation
    usdt = next(s for s in discover_scenarios(observation, build_wallet_graph(observation), historical_replay=True)
                if s['kind'] == 'asset_price' and s['exposures'][0]['asset'] == 'USDT')
    key = 'wallet_brief_' + sha256(usdt['id'].encode()).hexdigest()[:12]
    app.button(key=key).click().run(timeout=20)
    assert not app.exception
    assert app.session_state.analysis_section == 'Scenarios'
    assert app.session_state.wallet_stress_asset == _asset(observation, 'USDT')
    assert app.session_state.wallet_stress_shock == 12
    assert app.session_state.wallet_decision_probability == 37.0
    assert app.session_state.wallet_analysis_result['financial']['coverage']
    assert app.session_state.wallet_analysis_result['financial']['historical_replay']
