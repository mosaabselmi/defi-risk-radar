"""Economic scenario tests; captured observations and synthetic fixtures, no live writes."""
import copy
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path

import pytest

from src.risk import (Position, Scenario, ScenarioType, ExecutionAssumptions,
                      assess_scenario, scenario_impact, snapshot_quality_issues, stress_position)
from src.sources import DEMO_SNAPSHOT, fetch_market_snapshot
from test_reserve_configuration import rpc, addresses


@pytest.fixture
def position():
    # Captured public observations from 2026-10-10; replay, not a live feed.
    return Position(4, 6000, 2494.0905759149605, .999723919187263, .83)


@pytest.fixture
def execution():
    return ExecutionAssumptions(16000, 10, 20, 5, 10)


def test_recent_price_acceptance(position, execution):
    assert position.health_factor == pytest.approx(1.38, abs=.01)
    decision = assess_scenario(position, Scenario(ScenarioType.WETH_MARKET, -30), 10, execution)
    assert decision.impact.stressed.health_factor == pytest.approx(.97, abs=.01)
    assert decision.impact.price_downside_usd == pytest.approx(2993, abs=1)
    assert decision.impact.liquidation_eligible
    assert decision.impact.liquidation_penalty_usd is None
    assert decision.recommendation == 'EXIT'
    assert 'unquantified' in decision.rationale


@pytest.mark.parametrize('shock,sign', [(-10, 1), (10, -1)])
def test_usdc_debt_sign(position, execution, shock, sign):
    result = assess_scenario(position, Scenario(ScenarioType.USDC_MARKET, shock), 20, execution)
    impact = result.impact
    assert impact.stressed.collateral_usd == position.collateral_usd
    assert impact.pnl_usd == pytest.approx(sign * position.debt_usd * .1)
    assert impact.price_downside_usd >= 0
    assert result.expected_downside_usd >= 0
    if shock < 0:
        assert impact.stressed.health_factor > position.health_factor
        assert result.expected_downside_usd == 0
        assert result.expected_favorable_usd == pytest.approx(impact.pnl_usd * .2)
        assert result.recommendation == 'STAY'
    else:
        assert impact.stressed.health_factor < position.health_factor
        assert result.expected_downside_usd == pytest.approx(-impact.pnl_usd * .2)
        assert result.expected_favorable_usd == 0


@pytest.mark.parametrize('kind', [ScenarioType.WETH_OUTAGE, ScenarioType.USDC_OUTAGE])
def test_oracle_outages_do_not_invent_damage(position, execution, kind):
    result = assess_scenario(position, Scenario(kind), 50, execution)
    assert result.recommendation == 'MONITOR'
    assert result.impact.stressed == position
    assert result.impact.pnl_usd is None
    assert result.impact.price_downside_usd is None
    assert result.impact.liquidation_penalty_usd is None
    assert result.expected_downside_usd is None
    assert result.expected_favorable_usd is None
    with pytest.raises(ValueError):
        Scenario(kind, -30)


def test_only_target_asset_is_shocked(position):
    weth = scenario_impact(position, Scenario(ScenarioType.WETH_MARKET, -10))
    usdc = scenario_impact(position, Scenario(ScenarioType.USDC_MARKET, -10))
    assert weth.stressed.debt_usd == position.debt_usd
    assert weth.pnl_usd == pytest.approx(-position.collateral_usd * .1)
    assert usdc.stressed.collateral_usd == position.collateral_usd
    assert usdc.pnl_usd == pytest.approx(position.debt_usd * .1)


def test_probability_applied_once_and_not_to_exit_cost(position, execution):
    result = assess_scenario(position, Scenario(ScenarioType.WETH_MARKET, -20), 25, execution)
    assert result.expected_downside_usd == pytest.approx(position.collateral_usd * .2 * .25)
    assert result.exit_cost_usd == 66
    zero = assess_scenario(position, Scenario(ScenarioType.WETH_MARKET, -20), 0, execution)
    assert zero.expected_downside_usd == 0
    assert zero.recommendation == 'STAY'
    full = assess_scenario(position, Scenario(ScenarioType.WETH_MARKET, -20), 100, execution)
    assert full.expected_downside_usd == pytest.approx(full.impact.conditional_downside_usd)


