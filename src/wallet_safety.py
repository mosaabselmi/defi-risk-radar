"""Bounded searches over existing stress/repayment engines; no new accounting."""
from decimal import Decimal, localcontext

from src.wallet_decision import _decimal
from src.wallet_risk import analyze_wallet_stress
from src.wallet_mitigation import analyze_wallet_mitigation


MAX_DECLINE = Decimal('0.999999999999')
DISTANCE_STEPS = 128
DISTANCE_REFINEMENTS = 40


def analyze_wallet_safety(observation, collateral_address, shock, target_hf,
                          external_funding_usd=0, *, historical_replay=False,
                          now=None, incident='market_price'):
    """Find HF=1 brackets and a minimum sufficient proportional repayment.

    Distance scans positive prices through a 99.9999999999% decline, then
    bisects the first observed crossing for each reconciled convention. A
    sampled non-crossing is explicitly NOT proof that no crossing exists.
    Repayment is minimized on a disclosed binary fraction grid finer than
    1/(256 * largest raw debt); exact floored token units come from Block 10.
    All calls retain the original evidence and historical/freshness policy.
    """
    unavailable = {'coverage': False, 'status': 'MONITOR / INSUFFICIENT MODEL COVERAGE',
                   'current_actionable': False, 'execution_status': 'UNVERIFIED',
                   'distance': None, 'repayment': None}
    try:
        with localcontext() as context:
            context.prec = 120
            if incident != 'market_price':
                raise ValueError('Technical incidents do not establish a price or repayment scenario.')
            target = _decimal(target_hf, 'Target health factor', minimum=1, maximum=100)
            funding = _decimal(external_funding_usd, 'Assumed external funding USD')
            stress = analyze_wallet_stress(observation, collateral_address, shock, now,
                                          historical_replay=historical_replay)
            if not stress['coverage']:
                raise ValueError(stress['reason'])
            # This solver searches for a decline in *collateral* price. Debt
            # repricing is supported by stress, but has no collateral-distance
            # interpretation here.
            selected = next((asset for asset in observation['assets']
                             if asset['reserve_address'] == collateral_address), None)
            if not selected or not selected['collateral_enabled'] or int(selected['supplied_raw']) <= 0:
                raise ValueError('Safety-buffer distance requires nonzero enabled collateral.')
            distance = _distance(observation, collateral_address, stress, historical_replay, now)
            repayment = _repayment(observation, collateral_address, stress['shock'], target,
                                   funding, historical_replay, now)
            return {**unavailable, 'coverage': True, 'status': 'MONITOR / EXECUTION NOT VERIFIED',
                    'source_label': stress['source_label'], 'historical_replay': historical_replay,
                    'provenance': stress['provenance'], 'selected_reserve': collateral_address,
                    'shock': stress['shock'], 'target_hf': target,
                    'distance': distance, 'repayment': repayment,
                    'reason': 'Eligibility is not actual liquidation or guaranteed loss. Repayment is hypothetical external-funded principal, not a fee or verified executable action.'}
    except (ValueError, KeyError, TypeError, AttributeError, IndexError, ArithmeticError) as exc:
        return {**unavailable, 'reason': str(exc) or 'Incomplete safety-buffer inputs.'}


