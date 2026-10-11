"""Deterministic investigations grounded in a complete imported wallet graph.

These are applicable *hypotheses*, never incident detection or probability scores.
Dependency arrows run from the position/exposure to what it relies on.
"""
import networkx as nx

from src.wallet_risk import analyze_wallet_stress


STABLECOIN_SYMBOLS = frozenset({'USDC', 'USDT', 'GHO', 'DAI', 'USDS', 'FRAX', 'LUSD'})


def discover_scenarios(observation, graph, *, historical_replay=False, now=None):
    """Return position-specific investigations; an invalid graph fails closed.

    Ordering favors covered collateral stress, then captured technical scope.
    It is not an estimate of incident likelihood or overall risk.
    """
    if (graph is None or observation.get('complete') is not True
            or graph.graph.get('wallet_address') != observation.get('wallet_address')
            or graph.graph.get('block') != observation.get('block')):
        return []
    assets = {a['reserve_address']: a for a in observation['assets']}
    if set(assets) != set(graph.graph.get('asset_order', [])):
        return []

    def exposure_rows(component, *, account_scope=False):
        rows = []
        for address, asset in assets.items():
            node = 'exposure:' + address
            if node not in graph or (not account_scope and not nx.has_path(graph, node, component)):
                continue
            roles = []
            if int(asset['supplied_raw']):
                roles.append('collateral' if asset['collateral_enabled'] else 'supply (collateral disabled)')
            if int(asset['variable_debt_raw']):
                roles.append('debt')
            if roles:
                rows.append({'asset': asset['symbol'], 'address': address, 'roles': roles,
                             'supplied_units': asset['supplied_units'],
                             'debt_units': asset['variable_debt_units']})
        return rows

    def evidence(component, rows, *, account_scope=False):
        levels = set()
        paths = []
        for row in rows:
            start = 'exposure:' + row['address']
            if nx.has_path(graph, start, component):
                path = nx.shortest_path(graph, start, component)
                if graph.has_edge('position', start):
                    path = ['position'] + path
            elif account_scope and graph.has_edge('position', 'pool') and graph.has_edge('pool', 'reserve:' + row['address']):
                path = ['position', 'pool', 'reserve:' + row['address']]
                levels.add('modeled')  # Account-wide operational impact is an inference.
            else:
                continue
            paths.append(path)
            levels.update(graph[u][v]['evidence_level'] for u, v in zip(path, path[1:]))
        return ('UNRESOLVED' if 'unknown' in levels else 'MIXED / MODELED' if 'modeled' in levels
                else 'VERIFIED PATH' if levels else 'UNKNOWN'), paths

    def add(kind, name, component, rows, consequence, limitation, *, category='QUALITATIVE / OPERATIONAL',
            why='', account_scope=False):
        if component not in graph or not rows:
            return
        status, paths = evidence(component, rows, account_scope=account_scope)
        if not paths:
            return
        rows = [row for row in rows if any('exposure:' + row['address'] in path or
                                          'reserve:' + row['address'] in path for path in paths)]
        if not rows:
            return
        scenarios.append({'id': kind + ':' + component, 'kind': kind, 'name': name,
                          'component': component, 'dependency': graph.nodes[component]['label'].replace('\n', ' · '),
                          'contract_address': graph.nodes[component].get('contract_address'),
                          'pool_address': graph.nodes['pool'].get('contract_address') if 'pool' in graph else None,
                          'evidence': status, 'exposures': rows, 'paths': paths,
                          'quantification': category, 'why': why, 'consequence': consequence,
                          'limitation': limitation})

    scenarios = []
    for address, asset in assets.items():
        component = 'exposure:' + address
        rows = exposure_rows(component)
        if not rows:
            continue
        symbol = asset['symbol']
        if asset['collateral_enabled'] and int(asset['supplied_raw']):
            covered = analyze_wallet_stress(observation, address, 0, historical_replay=historical_replay, now=now)['coverage']
            add('collateral_price', symbol + ' collateral-price decline', component, rows,
                'Collateral mark and adjusted borrowing power may fall; health factor and liquidation eligibility can change.',
                'A hypothetical market shock is not an observed incident. Execution and liquidation outcomes are unverified.',
                category='SUPPORTED SCENARIO' if covered else 'UNSUPPORTED / UNKNOWN',
                why='This observed asset is enabled collateral for the imported account.')
        elif int(asset['variable_debt_raw']) or int(asset['supplied_raw']):
            stable = symbol.upper() in STABLECOIN_SYMBOLS
            add('asset_price', symbol + (' stablecoin price deviation (depeg)' if stable else ' asset-price deviation'),
                component, rows,
                'The USD value of this ' + ('liability' if int(asset['variable_debt_raw']) else 'supply') +
                ' could change in either direction.',
                'The current real-wallet stress control supports enabled collateral shocks only; no dollar loss is calculated. '
                + ('Stablecoin classification follows token metadata; peg behavior is not verified.' if stable else ''),
                category='UNSUPPORTED / UNKNOWN', why='This token is an observed ' +
                ('outstanding debt obligation.' if int(asset['variable_debt_raw']) else 'supplied asset.'))

    # A single verified source contract may serve several assets. One node means
    # one investigation with deduplicated exposure rows, never repeated USD marks.
    for source in sorted(set(graph.graph.get('source_nodes', {}).values())):
        if source not in graph:
            continue
        rows = exposure_rows(source)
        if graph.nodes[source].get('contract_address'):
            add('oracle_source', 'Configured oracle-source disruption', source, rows,
                'Valuation updates for connected assets may become unavailable or incorrect; direction and magnitude are unknown.',
                'The pinned read verifies a configured address, not feed health, freshness, failure or realized loss.',
                why='A pinned getSourceOfAsset read connects this source to an active wallet asset.')
        else:
            add('unresolved_oracle_source', 'Unresolved oracle-source investigation', source, rows,
                'The direct source or fallback route cannot be identified from this capture; impact cannot be localized.',
                'No source contract, feed failure, USD loss or health status is verified for this boundary.',
                category='UNSUPPORTED / UNKNOWN',
                why='This active asset has an unresolved oracle-source mapping in the captured graph.')
    if 'oracle' in graph:
        add('aave_oracle', 'Aave oracle disruption', 'oracle', exposure_rows('oracle'),
            'Borrowing-power and debt valuations may be affected; no market-price move is inferred.',
            'Observed oracle reads establish dependency, not an outage or economic damage.',
            why='Active reserves use the captured Aave Price Oracle for valuation.')
    if 'pool' in graph:
        add('pool_contract', 'Aave Pool contract interruption', 'pool', exposure_rows('pool', account_scope=True),
            'Position operations, including repayment and withdrawal, may be unavailable.',
            'Account-wide operational scope is modeled; no exploit loss, duration or executable route is known.',
            why='The position and its registered reserves are observed in this Pool.', account_scope=True)
        add('lending_liquidity', 'Lending withdrawal or repayment restriction', 'pool', exposure_rows('pool', account_scope=True),
            'A full unwind may require debt repayment and sufficient pool/market liquidity; access could be delayed.',
            'Available withdrawal liquidity, reserve restrictions and transaction execution are not verified.',
            why='This leveraged position uses Aave lending reserves.', account_scope=True)
    if 'ethereum' in graph:
        add('network', 'Ethereum transaction availability disruption', 'ethereum', exposure_rows('ethereum'),
            'Repayment, withdrawal or other mitigation transactions could be delayed.',
            'Reachability does not establish congestion, duration, a market shock or a USD loss.',
            why='Observed contracts execute on Ethereum; this infrastructure edge is modeled.')
    scenarios.sort(key=lambda item: (item['quantification'] != 'SUPPORTED SCENARIO',
                                     -len(item['exposures']), item['kind'], item['component']))
    return scenarios