def test_execution_components_use_notional_not_equity(execution):
    assert execution.components == {'Spread': 16, 'Slippage': 32, 'Trading fee': 8, 'Gas': 10}
    assert execution.total_usd == 66
    assert ExecutionAssumptions(32000, 10, 20, 5, 10).total_usd == 122


def test_liquidation_bonus_only_no_double_counting(position, execution):
    result = assess_scenario(position, Scenario(ScenarioType.WETH_MARKET, -30), 20, execution,
                             liquidation_bonus=.05, liquidation_fraction=.5)
    price_loss = position.collateral_usd * .3
    bonus_cost = position.debt_usd * .5 * .05
    assert result.impact.liquidation_penalty_usd == pytest.approx(bonus_cost)
    assert result.impact.conditional_downside_usd == pytest.approx(price_loss + bonus_cost)
    assert result.expected_downside_usd == pytest.approx((price_loss + bonus_cost) * .2)
    assert result.impact.stressed.equity_usd == pytest.approx(position.equity_usd - price_loss)
    # Debt repayment principal is offset by debt reduction; it is never added as loss.
    assert result.impact.conditional_downside_usd < price_loss + position.debt_usd * .5


def test_liquidation_fraction_and_collateral_cap(position):
    impact = scenario_impact(position, Scenario(ScenarioType.WETH_MARKET, -90), .05, 1)
    assert impact.liquidation_penalty_usd == pytest.approx(impact.stressed.collateral_usd / 1.05 * .05)
    with pytest.raises(ValueError):
        scenario_impact(position, Scenario(ScenarioType.WETH_MARKET, -30), None, .5)
    assert scenario_impact(position, Scenario(ScenarioType.WETH_MARKET, -10), .05, .5).liquidation_penalty_usd == 0


def test_unquantified_liquidation_prevents_unsupported_stay(position):
    expensive = ExecutionAssumptions(16000, 1000, 1000, 1000, 10)
    result = assess_scenario(position, Scenario(ScenarioType.WETH_MARKET, -30), 10, expensive)
    assert result.recommendation == 'MONITOR'
    modeled = assess_scenario(position, Scenario(ScenarioType.WETH_MARKET, -30), 10, expensive,
                              liquidation_bonus=.05, liquidation_fraction=.5)
    assert modeled.recommendation == 'STAY'


def test_zero_balances_and_nonpositive_equity(execution):
    no_debt = Position(4, 0, 2500, 1, .83)
    result = assess_scenario(no_debt, Scenario(ScenarioType.USDC_MARKET, 10), 10, execution)
    assert math.isinf(result.impact.stressed.health_factor)
    assert not result.impact.liquidation_eligible
    assert result.impact.pnl_usd == 0
    assert result.recommendation == 'STAY'
    for collateral, debt in [(0, 0), (0, 6000), (1, 6000), (4, 10000)]:
        result = assess_scenario(Position(collateral, debt, 2500, 1, .83),
                                 Scenario(ScenarioType.WETH_MARKET, -10), 10, execution)
        assert result.recommendation == 'MONITOR'
    assert Position(0, 6000, 2500, 1, .83).health_factor == 0


@pytest.mark.parametrize('price', [0, -1, 1e-12, 1e-8, float('nan'), float('inf'), True])
def test_invalid_or_near_zero_prices(price):
    with pytest.raises(ValueError):
        Position(4, 6000, price, 1, .83)
    with pytest.raises(ValueError):
        Position(4, 6000, 2500, price, .83)


@pytest.mark.parametrize('value', [float('nan'), float('inf'), True, -1])
def test_invalid_amounts_and_execution_assumptions(value):
    with pytest.raises(ValueError):
        Position(value, 6000, 2500, 1, .83)
    with pytest.raises(ValueError):
        ExecutionAssumptions(16000, 10, value, 5, 10)
    with pytest.raises(ValueError):
        ExecutionAssumptions(value, 10, 20, 5, 10)
    with pytest.raises(ValueError):
        ExecutionAssumptions(16000, 10, 20, 5, value)


