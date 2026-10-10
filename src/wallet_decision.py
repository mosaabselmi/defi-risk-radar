"""Conditional wallet economics, never an executable STAY/EXIT recommendation.

Consumes the existing connected analysis. Does not reconstruct portfolio values,
estimate liquidation penalties, query liquidity, or generate transaction plans.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext


@dataclass(frozen=True)
class WalletDecisionAssumptions:
    probability_pct: object
    avoided_downside_pct: object
    trade_notional_usd: object
    spread_bps: object
    slippage_bps: object
    fee_bps: object
    gas_usd: object


def _decimal(value, label, minimum=0, maximum=None):
    if isinstance(value, bool):
        raise ValueError(label + ' must be a finite number.')
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(label + ' must be a finite number.')
    if not number.is_finite() or number < minimum or maximum is not None and number > maximum:
        raise ValueError(label + ' is outside its supported range.')
    return number


def compare_wallet_economics(analysis, assumptions):
    """Expected scenario loss applies probability once; benefits need a mitigation assumption.

    Notional is the sum across user-assumed trading legs, never equity or directly
    withdrawable collateral. Debt principal is a repayment obligation, not a fee.
    Full downside avoidance is hypothetical even when assumed at 100%.
    """
    unavailable = {'comparison_available': False, 'recommendation': 'MONITOR / EXECUTION NOT VERIFIED',
                   'execution_status': 'UNVERIFIED', 'current_actionable': False,
                   'expected_scenario_downside_usd': None, 'hypothetical_cost_usd': None}
    try:
        with localcontext() as context:
            context.prec = 120
            return _compare(analysis, assumptions, unavailable)
    except (ValueError, KeyError, TypeError, AttributeError, ArithmeticError) as exc:
        return {**unavailable, 'reason': str(exc) or 'Incomplete economic comparison inputs.'}


def _compare(analysis, assumptions, unavailable):
    stress = analysis.get('financial')
    if analysis.get('incident') != 'market_price' or not stress or stress.get('coverage') is not True:
        raise ValueError('A supported market-price incident with complete reconciled stress coverage is required. Technical outage losses are unquantified.')
    probability = _decimal(assumptions.probability_pct, 'User-assumed 24h scenario probability', maximum=100)/100
    avoidance = _decimal(assumptions.avoided_downside_pct, 'Assumed downside avoided', maximum=100)/100
    notional = _decimal(assumptions.trade_notional_usd, 'Aggregate assumed trade notional')
    spread = _decimal(assumptions.spread_bps, 'Assumed spread bps', maximum=10000)
    slippage = _decimal(assumptions.slippage_bps, 'Assumed slippage bps', maximum=10000)
    fee = _decimal(assumptions.fee_bps, 'Assumed trading fee bps', maximum=10000)
    gas = _decimal(assumptions.gas_usd, 'Assumed aggregate gas USD')
    if notional == 0:
        raise ValueError('Enter a positive aggregate assumed trade notional. Zero notional does not establish a wallet unwind cost.')
    # Read the engine's result verbatim; no independent exposure/price accounting.
    delta = Decimal(str(stress['equity_change_usd']))
    baseline_equity = Decimal(str(stress['baseline']['equity_usd']))
    stressed_equity = Decimal(str(stress['stressed']['equity_usd']))
    debt = _decimal(stress['baseline']['debt_usd'], 'Pinned baseline debt')
    if not all(value.is_finite() for value in (delta, baseline_equity, stressed_equity)):
        raise ValueError('Stress amounts must be finite.')
    downside, favorable = max(-delta, Decimal(0)), max(delta, Decimal(0))
    expected = probability * downside
    expected_favorable = probability * favorable
    avoidable = downside * avoidance
    benefit = expected * avoidance
    components = {'Spread': notional*spread/10000, 'Slippage': notional*slippage/10000,
                  'Trading fee': notional*fee/10000, 'Gas': gas}
    cost = sum(components.values(), Decimal(0))
    margin = benefit - cost
    conclusion = 'EXPECTED AVOIDED DOWNSIDE EXCEEDS ASSUMED COST' if margin > 0 else 'ASSUMED COST EXCEEDS EXPECTED AVOIDED DOWNSIDE' if margin < 0 else 'BREAK-EVEN UNDER ASSUMPTIONS'
    threshold = cost / avoidable if avoidable else None
    required_avoidance = cost / expected if expected else None
    bps = spread + slippage + fee
    # Algebraic cost ceiling at the chosen probability and avoidance assumption.
    max_notional = (benefit-gas)*10000/bps if bps and benefit >= gas else None
    constraints = [
        'No executable quote, market depth, pool withdrawal liquidity, repayment route, funding or transaction ordering has been verified.',
        'The comparison assumes the entered avoidance fraction can be achieved before the scenario; that efficacy is not verified.',
        'Only the modeled mark-to-market component is compared; liquidation penalties, interest accrual, timing and failed-transaction costs are excluded.',
    ]
    if debt:
        constraints.append('Outstanding debt must be repaid or otherwise unwound before a full collateral withdrawal. The pinned debt mark is not a repayment quote or a transaction fee; all debt tokens, including tiny balances, remain obligations.')
    if baseline_equity <= 0 or stressed_equity <= 0:
        constraints.append('Non-positive marked equity: do not assume sale proceeds can fund repayment or that an equity-sized exit is possible.')
    if stress['risk_severity'] in ('DEFINITE_LIQUIDATION_ELIGIBILITY', 'POSSIBLE_LIQUIDATION_ELIGIBILITY'):
        constraints.append('Liquidation eligibility is modeled or possible. This economic comparison excludes liquidation mechanics and cannot establish total expected loss or a feasible exit.')
    if stress['historical_replay']:
        constraints.append('HISTORICAL / NOT LIVE: retrospective assumption comparison only; no current recommendation.')
    return {**unavailable, 'comparison_available': True, 'conclusion': conclusion,
            'reason': 'Conditional mark-to-market comparison only. A favorable or unfavorable comparison is not an actionable STAY/EXIT recommendation.',
            'mark_to_market_downside_usd': downside, 'favorable_stress_usd': favorable,
            'expected_scenario_downside_usd': expected, 'expected_favorable_usd': expected_favorable,
            'expected_avoided_downside_usd': benefit, 'hypothetical_cost_usd': cost,
            'cost_components': components, 'conditional_margin_usd': margin,
            'breakeven_probability': threshold, 'breakeven_probability_reachable': threshold is not None and threshold <= 1,
            'required_avoidance_fraction': required_avoidance,
            'maximum_trade_notional_usd': max_notional,
            'debt_repayment_required': debt > 0, 'pinned_debt_mark_usd': debt,
            'baseline_equity_usd': baseline_equity, 'stressed_equity_usd': stressed_equity,
            'source_label': stress['source_label'], 'historical_replay': stress['historical_replay'],
            'risk_severity': stress['risk_severity'], 'constraints': constraints,
            'assumptions': {'probability': probability, 'avoided_downside_fraction': avoidance,
                            'trade_notional_usd': notional, 'spread_bps': spread, 'slippage_bps': slippage,
                            'fee_bps': fee, 'gas_usd': gas}, 'provenance': stress['provenance']}