def _distance(observation, selected, stress, replay, now):
    candidates = stress['baseline']['health_factors_by_convention']
    result = {'status': 'NO_DEBT' if stress['baseline']['debt_usd'] == 0 else 'BOUNDARY_SEARCH',
              'by_convention': {}, 'search_max_decline': MAX_DECLINE,
              'resolution': MAX_DECLINE / DISTANCE_STEPS / 2**DISTANCE_REFINEMENTS,
              'scan_steps': DISTANCE_STEPS, 'refinement_steps': DISTANCE_REFINEMENTS,
              'possible_boundary': None, 'definite_boundary': None,
              'boundary_checks': {},
              'baseline_severity': stress['baseline']['liquidation_severity'],
              'limitation': 'First observed crossing on a bounded scan, refined to a bracket. Rounding can produce steps or narrow nonmonotonic intervals; no-crossing results are not a proof of safety. Prices at exactly zero are outside the engine domain.'}
    if result['status'] == 'NO_DEBT':
        return result
    pending = []
    for name, hf in candidates.items():
        if hf <= 1:
            result['by_convention'][name] = {'status': 'ALREADY_AT_OR_BELOW_ONE',
                'lower_decline': Decimal(0), 'upper_decline': Decimal(0), 'lower_hf': hf, 'upper_hf': hf}
        else:
            pending.append(name)
    previous = {name: (Decimal(0), candidates[name]) for name in pending}
    for step in range(1, DISTANCE_STEPS + 1):
        if not pending:
            break
        decline = MAX_DECLINE * step / DISTANCE_STEPS
        sample = analyze_wallet_stress(observation, selected, -decline, now, historical_replay=replay)
        if not sample['coverage']:
            raise ValueError(sample['reason'])
        for name in list(pending):
            hf = sample['stressed']['health_factors_by_convention'][name]
            lower, lower_hf = previous[name]
            if hf <= 1:
                upper, upper_hf = decline, hf
                for _ in range(DISTANCE_REFINEMENTS):
                    middle = (lower + upper) / 2
                    refined = analyze_wallet_stress(observation, selected, -middle, now, historical_replay=replay)
                    if not refined['coverage']:
                        raise ValueError(refined['reason'])
                    middle_hf = refined['stressed']['health_factors_by_convention'][name]
                    if middle_hf <= 1:
                        upper, upper_hf = middle, middle_hf
                    else:
                        lower, lower_hf = middle, middle_hf
                result['by_convention'][name] = {'status': 'BRACKETED', 'lower_decline': lower,
                    'upper_decline': upper, 'lower_hf': lower_hf, 'upper_hf': upper_hf}
                pending.remove(name)
            else:
                previous[name] = (decline, hf)
    for name in pending:
        result['by_convention'][name] = {'status': 'NO_CROSSING_FOUND',
            'lower_decline': None, 'upper_decline': None, 'last_hf': previous[name][1]}
    brackets = [entry for entry in result['by_convention'].values() if entry['upper_decline'] is not None]
    if brackets:
        result['possible_boundary'] = min(entry['upper_decline'] for entry in brackets)
        if len(brackets) == len(candidates):
            result['definite_boundary'] = max(entry['upper_decline'] for entry in brackets)
        result['status'] = 'ALREADY_AT_OR_BELOW_ONE' if result['possible_boundary'] == 0 else 'BRACKETED'
    else:
        result['status'] = 'NO_CROSSING_FOUND'
    for name in ('possible_boundary', 'definite_boundary'):
        decline = result[name]
        if decline is not None:
            checked = analyze_wallet_stress(observation, selected, -decline, now, historical_replay=replay)
            if not checked['coverage']:
                raise ValueError(checked['reason'])
            point = checked['stressed']
            result['boundary_checks'][name] = {
                'health_factor_min': point['health_factor_min'],
                'health_factor_max': point['health_factor_max'],
                'liquidation_severity': point['liquidation_severity']}
            if name == 'definite_boundary' and point['health_factor_max'] > 1:
                # Rounding steps can be nonmonotonic: do not infer a simultaneous
                # all-convention crossing from separate first-crossing brackets.
                result[name] = None
    return result


def _repayment(observation, selected, shock, target, funding, replay, now):
    def evaluate(numerator, denominator):
        result = analyze_wallet_mitigation(observation, selected, shock, funding,
            Decimal(numerator) * 100 / denominator, historical_replay=replay, now=now)
        if not result['coverage']:
            raise ValueError(result['reason'])
        post = result['post_repayment']
        meets = post['health_factor_min'] is None or post['health_factor_min'] >= target
        return result, meets

    original, met = evaluate(0, 1)
    if met:
        return {'status': 'TARGET_ALREADY_MET', 'result': original,
                'lower_fraction': Decimal(0), 'upper_fraction': Decimal(0),
                'fraction_resolution': Decimal(0), 'iterations': 0, 'target_met': True}
    largest = max(int(asset['variable_debt_raw']) for asset in observation['assets'])
    # Raw debt is uint256-validated by the existing engine: at most 264 iterations.
    bits = largest.bit_length() + 8
    denominator = 2**bits
    low, high = 0, denominator
    sufficient, met = evaluate(high, denominator)
    if not met:
        raise ValueError('Target cannot be reached under the supported full-repayment model.')
    count = 0
    while high - low > 1:
        midpoint = (low + high) // 2
        candidate, met = evaluate(midpoint, denominator)
        count += 1
        if met:
            high, sufficient = midpoint, candidate
        else:
            low = midpoint
    below, met = evaluate(low, denominator)
    if met:
        raise ValueError('Repayment minimum did not retain an unmet lower bound.')
    return {'status': 'MINIMUM_SUFFICIENT_ON_GRID', 'result': sufficient,
            'lower_fraction': Decimal(low) / denominator,
            'upper_fraction': Decimal(high) / denominator,
            'fraction_resolution': Decimal(1) / denominator,
            'iterations': count, 'target_met': True,
            'lower_bound_hf': below['post_repayment']['health_factor_min'],
            'lower_bound_requirement_usd': below['repayment_requirement_usd'],
            'limitation': 'Minimum sufficient proportion on the stated search grid, not an executable quote. Token repayments floor to base units. The adjacent lower grid point fails the target. Full nominal repayment removes debt; HF is then N/A, not a finite target value.'}
