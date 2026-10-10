"""Evidence-aware conceptual dependency graph.

An edge A -> B means A depends on B. Published contract addresses do not
prove the *current configured* contract-to-contract dependencies on-chain.
"""
import networkx as nx

from src.sources import validate_snapshot


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

# Default modeling rules for demo/legacy records. Oracle-to-feed edges are
# replaced by the discovered mappings when validated on-chain evidence exists.
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
    config = snapshot.get('reserve_configuration', {})
    if not isinstance(config, dict):
        validate_snapshot(snapshot)
    discovery = config.get('oracle_sources')
    if 'oracle_sources' in config:
        validate_snapshot(snapshot)
    for node, kind in NODES:
        g.add_node(node, kind=kind)
    for src, dep, relation in LINKS:
        if discovery is not None and src == 'Aave Price Oracle' and dep in ('WETH Feed', 'USDC Feed'):
            continue
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
    if discovery is not None:
        block = config['block']
        g.nodes['Aave Price Oracle']['contract_address'] = discovery['oracle_address']
        for asset in ('WETH', 'USDC'):
            feed = asset + ' Feed'
            source = discovery['sources'][asset]['source_address']
            configured = int(source, 16) != 0
            g.nodes[feed].update(
                contract_address=source if configured else None,
                kind='configured oracle source' if configured else 'no configured direct oracle source',
            )
            if configured:
                g.add_edge('Aave Price Oracle', feed,
                           relation=f'configured {asset} source via getSourceOfAsset(address)',
                           provenance=f"On-chain snapshot · Ethereum block {block['number']} · {block['hash']} · "
                                      f"oracle {discovery['oracle_address']} · source {source}",
                           verified_onchain=True, chain_id=config['chain_id'],
                           block_number=block['number'], block_hash=block['hash'],
                           block_timestamp=block['timestamp'], retrieved_at=config['retrieved_at'],
                           rpc_host=config['rpc_host'], oracle_address=discovery['oracle_address'],
                           source_address=source, underlying_address=discovery['sources'][asset]['underlying_address'],
                           call_evidence=discovery['calls'][2 if asset == 'WETH' else 3])
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
