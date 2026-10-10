"""Pure hypothetical repayment accounting; never withdrawal or execution advice.

Reuse the validated wallet stress engine and its Aave rounding conventions.
Nominal repayment floors token base units; actual scaled-debt settlement, interest,
funding conversion, approvals, liquidity and transaction ordering are unverified.
"""
from decimal import Decimal, ROUND_FLOOR, localcontext

from src.wallet_decision import _decimal
from src.wallet_risk import analyze_wallet_stress, _valuation, _public


def analyze_wallet_mitigation(observation, collateral_address, shock,
                              external_funding_usd, repayment_pct, *,
                              historical_replay=False, now=None, incident='market_price'):
    """Repay the same assumed percentage of EVERY debt token, externally funded.

    Prices/thresholds and all supplies stay fixed in the selected stress scenario.
    Funding compares conservative debt-token marks only, excluding trading/gas
    costs. No asset sales, collateral withdrawals or flash-loan routes are modeled.
    """
    unavailable = {'coverage': False, 'status': 'MONITOR / INSUFFICIENT MODEL COVERAGE',
                   'current_actionable': False, 'execution_status': 'UNVERIFIED',
                   'withdrawal_modeled': False, 'post_repayment': None}
    try:
        with localcontext() as context:
            context.prec = 120
            if incident != 'market_price':
                raise ValueError('Technical outages do not supply a supported financial repayment scenario.')
            funding = _decimal(external_funding_usd, 'Assumed external repayment funding USD')
            fraction = _decimal(repayment_pct, 'Assumed proportion of every debt token repaid', maximum=100) / 100
            stress = analyze_wallet_stress(observation, collateral_address, shock, now,
                                          historical_replay=historical_replay)
            if not stress['coverage']:
                raise ValueError(stress['reason'])
            assets, repaid, obligations = [], [], []
            baseline_rows = {r['reserve_address']: r for r in stress['baseline']['assets']}
            scenario_rows = {r['reserve_address']: r for r in stress['stressed']['assets']}
            for asset in observation['assets']:
                address = asset['reserve_address']
                debt = int(asset['variable_debt_raw'])
                repayment = int((Decimal(debt) * fraction).to_integral_value(rounding=ROUND_FLOOR))
                modeled = {'address': address, 'symbol': asset['symbol'], 'decimals': asset['decimals'],
                           'supplied': int(asset['supplied_raw']), 'borrowed': debt - repayment,
                           'enabled': asset['collateral_enabled'],
                           'price': int(asset['oracle_price_base_raw']),
                           'threshold': asset['reserve_configuration_raw'][2]}
                assets.append(modeled)
                repaid.append({**modeled, 'borrowed': repayment})
                if debt:  # Every nonzero obligation remains, including dust.
                    unit = Decimal(10) ** asset['decimals']
                    obligations.append({'asset': asset['symbol'], 'reserve_address': address,
                                        'decimals': asset['decimals'], 'debt_raw': debt,
                                        'debt_units': Decimal(debt) / unit,
                                        'pinned_price_usd': baseline_rows[address]['price_usd'],
                                        'scenario_price_usd': scenario_rows[address]['price_usd'],
                                        'pinned_debt_usd': baseline_rows[address]['debt_usd'],
                                        'scenario_debt_usd': scenario_rows[address]['debt_usd'],
                                        'repayment_raw': repayment, 'repayment_units': Decimal(repayment) / unit,
                                        'remaining_raw': debt - repayment,
                                        'remaining_units': Decimal(debt - repayment) / unit})
            convention = stress['hf_convention']
            post = _public(_valuation(assets, collateral_address, stress['shock']), convention)
            repayment_values = _public(_valuation(repaid, collateral_address, stress['shock']), convention)
            repayment_rows = {r['reserve_address']: r for r in repayment_values['assets']}
            remaining_rows = {r['reserve_address']: r for r in post['assets']}
            for row in obligations:
                row['repayment_usd'] = repayment_rows[row['reserve_address']]['debt_usd']
                row['remaining_debt_usd'] = remaining_rows[row['reserve_address']]['debt_usd']
            requirement = repayment_values['debt_usd']
            full = stress['stressed']['debt_usd']
            return {**unavailable, 'coverage': True, 'status': 'MONITOR / EXECUTION NOT VERIFIED',
                    'source_label': stress['source_label'], 'historical_replay': stress['historical_replay'],
                    'provenance': stress['provenance'], 'selected_reserve': collateral_address,
                    'shock': stress['shock'], 'repayment_fraction': fraction,
                    'assumed_external_funding_usd': funding,
                    'debt_obligations': obligations,
                    'pinned_full_debt_usd': stress['baseline']['debt_usd'],
                    'scenario_full_debt_usd': full,
                    'pinned_full_funding_sufficient': funding >= stress['baseline']['debt_usd'],
                    'full_funding_sufficient': funding >= full,
                    'full_funding_shortfall_usd': max(full - funding, Decimal(0)),
                    'repayment_requirement_usd': requirement,
                    'repayment_funding_sufficient': funding >= requirement,
                    'repayment_funding_shortfall_usd': max(requirement - funding, Decimal(0)),
                    'assumed_funding_left_usd': max(funding - requirement, Decimal(0)),
                    'all_debt_nominally_repaid': all(a['borrowed'] == 0 for a in assets),
                    'before_repayment': stress['stressed'], 'post_repayment': post,
                    'hf_convention': convention, 'fees_included': False,
                    'reason': 'Mathematical token-debt coverage only. USD funding is assumed convertible '
                              'into each exact debt token at modeled oracle prices; no funds, route or execution are verified.',
                    'limitations': [
                        'Outstanding balances include interest through the captured block; later accrual and scaled-debt burn rounding are not simulated.',
                        'Repayment uses external funds, not collateral sale proceeds or marked equity. Supplied units and collateral flags are held unchanged.',
                        'USD marks are conservatively rounded per asset using the existing Aave base-unit accounting; USD sufficiency does not prove token availability.',
                        'Trading fees, spreads, slippage and gas are excluded from repayment requirements and remain separate assumptions in conditional economics.',
                        'No collateral withdrawal is modeled. Pool liquidity, reserve restrictions, approvals, funding conversion and transaction mechanics remain unverified.',
                        'Post-repayment HF is conditional arithmetic, not an execution guarantee. Liquidation may occur before any hypothetical repayment.',
                        'Paying debt transfers external wealth into the position; a higher position equity is not investment profit or verified downside avoidance.',
                    ]}
    except (ValueError, KeyError, TypeError, AttributeError, IndexError, ArithmeticError) as exc:
        return {**unavailable, 'reason': str(exc) or 'Incomplete repayment-model inputs.'}