@pytest.mark.parametrize('value', [float('nan'), float('inf'), True, -1, 101])
def test_invalid_probability_and_fraction(position, execution, value):
    with pytest.raises(ValueError):
        assess_scenario(position, Scenario(ScenarioType.WETH_MARKET, -10), value, execution)
    with pytest.raises(ValueError):
        scenario_impact(position, Scenario(ScenarioType.WETH_MARKET, -30), .05, value)


@pytest.mark.parametrize('value', [-100, -101, 101, float('nan'), float('inf'), True])
def test_invalid_shocks(value):
    with pytest.raises(ValueError):
        Scenario(ScenarioType.USDC_MARKET, value)


def test_zero_notional_is_critical(position):
    result = assess_scenario(position, Scenario(ScenarioType.WETH_MARKET, -10), 10,
                             ExecutionAssumptions(0, 10, 20, 5, 10))
    assert result.recommendation == 'MONITOR'


@pytest.fixture
def fresh_snapshot(rpc):
    snapshot = fetch_market_snapshot(rpc)
    now = datetime.now(timezone.utc)
    snapshot['retrieved_at'] = now.isoformat()
    for price in snapshot['prices'].values():
        price['timestamp'] = now.timestamp()
    snapshot['reserve_configuration']['block']['timestamp'] = int(now.timestamp())
    return snapshot


def test_quality_gate_fresh_demo_and_legacy(fresh_snapshot):
    assert snapshot_quality_issues(fresh_snapshot) == ()
    assert snapshot_quality_issues(DEMO_SNAPSHOT)
    legacy = copy.deepcopy(fresh_snapshot)
    del legacy['reserve_configuration']
    assert any('configuration is missing' in issue for issue in snapshot_quality_issues(legacy))


@pytest.mark.parametrize('tamper', ['stale_price', 'stale_block', 'stale_retrieval', 'future',
                                  'confidence', 'missing_timestamp', 'invalid_date', 'near_zero'])
def test_quality_gate_stale_and_unreliable(fresh_snapshot, execution, tamper):
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    if tamper == 'stale_price':
        fresh_snapshot['prices']['WETH']['timestamp'] = old.timestamp()
    elif tamper == 'stale_block':
        fresh_snapshot['reserve_configuration']['block']['timestamp'] = int(old.timestamp())
    elif tamper == 'stale_retrieval':
        fresh_snapshot['retrieved_at'] = old.isoformat()
    elif tamper == 'future':
        fresh_snapshot['prices']['USDC']['timestamp'] = datetime.now(timezone.utc).timestamp() + 120
    elif tamper == 'confidence':
        fresh_snapshot['prices']['WETH']['confidence'] = .2
    elif tamper == 'missing_timestamp':
        fresh_snapshot['prices']['USDC']['timestamp'] = None
    elif tamper == 'invalid_date':
        fresh_snapshot['retrieved_at'] = 'invalid date'
    else:
        fresh_snapshot['prices']['WETH']['usd'] = 1e-12
    issues = snapshot_quality_issues(fresh_snapshot)
    assert issues
    result = assess_scenario(Position(4, 6000, 2500, 1, .83), Scenario(ScenarioType.WETH_MARKET, -20),
                             10, execution, quality_issues=issues)
    assert result.recommendation == 'MONITOR'


