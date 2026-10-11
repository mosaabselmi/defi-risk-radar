"""Presentation taxonomy and faithful projections; never new dependencies."""
from copy import deepcopy

import networkx as nx


CATEGORIES = (
    'Position', 'Protocol', 'Smart Contract', 'Asset/Token', 'Oracle',
    'Lending Market', 'Liquidity Pool/AMM', 'Vault/Strategy', 'Blockchain',
    'Bridge', 'Infrastructure/Issuer', 'Unknown/Boundary',
)

# Category colors indicate FUNCTION ONLY, not relationship verification or risk.
_DARK = ('#263B51','#38334F','#35404A','#24413E','#3D3552','#334230',
         '#294447','#413445','#303B4D','#3D3D30','#3B3E43','#3C3834')
_LIGHT = ('#E4EDF7','#EDE8F7','#E8EDF0','#E0F0EA','#EFE7F6','#E8EEDC',
          '#DFF0F2','#F2E5EE','#E6EBF3','#EFEEDC','#E8EAED','#EEE8E2')


def category_color(category, theme='dark'):
    category = category if category in CATEGORIES else 'Unknown/Boundary'
    return (_LIGHT if theme == 'light' else _DARK)[CATEGORIES.index(category)]


def node_semantics(key, data):
    """Functional role is separate from a contract/account/record identity.

    Only known existing graph keys or explicit valid category metadata map to a
    role. Unknown kinds never create or imply a vault, bridge, issuer or AMM.
    """
    if key == 'position':
        category, identity = 'Position', 'Public account; ownership not established'
    elif key == 'pool':
        category, identity = 'Protocol', 'Aave Pool smart contract'
    elif key == 'oracle':
        category, identity = 'Oracle', 'Aave Price Oracle smart contract'
    elif key == 'ethereum':
        category, identity = 'Blockchain', 'Ethereum network; shared infrastructure'
    elif key.startswith('exposure:'):
        category, identity = 'Position', 'Observed asset-specific wallet exposure'
    elif key.startswith('reserve:'):
        category, identity = 'Lending Market', 'Logical reserve record; not a separate contract'
    elif key.startswith(('token:', 'a_token:', 'debt_token:')):
        category, identity = 'Asset/Token', 'ERC-20 smart contract; ' + data.get('kind', 'token component')
    elif key.startswith('valuation:'):
        category, identity = 'Oracle', 'Asset-specific oracle query; not a separate contract'
    elif key.startswith('source:'):
        category, identity = (('Oracle', 'Configured oracle-source smart contract; health unverified')
                              if data.get('contract_address') else
                              ('Unknown/Boundary', 'Source/fallback dependency not established'))
    else:
        category = data.get('functional_category', 'Unknown/Boundary')
        if category not in CATEGORIES:
            category = 'Unknown/Boundary'
        identity = data.get('kind', 'Unclassified component; technical identity not established')
    return {'category': category, 'technical_identity': identity}


def overview_graph(graph, selected=None):
    """Hide token/query details, retain exposure/reserve meaning and shared nodes.

    Retained endpoints connect only through paths in the original graph whose
    interior nodes are hidden. Original edges are copied verbatim. Every added
    presentation edge is marked as a projection with all constituent paths and
    evidence. No transitive path becomes a direct verified contract claim.
    The inspected component is always retained, even if normally hidden.
    """
    retained = {key for key in graph if key in ('position','pool','oracle','ethereum')
                or key.startswith(('exposure:', 'reserve:', 'source:'))}
    if selected in graph:
        retained.add(selected)
    result = nx.DiGraph(**deepcopy(graph.graph))
    result.graph.update(view='Overview', hidden_nodes=sorted(set(graph) - retained))
    result.add_nodes_from((key, deepcopy(data)) for key, data in graph.nodes(data=True) if key in retained)
    for start in graph:
        if start not in retained:
            continue
        # The captured graph is small and acyclic; visited paths also bound cycles.
        pending = [[start]]
        paths = {}
        while pending:
            path = pending.pop()
            for child in graph.successors(path[-1]):
                if child in path:
                    continue
                extended = path + [child]
                if child in retained:
                    paths.setdefault(child, []).append(extended)
                else:
                    pending.append(extended)
        for end, routes in paths.items():
            if graph.has_edge(start, end):
                # Preserve original direct edge evidence. Any alternate hidden
                # routes are inspectable separately and never upgrade that edge.
                edge = deepcopy(graph[start][end])
                edge.update(projected=False, projection_paths=deepcopy(routes))
            else:
                levels = {graph[u][v]['evidence_level'] for path in routes for u, v in zip(path,path[1:])}
                level = 'unknown' if 'unknown' in levels else 'modeled'
                edge = {'relation': 'Summarized dependency path; not a direct contract relationship',
                        'evidence_level': level, 'verified_onchain': False,
                        'call_evidence': None, 'provenance': 'Faithful presentation projection; full constituent paths retained',
                        'chain_id': 1, 'block_number': graph.graph['block']['number'],
                        'block_hash': graph.graph['block']['hash'], 'projected': True,
                        'projection_paths': deepcopy(routes),
                        'path_evidence': [[{'dependent':u,'dependency':v,**deepcopy(graph[u][v])}
                                           for u,v in zip(path,path[1:])] for path in routes]}
            result.add_edge(start, end, **edge)
    return result


def inspection_evidence_rows(graph, inspection):
    """Compact table uses the original Block 11 inspection, not projected reachability."""
    if not inspection.get('available'):
        return []
    component = inspection['component']
    semantics = node_semantics(component, graph.nodes[component])
    return [{'Component': inspection['label'].replace('\n', ' · '),
             'Function': semantics['category'], 'Technical identity': semantics['technical_identity'],
             'Evidence status': inspection['component_evidence_status'],
             'Relationship evidence': ', '.join(inspection['trace_evidence_levels']),
             'Affected exposures': ', '.join(row['asset'] for row in inspection['exposures']) or 'No supported exposure path',
             'Observed position': graph.graph['wallet_address'],
             'Provenance': 'Full paths & pinned RPC below'}]
