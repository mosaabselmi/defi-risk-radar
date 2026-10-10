"""Transparent, scenario-based calculations; NOT a calibrated financial forecast."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Position:
    collateral_units: float
    debt_units: float
    collateral_price_usd: float
    debt_price_usd: float
    liquidation_threshold: float

    def __post_init__(self):
        if self.collateral_units < 0 or self.debt_units < 0:
            raise ValueError('Token quantities must be nonnegative')
        if self.collateral_price_usd <= 0 or self.debt_price_usd <= 0:
            raise ValueError('Prices must be positive')
        if not 0 <= self.liquidation_threshold <= 1:
            raise ValueError('Liquidation threshold must be between 0 and 1')

    @property
    def collateral_usd(self) -> float:
        return self.collateral_units * self.collateral_price_usd

    @property
    def debt_usd(self) -> float:
        return self.debt_units * self.debt_price_usd

    @property
    def equity_usd(self) -> float:
        return self.collateral_usd - self.debt_usd

    @property
    def health_factor(self) -> float:
        return float('inf') if self.debt_usd == 0 else (
            self.collateral_usd * self.liquidation_threshold / self.debt_usd
        )


def stress_position(position: Position, collateral_shock_pct: float = 0,
                    debt_shock_pct: float = 0) -> Position:
    if not -100 <= collateral_shock_pct <= 100 or not -100 <= debt_shock_pct <= 100:
        raise ValueError('Shocks must be between -100 and 100 percent')
    if collateral_shock_pct == -100 or debt_shock_pct == -100:
        # Keep strictly positive prices to avoid zero-value ambiguity in Position.
        # Model a total loss as a limiting tiny positive value.
        epsilon = 1e-9
    else:
        epsilon = 0
    return Position(
        position.collateral_units, position.debt_units,
        max(position.collateral_price_usd * (1 + collateral_shock_pct / 100), epsilon),
        max(position.debt_price_usd * (1 + debt_shock_pct / 100), epsilon),
        position.liquidation_threshold,
    )


@dataclass(frozen=True)
class Decision:
    stay_expected_loss_usd: float
    exit_cost_usd: float
    breakeven_probability: float | None
    recommendation: str


def compare_stay_exit(equity_usd: float, probability_pct: float,
                      severity_pct: float, exit_cost_pct: float,
                      fixed_fee_usd: float = 0) -> Decision:
    """Illustrative exposure-based expected-loss model with an explicit horizon.

    Probability must be chosen for a stated horizon in the UI. Exit cost is
    calculated against positive position equity here as a *proxy*, not a trade quote.
    """
    if equity_usd < 0 or fixed_fee_usd < 0:
        raise ValueError('Exposure and fees must be nonnegative')
    if any(not 0 <= x <= 100 for x in (probability_pct, severity_pct, exit_cost_pct)):
        raise ValueError('Percentages must be within [0, 100]')
    conditional_loss = equity_usd * severity_pct / 100
    stay_loss = probability_pct / 100 * conditional_loss
    exit_cost = equity_usd * exit_cost_pct / 100 + fixed_fee_usd
    breakeven = None if conditional_loss == 0 else exit_cost / conditional_loss
    recommendation = 'EXIT' if stay_loss > exit_cost else 'STAY'
    return Decision(stay_loss, exit_cost, breakeven, recommendation)