def test_dashboard_switches_scenarios_and_keeps_graph_inspection_separate(fresh_snapshot):
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py'))
    app.session_state['application_mode'] = 'SYNTHETIC PLAYGROUND'
    app.session_state['snapshot'] = fresh_snapshot
    app.run(timeout=20)
    assert not app.exception
    weth_hf = app.metric[3].value
    weth_breakdown = app.table[0].value.copy()
    app.selectbox(key='graph_component').set_value('Ethereum').run(timeout=20)
    assert app.metric[3].value == weth_hf
    assert app.table[0].value.equals(weth_breakdown)
    app.selectbox(key='economic_scenario').set_value(ScenarioType.USDC_MARKET.value).run(timeout=20)
    assert not app.exception
    assert float(app.metric[3].value) > float(app.metric[2].value)
    assert 'SCENARIO RESULT · STAY' in ' '.join(item.value for item in app.markdown)
    app.slider(key='USDC_shock').set_value(10).run(timeout=20)
    assert float(app.metric[3].value) < float(app.metric[2].value)
    # $60 expected downside at 10% is below the $66 assumed unwind cost.
    assert 'SCENARIO RESULT · STAY' in ' '.join(item.value for item in app.markdown)
    app.slider(key='scenario_probability').set_value(20).run(timeout=20)
    assert 'SCENARIO RESULT · EXIT' in ' '.join(item.value for item in app.markdown)
    for kind in (ScenarioType.WETH_OUTAGE, ScenarioType.USDC_OUTAGE):
        app.selectbox(key='economic_scenario').set_value(kind.value).run(timeout=20)
        assert not app.exception
        assert app.metric[3].value == app.metric[2].value
        assert app.metric[4].value == 'N/A'
        assert 'SCENARIO RESULT · MONITOR' in ' '.join(item.value for item in app.markdown)
        assert ' → ' in ' '.join(item.value for item in app.markdown)
        assert not any('market-price shock · %' in slider.label for slider in app.slider)


def test_dashboard_zero_position_stale_and_legacy(fresh_snapshot):
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py'))
    app.session_state['application_mode'] = 'SYNTHETIC PLAYGROUND'
    app.session_state['snapshot'] = fresh_snapshot
    app.run(timeout=20)
    app.number_input[0].set_value(0).run(timeout=20)
    assert not app.exception
    assert 'SCENARIO RESULT · MONITOR' in ' '.join(item.value for item in app.markdown)
    app.number_input[1].set_value(0).run(timeout=20)
    assert not app.exception
    assert app.metric[3].value == '∞'
    legacy = copy.deepcopy(fresh_snapshot)
    del legacy['reserve_configuration']
    app.session_state['snapshot'] = legacy
    app.run(timeout=20)
    assert not app.exception
    assert any('Assumed liquidation threshold' in slider.label for slider in app.slider)
    assert 'SCENARIO RESULT · MONITOR' in ' '.join(item.value for item in app.markdown)


def test_health_curve_uses_selected_asset(position):
    from src.viz import plot_health_curve
    weth = plot_health_curve(position, -10, asset='WETH')
    usdc = plot_health_curve(position, -10, asset='USDC')
    assert weth.data[1].y[0] < position.health_factor
    assert usdc.data[1].y[0] > position.health_factor
    assert len(plot_health_curve(position, 0, asset='USDC', selected_is_scenario=False).data) == 1


def test_near_floor_curve_does_not_fabricate_prices():
    from src.viz import plot_health_curve
    p = Position(4, 1, 1e-7, 1, .8)
    curve = plot_health_curve(p, 0)
    assert curve.data[0].y[0] is None


@pytest.mark.parametrize('price', [0, 1e-12, float('nan'), float('inf')])
def test_dashboard_invalid_prices_monitor_without_crash(price):
    from streamlit.testing.v1 import AppTest
    snapshot = copy.deepcopy(DEMO_SNAPSHOT)
    snapshot['prices']['WETH']['usd'] = price
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py'))
    app.session_state['application_mode'] = 'SYNTHETIC PLAYGROUND'
    app.session_state['snapshot'] = snapshot
    app.run(timeout=20)
    assert not app.exception
    assert any('MONITOR' in error.value for error in app.error)


def test_zero_probability_hf_boundary_and_invalid_rates(execution):
    p = Position(4, 8000, 2500, 1, .8)
    assert not scenario_impact(p, Scenario(ScenarioType.WETH_MARKET, 0)).liquidation_eligible
    assert scenario_impact(p, Scenario(ScenarioType.WETH_MARKET, -1)).liquidation_eligible
    for rate in [-1, 10001, float('nan')]:
        with pytest.raises(ValueError):
            ExecutionAssumptions(1000, rate, 20, 5, 10)
    with pytest.raises(ValueError):
        Position(4, 6000, 2500, 1, float('nan'))
