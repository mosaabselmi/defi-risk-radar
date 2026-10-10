import math
import pytest
from src.risk import (Position, stress_position, Scenario, ScenarioType,
                      ExecutionAssumptions, assess_scenario, scenario_impact)


def test_health_factor_and_shock():
    p = Position(4, 6000, 2500, 1.0, .8)
    assert p.health_factor == pytest.approx(4/3)
    stress = stress_position(p, -30)
    assert stress.health_factor == pytest.approx(.93333333333)
    assert stress.health_factor < 1


def test_no_debt():
    assert math.isinf(Position(2, 0, 2500, 1, .8).health_factor)


def test_expected_loss_and_breakeven():
    result = assess_scenario(Position(4, 6000, 2500, 1, .8),
                             Scenario(ScenarioType.WETH_MARKET, -20), 20,
                             ExecutionAssumptions(16000, 10, 30, 10, 10))
    assert result.expected_downside_usd == pytest.approx(400)
    assert result.exit_cost_usd == pytest.approx(90)
    assert result.breakeven_probability == pytest.approx(90/2000)
    assert result.recommendation == 'EXIT'


def test_low_prob_means_stay():
    assert assess_scenario(Position(4, 6000, 2500, 1, .8),
                           Scenario(ScenarioType.WETH_MARKET, -20), 1,
                           ExecutionAssumptions(16000, 10, 30, 10, 10)).recommendation == 'STAY'


def test_zero_loss_breakeven_is_none():
    assert assess_scenario(Position(4, 6000, 2500, 1, .8), Scenario(ScenarioType.WETH_MARKET, 0),
                           25, ExecutionAssumptions(16000, 10, 30, 10, 10)).breakeven_probability is None


def test_invalid_data():
    with pytest.raises(ValueError):
        Position(1, -1, 2000, 1, .8)
    with pytest.raises(ValueError):
        assess_scenario(Position(4, 6000, 2500, 1, .8), Scenario(ScenarioType.WETH_MARKET, -20),
                         101, ExecutionAssumptions(16000, 10, 30, 10, 10))


def test_health_factor_exact_boundary():
    p = Position(4, 8000, 2500, 1.0, .8)
    assert p.health_factor == pytest.approx(1.0)


def test_expected_loss_equals_cost_keeps_stay():
    result = assess_scenario(Position(4, 6000, 2500, 1, .8), Scenario(ScenarioType.WETH_MARKET, -20),
                             10, ExecutionAssumptions(20000, 50, 40, 10, 0))
    assert result.expected_downside_usd == pytest.approx(result.exit_cost_usd)
    assert result.recommendation == 'STAY'


def test_zero_borrowing_does_not_break_stress():
    p = Position(2, 0, 2500, 1, .8)
    assert math.isinf(stress_position(p, -50).health_factor)


def test_decoded_reserve_threshold_scales_basis_points():
    from src.sources import parse_reserve_configuration
    raw = '0x' + ''.join(f'{value:064x}' for value in
                        [18, 7500, 8250, 10500, 1000, 1, 1, 0, 1, 0])
    config = parse_reserve_configuration(raw, 'WETH')
    p = Position(4, 6000, 2500, 1, config['liquidation_threshold_bps'] / 10000)
    assert p.health_factor == pytest.approx(1.375)
    assert stress_position(p, -20).health_factor == pytest.approx(1.1)