def causal_trace(scenario, financial=None):
    """A mechanism-specific explanation; only an existing covered engine adds numbers."""
    if scenario['kind'] != 'collateral_price' or scenario['quantification'] != 'SUPPORTED SCENARIO':
        return [scenario['name'], scenario['consequence'], 'Economic effect: UNQUANTIFIED.']
    if not financial or not financial.get('coverage') or financial.get('selected_reserve') != scenario['exposures'][0]['address']:
        return [scenario['name'], 'Select this enabled collateral explicitly to calculate its hypothetical impact.']
    before, after = financial['baseline'], financial['stressed']
    hf = 'N/A (no debt)' if after['health_factor'] is None else f"{after['health_factor']:.4f}"
    base_hf = 'N/A (no debt)' if before['health_factor'] is None else f"{before['health_factor']:.4f}"
    direction = 'falls' if financial['shock'] < 0 else 'rises' if financial['shock'] > 0 else 'is unchanged'
    eligibility = ('definite eligibility' if after['liquidation_eligible'] else 'possible eligibility'
                   if after['liquidation_possible'] else 'no modeled eligibility')
    reserve = scenario['exposures'][0]['address']
    pool = scenario['pool_address']
    return [scenario['exposures'][0]['asset'] + f" selected market-price change {financial['shock']:+.0%}",
            scenario['exposures'][0]['asset'] + ' collateral (' + reserve[:10] + '…) market value ' + direction,
            'Aave Pool (' + pool[:10] + '…) collateral-adjusted value ' + direction +
            '; same-asset debt, if any, also reprices', 'Account HF: ' + base_hf + ' → ' + hf,
            'HF = 1 boundary: ' + eligibility,
            f"Marked equity change: ${financial['equity_change_usd']:,.2f}; conditional response requires assumed probability and costs."]
