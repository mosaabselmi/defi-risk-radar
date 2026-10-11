"""Failure mechanisms over Block 11 inspection; no graph or financial engine."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation

from src.wallet import wallet_quality_issues


INCIDENTS = {
    'market_price': 'Token market-price shock',
    'oracle_disruption': 'Oracle disruption / incorrect pricing',
    'contract_disruption': 'Contract compromise / interruption',
    'liquidity_disruption': 'Liquidity / exit disruption',
    'network_disruption': 'Blockchain transaction disruption',
    'stablecoin_depeg': 'Stablecoin depeg · explicit price scenario',
}

MECHANISMS = {
    'market_price': 'The explicitly selected token changes USD market price; this does not imply a dependency malfunction.',
    'stablecoin_depeg': 'An explicitly selected token departs from an assumed peg; this is an asset-price scenario, not an oracle outage.',
    'oracle_disruption': 'A configured source or oracle may stop updating or return stale/incorrect prices.',
    'contract_disruption': 'A relevant contract could be compromised or stop servicing operations.',
    'liquidity_disruption': 'Repayment-token acquisition, trading routes or withdrawals may lack executable liquidity.',
    'network_disruption': 'Congestion, unavailable block inclusion or network interruption may delay or prevent transactions.',
}


def analyze_dependency_failure(observation, inspection, analysis, incident, asset_address=None):
    """Classify a hypothetical mechanism, reusing only an already-covered stress.

    Inspection paths describe possible exposure, not actual failure causation.
    This adapter never fetches, builds a graph, calls a financial engine, or
    changes a selected asset/shock. Its supported numerical output is an exact
    copy of the independently selected, matching market-scenario result.
    """
    result = {'available': False, 'incident': incident, 'quantification': 'UNQUANTIFIED',
              'financial': None, 'exposures': [], 'selected_asset': None,
              'current_actionable': False, 'historical': True,
              'source_label': 'HISTORICAL / NOT LIVE',
              'mechanism': MECHANISMS.get(incident, 'Unsupported failure mechanism.') if isinstance(incident, str) else 'Unsupported failure mechanism.',
              'consequence': 'No quantified consequence is established.',
              'limitation': 'Complete matching dependency evidence is required.',
              'dimensions': {}, 'provenance': {}, 'can_apply_market': False}
    try:
        if incident not in INCIDENTS or inspection.get('available') is not True or observation.get('complete') is not True:
            result['limitation'] = inspection.get('reason', 'Unsupported incident or unavailable dependency evidence.')
            return result
        graph = analysis.get('graph')
        provenance = inspection['provenance']
        if (graph is None or observation['chain_id'] != 1 or inspection['component'] not in graph
                or provenance['chain_id'] != observation['chain_id']
                or provenance['wallet_address'] != observation['wallet_address']
                or provenance['block'] != observation['block']
                or provenance['retrieved_at'] != observation['retrieved_at']
                or graph.graph['wallet_address'] != observation['wallet_address']
                or graph.graph['block'] != observation['block']):
            return result
        assets = {a['reserve_address']: a for a in observation['assets']}
        rows = inspection['exposures']
        if len({r['reserve_address'] for r in rows}) != len(rows):
            return result
        for row in rows:
            asset = assets[row['reserve_address']]
            if (row['supplied_units'] != asset['supplied_units']
                    or row['debt_units'] != asset['variable_debt_units']
                    or row['collateral_enabled'] != asset['collateral_enabled']):
                return result
            path = row['dependency_path'] or row['association_path']
            if not path or any(not graph.has_edge(u, v) for u, v in zip(path, path[1:])):
                return result
            if row['dependency_path'] and (path[0] != 'exposure:' + row['reserve_address'] or path[-1] != inspection['component']):
                return result
        financial = analysis.get('financial')
        historical = bool((financial or {}).get('historical_replay') or wallet_quality_issues(observation))
        result.update(available=True, historical=historical,
                      source_label='HISTORICAL / NOT LIVE' if historical else 'DATED ON-CHAIN OBSERVATION',
                      component=inspection['component'], dependency=inspection['label'],
                      component_type=inspection['component_type'],
                      contract_address=inspection['contract_address'],
                      evidence_status=inspection['component_evidence_status'],
                      exposures=deepcopy(rows), provenance=deepcopy(provenance))
        result['limitation'] = ('Pinned relationships establish association/reachability only; they do not '
                                'prove a failure, its timing, source health or realized economic loss.')
        if 'unknown' in inspection['trace_evidence_levels']:
            result['limitation'] = 'This trace contains unresolved relationships; the dependency and causal impact are not fully established.'
        elif 'modeled' in inspection['trace_evidence_levels']:
            result['limitation'] = 'This trace includes modeled impact pathways; not every relationship is independently verified.'
        if incident == 'contract_disruption' and not inspection['component'].startswith(('pool', 'oracle', 'token:', 'a_token:', 'debt_token:', 'source:')):
            result['limitation'] += ' The selected account, reserve record or infrastructure is not independently established as a separate contract.'
        if incident == 'oracle_disruption' and not inspection['component'].startswith(('oracle', 'source:', 'valuation:')):
            result['limitation'] += ' The selected component is not an observed oracle/source endpoint; an oracle failure is not established here.'
        if not rows:
            result['consequence'] = 'No active wallet exposure is connected by the supported inspection paths.'
            return result
        if incident in ('market_price', 'stablecoin_depeg'):
            return _price_result(result, observation, inspection, analysis, assets, asset_address)
        collateral = any(Decimal(r['enabled_collateral_units']) > 0 for r in rows)
        debt = any(Decimal(r['debt_units']) > 0 for r in rows)
        supplied = any(Decimal(r['supplied_units']) > 0 for r in rows)
        if incident == 'oracle_disruption':
            result['consequence'] = 'Incorrect Aave valuations could change borrowing power or liquidation eligibility; direction and magnitude are unknown.'
            result['dimensions'] = {
                'Collateral valuation': 'Could be mispriced; no market-price change inferred.' if collateral else 'No enabled collateral in this trace.',
                'Debt valuation': 'Could be mispriced; token obligations remain.' if debt else 'No debt in this trace.',
                'Liquidation eligibility': 'Cannot infer HF changes from a disruption alone.',
                'Ability to exit': 'Operations may be affected by invalid pricing; execution unverified.',
                'Access to funds': 'A source-address read does not establish loss of access or loss of principal.',
            }
        else:
            consequence = {
                'contract_disruption': 'A compromise could threaten supplied funds; a service interruption could block repayment or withdrawal. No exploit loss is estimated.',
                'liquidity_disruption': 'Unwind or repayment may be delayed, unavailable or more costly; collateral value is not immediately withdrawable cash.',
                'network_disruption': 'Delayed or failed transactions may prevent timely mitigation; reachability does not establish a price loss or network failure.',
            }
            result['consequence'] = consequence[incident]
            if incident == 'contract_disruption':
                if inspection['component'].startswith(('source:', 'oracle', 'valuation:')):
                    result['consequence'] = ('A compromise could corrupt pricing; interruption could impair price queries. '
                                             'No direct custody relationship or exploit loss is established.')
                elif not supplied:
                    result['consequence'] = ('Contract compromise/interruption could impair observed debt servicing or accounting. '
                                             'No supplied balance is present along this trace; no exploit loss is estimated.')
            result['dimensions'] = {
                'Collateral valuation': 'No additional price shock is attributed to this technical incident; the independent market scenario is unchanged.',
                'Debt valuation': 'Debt obligations persist; no debt forgiveness or price change inferred.' if debt else 'No debt in this trace.',
                'Liquidation eligibility': 'Not recalculated for this technical incident; inability to mitigate is a separate risk.',
                'Ability to exit': 'Repayment/withdrawal or transaction execution may be disrupted; routes and liquidity unverified.',
                'Access to funds': ('Compromise could cause loss; interruption could delay access. Neither is established.'
                                    if incident == 'contract_disruption' and supplied else
                                    'Temporary access/execution delay is possible; principal loss is not established.'),
            }
            if incident == 'contract_disruption' and inspection['component'].startswith(('source:', 'oracle', 'valuation:')):
                result['dimensions']['Access to funds'] = 'Pricing disruption may constrain operations; no direct custody or principal-loss relationship is established.'
        return result
    except (KeyError, TypeError, ValueError, AttributeError, InvalidOperation):
        return {**result, 'available': False, 'quantification': 'UNQUANTIFIED',
                'financial': None, 'exposures': [], 'can_apply_market': False,
                'limitation': 'Incomplete or inconsistent failure-impact inputs.'}


def _price_result(result, observation, inspection, analysis, assets, address):
    connected = {r['reserve_address']: r for r in result['exposures']}
    if address not in connected:
        result['limitation'] = 'Explicitly select an observed asset connected to this dependency; no asset match is inferred.'
        return result
    asset = assets[address]
    result['selected_asset'] = address
    result['price_exposure'] = deepcopy(connected[address])
    result['dimensions'] = {
        'Collateral valuation': ('Selected asset price changes enabled collateral value and borrowing power.'
                                 if asset['collateral_enabled'] and int(asset['supplied_raw']) else
                                 'No enabled collateral for this asset; supplied holdings may still change marked equity.'),
        'Debt valuation': ('A lower USD price reduces the USD debt mark; a higher price increases it. Token units remain owed.'
                           if int(asset['variable_debt_raw']) else 'No debt in the selected asset.'),
        'Liquidation eligibility': 'Use only the matching covered collateral-price stress; no automatic liquidation claim.',
        'Ability to exit': 'Repayment funding, liquidity and execution remain unverified.',
        'Access to funds': 'A price scenario alone does not establish loss of access or realized principal loss.',
    }
    result['consequence'] = f"{asset['symbol']} price changes may affect supplied-value marks, debt-value marks and HF according to the observed exposure roles."
    if int(asset['variable_debt_raw']):
        result['consequence'] += ' A lower USD price reduces the debt mark; a higher price increases it. Token units remain owed.'
    eligible = asset['collateral_enabled'] and int(asset['supplied_raw']) > 0
    result['can_apply_market'] = bool(eligible)
    if not eligible:
        result['limitation'] = ('The existing wallet engine supports enabled-collateral price shocks only; '
                                'debt-only or disabled-collateral depegs remain qualitative and unquantified.')
        return result
    financial = analysis.get('financial')
    if analysis.get('incident') == 'market_price' and financial is not None and financial.get('coverage') is not True:
        result['limitation'] = 'Existing stress coverage failed: ' + financial.get('reason', 'Incomplete or unsupported evidence.')
        return result
    if (analysis.get('incident') != 'market_price' or not financial
            or financial.get('selected_reserve') != address):
        result['quantification'] = 'ASSUMPTION-DEPENDENT'
        result['consequence'] += ' Financial impact is UNQUANTIFIED until a matching price scenario is explicitly applied.'
        result['limitation'] = 'Apply this collateral explicitly to the market scenario above; the current independent scenario is not used for a different asset or outage.'
        return result
    provenance = financial['provenance']
    if (provenance['block'] != observation['block'] or provenance['wallet_address'] != observation['wallet_address']
            or provenance['retrieved_at'] != observation['retrieved_at'] or provenance['chain_id'] != 1
            or provenance['oracle'] != observation['oracle']
            or Decimal(str(analysis['shock'])) != financial['shock']
            or analysis.get('asset_address') != address):
        result['limitation'] = 'The financial result does not match the inspected account, asset, shock and pinned observation.'
        return result
    if result['historical'] and not financial.get('historical_replay'):
        result['limitation'] = 'Historical data requires the existing verified historical-replay coverage; no current signal is available.'
        return result
    result['quantification'] = 'SUPPORTED SCENARIO'
    result['financial'] = deepcopy(financial)
    result['consequence'] = (f"Explicit {asset['symbol']} price scenario: marked equity change ${financial['equity_change_usd']:,.2f}; "
                             'HF and liquidation severity come from the existing wallet stress engine. Execution remains unverified.')
    result['limitation'] = ('The chosen price shock is hypothetical, not an observed dependency failure or realized loss. '
                            + ('Peg/stablecoin identity is user-assumed, not verified by token metadata. ' if result['incident'] == 'stablecoin_depeg' else '')
                            + ('Some traced relationships are modeled or unresolved. ' if any(level != 'verified' for level in inspection['trace_evidence_levels']) else '')
                            + 'No outage-to-price-loss causation is established.')
    return result
