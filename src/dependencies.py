"""Evidence-aware conceptual dependency graph.

An edge A -> B means A depends on B. Published contract addresses do not
prove the *current configured* contract-to-contract dependencies on-chain.
"""
import networkx as nx


NODES = [
    ('Position', 'hypothetical investor position'),
    ('Aave Pool', 'lending protocol'),
    ('WETH', 'collateral asset'),
    ('USDC', 'debt asset'),
    ('Aave Price Oracle', 'protocol oracle'),
    ('WETH Feed', 'published oracle reference'),
    ('USDC Feed', 'published oracle reference'),
    ('Ethereum', 'settlement network'),
]

# Each edge is an explicit modeling assumption. Actual deployed feed
# configurations must be independently verified with on-chain oracle calls.
LINKS = [
    ('Position', 'Aave Pool', 'uses lending market'),
    ('Position', 'WETH', 'collateral exposure'),
    ('Position', 'USDC', 'debt exposure'),
    ('Aave Pool', 'Aave Price Oracle', 'uses protocol oracle'),
    ('Aave Pool', 'Ethereum', 'settles on chain'),
    ('Aave Price Oracle', 'WETH Feed', 'assumed WETH source relationship'),
    ('Aave Price Oracle', 'USDC Feed', 'assumed USDC source relationship'),
    ('WETH', 'Ethereum', 'token settles on chain'),
    ('USDC', 'Ethereum', 'token settles on chain'),
]


def build_dependency_graph(snapshot: dict) -> nx.DiGraph:
    g = nx.DiGraph()
    known = snapshot.get('addresses', {})
    sourced = snapshot.get('mode') == 'sourced' and bool(known)
    for node, kind in NODES:
        g.add_node(node, kind=kind)
    for src, dep, relation in LINKS:
        g.add_edge(src, dep, relation=relation,
                   provenance='Modeled link; addresses from published registry' if sourced
                   else 'Illustrative modeling rule',
                   verified_onchain=False)
    contract_map = {
        'Aave Pool': 'POOL', 'Aave Price Oracle': 'ORACLE',
        'WETH': 'WETH_UNDERLYING', 'USDC': 'USDC_UNDERLYING',
        'WETH Feed': 'WETH_ORACLE', 'USDC Feed': 'USDC_ORACLE',
    }
    for node, key in contract_map.items():
        g.nodes[node]['contract_address'] = known.get(key)
    return g


def affected_by_failure(g: nx.DiGraph, component: str) -> list[str]:
    """Return every node upstream of the selected failed dependency."""
    if component not in g:
        return []
    return sorted(nx.ancestors(g, component))


def graph_provenance(g: nx.DiGraph) -> list[dict]:
    return [
        {'Dependent': u, 'Depends on': v,
         'Relationship': meta.get('relation', ''),
         'Evidence level': meta.get('provenance', '')}
        for u, v, meta in g.edges(data=True)
    ]
