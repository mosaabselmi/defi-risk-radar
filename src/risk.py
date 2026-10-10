"""Transparent, scenario-based calculations; NOT a calibrated financial forecast."""
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import math

MIN_PRICE_USD = 1e-8
MAX_DATA_AGE_SECONDS = 3600
MIN_PRICE_CONFIDENCE = 0.9


def _number(value, label, minimum=0, maximum=None):
    if (type(value) not in (int, float) or not math.isfinite(value)
            or value < minimum or (maximum is not None and value > maximum)):
        raise ValueError(f'{label} must be finite and within the permitted range')


@dataclass(frozen=True)
class Position:
    collateral_units: float
    debt_units: float
    collateral_price_usd: float
    debt_price_usd: float
    liquidation_threshold: float

    def __post_init__(self):
        for name in ('collateral_units', 'debt_units'):
            _number(getattr(self, name), name)
        for name in ('collateral_price_usd', 'debt_price_usd'):
            _number(getattr(self, name), name, MIN_PRICE_USD)
            if getattr(self, name) == MIN_PRICE_USD:
                raise ValueError('Prices are too close to zero for this model')
        _number(self.liquidation_threshold, 'Liquidation threshold', 0, 1)
        if not all(math.isfinite(v) for v in (self.collateral_usd, self.debt_usd, self.equity_usd)):
            raise ValueError('Position values exceed the supported numeric range')

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
    for shock in (collateral_shock_pct, debt_shock_pct):
        _number(shock, 'Price shock', -100, 100)
        if shock == -100:
            raise ValueError('A -100% shock requires a separate zero-price model')
    return Position(
        position.collateral_units, position.debt_units,
        position.collateral_price_usd * (1 + collateral_shock_pct / 100),
        position.debt_price_usd * (1 + debt_shock_pct / 100),
        position.liquidation_threshold,
    )


class ScenarioType(str, Enum):
    WETH_MARKET = 'WETH market-price shock'
    USDC_MARKET = 'USDC market-price shock / depeg'
    WETH_OUTAGE = 'WETH oracle-source outage'
    USDC_OUTAGE = 'USDC oracle-source outage'

    @property
    def oracle_only(self):
        return self in (self.WETH_OUTAGE, self.USDC_OUTAGE)

    @property
    def asset(self):
        return 'WETH' if self in (self.WETH_MARKET, self.WETH_OUTAGE) else 'USDC'


@dataclass(frozen=True)
class Scenario:
    kind: ScenarioType
    shock_pct: float = 0

    def __post_init__(self):
        if not isinstance(self.kind, ScenarioType):
            raise ValueError('Unknown economic scenario type')
        _number(self.shock_pct, 'Price shock', -100, 100)
        if self.shock_pct == -100 or (self.kind.oracle_only and self.shock_pct != 0):
            raise ValueError('Oracle outages cannot imply a price shock; -100% is unsupported')


@dataclass(frozen=True)
class ExecutionAssumptions:
    trade_notional_usd: float
    spread_bps: float
    slippage_bps: float
    trading_fee_bps: float
    gas_usd: float

    def __post_init__(self):
        _number(self.trade_notional_usd, 'Trade notional')
        _number(self.gas_usd, 'Gas')
        for name in ('spread_bps', 'slippage_bps', 'trading_fee_bps'):
            _number(getattr(self, name), name, 0, 10_000)
        if not math.isfinite(self.total_usd):
            raise ValueError('Execution cost exceeds the supported numeric range')

    @property
    def components(self):
        return {
            'Spread': self.trade_notional_usd * self.spread_bps / 10_000,
            'Slippage': self.trade_notional_usd * self.slippage_bps / 10_000,
            'Trading fee': self.trade_notional_usd * self.trading_fee_bps / 10_000,
            'Gas': self.gas_usd,
        }

    @property
    def total_usd(self):
        return sum(self.components.values())


@dataclass(frozen=True)
class EconomicImpact:
    base: Position
    stressed: Position
    pnl_usd: float | None
    price_downside_usd: float | None
    favorable_usd: float | None
    liquidation_eligible: bool
    liquidation_penalty_usd: float | None
    conditional_downside_usd: float | None


def scenario_impact(position: Position, scenario: Scenario,
                    liquidation_bonus: float | None = None,
                    liquidation_fraction: float | None = None) -> EconomicImpact:
    """Mark-to-market loss plus optional *incremental bonus* cost, never principal twice.

    Fraction is assumed debt repaid if liquidation occurs, not Aave's actual close
    factor. Repayment is capped by available stressed collateral including bonus.
    """
    if liquidation_bonus is not None:
        _number(liquidation_bonus, 'Liquidation bonus fraction', 0, 6)
    if liquidation_fraction is not None:
        _number(liquidation_fraction, 'Assumed debt liquidation fraction', 0, 1)
        if liquidation_bonus is None:
            raise ValueError('A liquidation estimate requires a sourced bonus')
    if scenario.kind.oracle_only:
        return EconomicImpact(position, position, None, None, None,
                              position.debt_usd > 0 and position.health_factor < 1, None, None)
    stressed = stress_position(position,
                               scenario.shock_pct if scenario.kind.asset == 'WETH' else 0,
                               scenario.shock_pct if scenario.kind.asset == 'USDC' else 0)
    pnl = stressed.equity_usd - position.equity_usd
    eligible = stressed.debt_usd > 0 and stressed.health_factor < 1
    penalty = 0.0 if not eligible else None
    if eligible and liquidation_fraction is not None:
        repayment = min(stressed.debt_usd * liquidation_fraction,
                        stressed.collateral_usd / (1 + liquidation_bonus))
        penalty = repayment * liquidation_bonus
    # Unknown liquidation damage is excluded, making downside a stated lower bound.
    net_pnl = pnl - (penalty or 0)
    return EconomicImpact(position, stressed, pnl, max(-pnl, 0), max(net_pnl, 0),
                          eligible, penalty, max(-net_pnl, 0))


