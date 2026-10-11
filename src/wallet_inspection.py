"""Read-only dependency traces. Reachability is never a financial loss model."""
from copy import deepcopy

import networkx as nx

from src.sources import SourceError
from src.wallet import WALLET_SELECTORS, _header
from src.wallet_dependencies import build_wallet_graph


def _edge_provenance(observation, graph, u, v):
    edge = graph[u][v]
    result = {'dependent': u, 'dependency': v, **deepcopy(edge), 'rpc_reads': []}
    if edge['evidence_level'] != 'verified':
        return result
    evidence = edge['call_evidence']
    if isinstance(evidence, dict):  # Validated getSourceOfAsset receipt, including optional mappings.
        result['rpc_reads'] = [deepcopy(evidence)]
        return result
    asset_address = graph.nodes[v].get('asset') or graph.nodes[u].get('asset')
    asset = next((a for a in observation['assets'] if a['reserve_address'] == asset_address), None)
    wallet_argument = observation['wallet_address'][2:].rjust(64, '0')
    asset_argument = asset_address[2:].rjust(64, '0') if asset_address else ''
    if u == 'position' and v == 'pool':
        specs = [(observation['pool'], 'getUserAccountData(address)', wallet_argument)]
    elif u == 'position' or u.startswith('exposure:'):
        specs = [(observation['data_provider'], 'getUserReserveData(address,address)', asset_argument + wallet_argument)]
    elif u == 'pool':
        specs = [(observation['pool'], 'getReserveAddressById(uint16)', format(asset['reserve_id'], '064x'))]
    elif v.startswith('token:'):
        specs = [(observation['pool'], 'getReserveAddressById(uint16)', format(asset['reserve_id'], '064x')),
                 (asset_address, 'decimals()', '')]
    elif v.startswith(('a_token:', 'debt_token:')):
        specs = [(observation['data_provider'], 'getReserveTokensAddresses(address)', asset_argument),
                 (graph.nodes[v]['contract_address'], 'balanceOf(address)', wallet_argument)]
    elif v == 'oracle':
        specs = [(observation['oracle'], 'getAssetPrice(address)', asset_argument)]
    else:
        raise SourceError('Verified relationship has no supported raw-evidence trace.')
    for target, signature, argument in specs:
        params = [{'to': target, 'data': WALLET_SELECTORS[signature] + argument}, hex(observation['block']['number'])]
        matches = [e for e in observation['rpc_evidence'] if e.get('method') == 'eth_call' and e.get('params') == params]
        if (len(matches) != 1 or matches[0].get('jsonrpc') != '2.0'
                or type(matches[0].get('id')) is not int or 'error' in matches[0]):
            raise SourceError('Verified relationship is missing unambiguous pinned RPC provenance.')
        result['rpc_reads'].append(deepcopy(matches[0]))
    if [e['result'] for e in result['rpc_reads']] != (evidence if isinstance(evidence, list) else [evidence]):
        raise SourceError('Relationship trace disagrees with verified call evidence.')
    return result