@dataclass(frozen=True)
class Decision:
    recommendation: str
    rationale: str
    expected_downside_usd: float | None
    expected_favorable_usd: float | None
    exit_cost_usd: float
    breakeven_probability: float | None
    impact: EconomicImpact
    issues: tuple[str, ...]


def assess_scenario(position: Position, scenario: Scenario, probability_pct: float,
                    execution: ExecutionAssumptions, *, liquidation_bonus=None,
                    liquidation_fraction=None, quality_issues=()) -> Decision:
    _number(probability_pct, 'Assumed 24h scenario probability', 0, 100)
    impact = scenario_impact(position, scenario, liquidation_bonus, liquidation_fraction)
    issues = list(quality_issues)
    if position.collateral_units == 0 and position.debt_units == 0:
        issues.append('No position exposure is entered.')
    elif position.equity_usd <= 0:
        issues.append('Non-positive base equity: an orderly funded exit is not established.')
    if position.debt_usd > 0 and position.health_factor < 1:
        issues.append('Base position is already modeled liquidation-eligible; execution is uncertain.')
    if execution.trade_notional_usd == 0 and (position.collateral_units or position.debt_units):
        issues.append('Zero traded notional does not establish an unwind cost for this position.')
    expected = None if scenario.kind.oracle_only else probability_pct / 100 * impact.conditional_downside_usd
    favorable = None if scenario.kind.oracle_only else probability_pct / 100 * impact.favorable_usd
    breakeven = (execution.total_usd / impact.conditional_downside_usd
                 if impact.conditional_downside_usd else None)
    if scenario.kind.oracle_only:
        recommendation, reason = 'MONITOR', 'Oracle outage impact is unquantified; no market crash or dollar loss is inferred.'
    elif issues:
        recommendation, reason = 'MONITOR', 'Critical input or execution uncertainty: ' + ' '.join(issues)
    elif expected > execution.total_usd:
        recommendation, reason = 'EXIT', 'Modeled 24h expected downside exceeds assumed execution cost.'
        if impact.liquidation_eligible and impact.liquidation_penalty_usd is None:
            reason += ' Price downside alone justifies this comparison; liquidation damage remains unquantified.'
    elif impact.liquidation_eligible and impact.liquidation_penalty_usd is None:
        recommendation, reason = 'MONITOR', 'Price downside does not justify exit, but incremental liquidation damage is unquantified.'
    else:
        recommendation, reason = 'STAY', 'Modeled 24h expected downside does not exceed assumed execution cost.'
    return Decision(recommendation, reason, expected, favorable, execution.total_usd,
                    breakeven, impact, tuple(issues))


def snapshot_quality_issues(snapshot: dict, now: datetime | None = None) -> tuple[str, ...]:
    """Conservative decision gate; demo/legacy arithmetic remains available.

    Policy: observations <=1h old, confidence >=0.9, future tolerance 60s.
    These are explicit quality rules, not empirically calibrated risk estimates.
    """
    from src.sources import SourceError, validate_snapshot
    if not isinstance(snapshot, dict) or snapshot.get('mode') != 'sourced':
        return ('Illustrative data: no sourced prices or reserve configuration for an actionable comparison.',)
    try:
        validate_snapshot(snapshot)
    except (SourceError, TypeError, ValueError):
        return ('Source snapshot is incomplete or invalid.',)
    current = (now or datetime.now(timezone.utc)).timestamp()
    issues = []

    def fresh(timestamp, label):
        if type(timestamp) not in (int, float) or not math.isfinite(timestamp):
            issues.append(label + ' timestamp is missing or invalid.')
        elif not -60 <= current - timestamp <= MAX_DATA_AGE_SECONDS:
            issues.append(label + ' is stale or future-dated (1h freshness policy).')

    try:
        retrieved = datetime.fromisoformat(snapshot['retrieved_at'].replace('Z', '+00:00'))
        if retrieved.tzinfo is None:
            raise ValueError('Timezone required')
        fresh(retrieved.timestamp(), 'Snapshot')
    except (ValueError, TypeError, OverflowError):
        issues.append('Snapshot retrieval time is invalid.')
    for asset in ('WETH', 'USDC'):
        price = snapshot['prices'][asset]
        if price['usd'] <= MIN_PRICE_USD:
            issues.append(asset + ' price is too close to zero for this model.')
        fresh(price.get('timestamp'), asset + ' spot observation')
        confidence = price.get('confidence')
        if type(confidence) not in (int, float) or not MIN_PRICE_CONFIDENCE <= confidence <= 1:
            issues.append(asset + ' price confidence is missing or below 0.9.')
    config = snapshot.get('reserve_configuration')
    if not config:
        issues.append('Sourced WETH reserve configuration is missing; threshold is assumed.')
    else:
        fresh(config['block']['timestamp'], 'Reserve block')
        for asset, reserve in config['reserves'].items():
            if not reserve['is_active'] or reserve['is_frozen']:
                issues.append(asset + ' reserve is inactive or frozen; execution assumptions need review.')
    return tuple(issues)