def inspect_wallet_dependency(observation, component):
    """Inspect a captured graph without fetching, changing incidents or pricing outages.

    Pool/position inspections include account membership as explicitly modeled
    impact scope. They do not invent reversed asset-to-Pool dependency edges.
    """
    unavailable = {'available': False, 'status': 'MONITOR / INSUFFICIENT MODEL COVERAGE',
                   'economic_impact': 'UNQUANTIFIED', 'current_actionable': False,
                   'exposures': [], 'position_paths': [], 'relationships': [], 'highlighted_nodes': []}
    try:
        block = observation['block']
        headers = [e for e in observation['rpc_evidence'] if e.get('method') == 'eth_getBlockByNumber']
        if (len(headers) != 2 or any(_header(e['result'], block['number']) != block
                                    or e.get('jsonrpc') != '2.0' or type(e.get('id')) is not int or 'error' in e
                                    for e in headers)
                or headers[0]['params'] not in ([hex(block['number']), False], ['latest', False])
                or headers[1]['params'] != [hex(block['number']), False]):
            raise SourceError('Dependency inspection requires matching pinned block-header evidence.')
        chains = [e for e in observation['rpc_evidence'] if e.get('method') == 'eth_chainId']
        if (len(chains) != 1 or chains[0]['params'] != [] or int(chains[0]['result'], 16) != 1
                or chains[0].get('jsonrpc') != '2.0' or type(chains[0].get('id')) is not int or 'error' in chains[0]):
            raise SourceError('Dependency inspection requires Ethereum mainnet RPC provenance.')
        graph = build_wallet_graph(observation)
        if component not in graph:
            raise SourceError('Inspected component is absent from this imported wallet graph.')
        affected = nx.ancestors(graph, component) | {component}
        position_paths = list(nx.all_simple_paths(graph, 'position', component)) if component != 'position' else [['position']]
        edges = {pair for path in position_paths for pair in zip(path, path[1:])}
        exposures = []
        for asset in observation['assets']:
            address = asset['reserve_address']
            exposure = 'exposure:' + address
            path = nx.shortest_path(graph, exposure, component) if exposure in affected else None
            scope = 'asset-level reverse dependency reachability'
            association = None
            if path is None and component == 'pool':
                # Verified reserve registration + account membership; modeled
                # account-wide impact scope, not an invented causal graph edge.
                association = ['position', 'pool', 'reserve:' + address]
                scope = 'account-level association; impact scope is modeled'
            elif component == 'position':
                association = ['position', exposure]
                scope = 'observed account contents; not reverse reachability'
            elif path is None:
                continue
            if association:
                edges.update(zip(association, association[1:]))
            if path:
                edges.update(zip(path, path[1:]))
            affected.add(exposure)
            exposures.append({'reserve_address': address, 'asset': asset['symbol'],
                              'supplied_units': asset['supplied_units'],
                              'enabled_collateral_units': asset['supplied_units'] if asset['collateral_enabled'] else '0',
                              'debt_units': asset['variable_debt_units'],
                              'collateral_enabled': asset['collateral_enabled'],
                              'scope': scope, 'dependency_path': path, 'association_path': association,
                              'position_exposure_path': ['position', exposure]})
            edges.add(('position', exposure))
        relationships = [_edge_provenance(observation, graph, u, v) for u, v in sorted(edges)]
        data = graph.nodes[component]
        incident_levels = {e['evidence_level'] for _, _, e in list(graph.in_edges(component, data=True)) + list(graph.out_edges(component, data=True))}
        node_status = ('UNRESOLVED' if component.startswith('source:') and not data.get('contract_address')
                       else 'VERIFIED ASSOCIATION' if 'verified' in incident_levels else 'MODELED')
        trace_levels = sorted({e['evidence_level'] for e in relationships})
        return {**unavailable, 'available': True, 'status': 'READ-ONLY DEPENDENCY INSPECTION',
                'component': component, 'label': data['label'], 'component_type': data['kind'],
                'contract_address': data.get('contract_address'), 'component_evidence_status': node_status,
                'trace_evidence_levels': trace_levels,
                'exposures': exposures, 'position_paths': position_paths, 'relationships': relationships,
                'source_mappings': {row['reserve_address']: deepcopy(graph.graph['oracle_sources'][row['reserve_address']])
                                    for row in exposures},
                'highlighted_nodes': sorted(affected),
                'provenance': {'chain_id': observation['chain_id'], 'block': deepcopy(observation['block']),
                               'wallet_address': observation['wallet_address'], 'retrieved_at': observation['retrieved_at'],
                               'rpc_host': observation['rpc_host'],
                               'block_header_evidence': deepcopy([e for e in observation['rpc_evidence']
                                                                 if e.get('method') == 'eth_getBlockByNumber']),
                               'source_header_evidence': deepcopy(observation.get('oracle_sources', {}).get('header_evidence', []))},
                'reason': 'Potential technical exposure only. Verified associations do not verify every '
                          'path, source health, a malfunction or a dollar loss. Impact propagation reverses dependency arrows.'}
    except (SourceError, ValueError, KeyError, TypeError, IndexError, AttributeError, nx.NetworkXException) as exc:
        return {**unavailable, 'reason': str(exc) or 'Incomplete dependency evidence.'}
