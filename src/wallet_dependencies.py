"""Dynamic wallet dependencies and incident linkage; no retrieval or financial math."""
import networkx as nx

from src.sources import SourceError, _words, _decode_address
from src.wallet import WALLET_SELECTORS, _address_array
from src.wallet_risk import _read, analyze_wallet_stress


def build_wallet_graph(observation):
    if observation.get('complete') is not True or observation['chain_id'] != 1:
        raise SourceError('A complete Ethereum wallet observation is required for the graph.')
    block = observation['block']
    for read in observation['rpc_evidence']:
        if read['method'] in ('eth_call', 'eth_getCode') and read['params'][1] != hex(block['number']):
            raise SourceError('Wallet graph evidence has inconsistent block tags.')
    enumeration = observation['enumerated_reserves']
    addresses = [e['reserve_address'] for e in enumeration]
    if len(addresses) != len(set(addresses)) or set(addresses) != set(_address_array(_read(observation, observation['pool'], 'getReservesList()'))):
        raise SourceError('Wallet graph requires complete pinned reserve enumeration.')
    argument = observation['wallet_address'][2:].rjust(64, '0')
    for entry in enumeration:
        values = _words(_read(observation, observation['data_provider'], 'getUserReserveData(address,address)',
                              entry['reserve_address'][2:].rjust(64, '0') + argument), 9)
        if values[:3] != [int(entry['supplied_raw']), 0, int(entry['variable_debt_raw'])] or values[8] != int(entry['collateral_enabled']):
            raise SourceError('Reserve enumeration disagrees with pinned wallet balances.')
    active = {e['reserve_address']: e for e in observation['enumerated_reserves']
              if int(e['supplied_raw']) or int(e['variable_debt_raw'])}
    assets = observation['assets']
    if len(assets) != len(active) or {a['reserve_address'] for a in assets} != set(active):
        raise SourceError('Partial wallet exposures cannot form a complete graph.')
    g = nx.DiGraph(wallet_address=observation['wallet_address'], block=dict(block),
                   retrieved_at=observation['retrieved_at'], asset_order=[a['reserve_address'] for a in assets])
    def node(key, label, kind, address=None, asset=None, layer=None):
        g.add_node(key, label=label, kind=kind, contract_address=address, asset=asset, layer=layer)
    def edge(u, v, relation, evidence=None, level='modeled'):
        g.add_edge(u, v, relation=relation, evidence_level='verified' if evidence else level,
                   verified_onchain=bool(evidence), call_evidence=evidence,
                   block_number=block['number'], block_hash=block['hash'],
                   provenance='Pinned RPC contract read' if evidence else 'Analytical relationship' if level == 'modeled' else 'Not discovered')
    node('position', 'Imported position', 'public account, not ownership proof', observation['wallet_address'], layer=6)
    node('pool', 'Aave Pool', 'verified market pool', observation['pool'], layer=5)
    node('oracle', 'Aave Price Oracle', 'shared oracle contract', observation['oracle'], layer=-1)
    node('ethereum', 'Ethereum', 'shared execution infrastructure', layer=-2)
    account_read = _read(observation, observation['pool'], 'getUserAccountData(address)', argument)
    _words(account_read, 6)
    edge('position', 'pool', 'account observed in this lending market', account_read)
    edge('pool', 'ethereum', 'execution relies on Ethereum')
    edge('oracle', 'ethereum', 'oracle contract executes on Ethereum')
    for asset in assets:
        address, symbol = asset['reserve_address'], asset['symbol']
        entry = active[address]
        raw = _read(observation, observation['data_provider'], 'getUserReserveData(address,address)', address[2:].rjust(64, '0') + argument)
        values = _words(raw, 9)
        if (values[:3] != [int(asset['supplied_raw']), 0, int(asset['variable_debt_raw'])]
                or values[8] != int(asset['collateral_enabled'])
                or entry['supplied_raw'] != asset['supplied_raw'] or entry['variable_debt_raw'] != asset['variable_debt_raw']
                or asset['provenance']['block_hash'] != block['hash'] or asset['provenance']['block_number'] != block['number']):
            raise SourceError('Wallet graph exposure evidence is inconsistent.')
        exposure, reserve, token, valuation, source = [name + ':' + address for name in ('exposure', 'reserve', 'token', 'valuation', 'source')]
        roles = []
        if int(asset['supplied_raw']):
            roles.append('enabled collateral' if asset['collateral_enabled'] else 'supply · disabled collateral')
        if int(asset['variable_debt_raw']):
            roles.append('variable debt')
        node(exposure, symbol + '\n' + ' / '.join(roles), 'wallet exposure', asset=address, layer=4)
        g.nodes[exposure].update(supplied_units=asset['supplied_units'], debt_units=asset['variable_debt_units'])
        node(reserve, symbol + ' reserve', 'logical reserve record, not a separate contract', asset=address, layer=3)
        node(token, symbol + '\nunderlying token', 'underlying ERC-20', address, address, 2)
        node(valuation, symbol + '\nAave valuation', 'asset-specific oracle query', observation['oracle'], address, 1)
        edge('position', exposure, 'observed supplied / borrowed exposure', raw)
        edge(exposure, reserve, 'position exposure belongs to this reserve', raw)
        reserve_id = _read(observation, observation['pool'], 'getReserveAddressById(uint16)', format(asset['reserve_id'], '064x'))
        if _decode_address(reserve_id) != address:
            raise SourceError('Reserve ID does not match the imported asset.')
        edge('pool', reserve, 'registered reserve record', reserve_id)
        metadata = _read(observation, address, 'decimals()')
        if _words(metadata, 1)[0] != asset['decimals']:
            raise SourceError('Token metadata mismatch.')
        edge(reserve, token, 'underlying identified by reserve ID and token metadata', [reserve_id, metadata])
        token_read = _read(observation, observation['data_provider'], 'getReserveTokensAddresses(address)', address[2:].rjust(64, '0'))
        token_addresses = _words(token_read, 3)
        for prefix, key, raw_key, index, label in [('a_token', 'a_token_address', 'supplied_raw', 0, 'aToken'),
                                                   ('debt_token', 'variable_debt_token_address', 'variable_debt_raw', 2, 'debt token')]:
            if int(asset[raw_key]):
                if token_addresses[index] != int(asset[key], 16):
                    raise SourceError('Reserve token mapping mismatch.')
                balance_read = _read(observation, asset[key], 'balanceOf(address)', argument)
                if _words(balance_read, 1)[0] != int(asset[raw_key]):
                    raise SourceError('Reserve token balance mismatch.')
                component = prefix + ':' + address
                node(component, symbol + '\n' + label, 'reserve token component', asset[key], address, 2)
                edge(reserve, component, 'configured reserve token; wallet balance cross-checked', [token_read, balance_read])
        price_read = _read(observation, observation['oracle'], 'getAssetPrice(address)', address[2:].rjust(64, '0'))
        if _words(price_read, 1)[0] != int(asset['oracle_price_base_raw']) or int(asset['oracle_price_base_raw']) <= 0:
            raise SourceError('Pinned oracle price mismatch.')
        edge(reserve, valuation, 'analytical borrowing / debt valuation dependency')
        edge(valuation, 'oracle', 'price read from this oracle', price_read)
        # Never transplant demo/source-snapshot mappings from another block or asset.
        data = WALLET_SELECTORS['getSourceOfAsset(address)'] + address[2:].rjust(64, '0')
        reads = [r for r in observation['rpc_evidence'] if r['method'] == 'eth_call'
                 and r['params'][0] == {'to': observation['oracle'], 'data': data}]
        if len(reads) > 1:
            raise SourceError('Ambiguous oracle-source evidence.')
        configured = _decode_address(reads[0]['result'], allow_zero=True) if reads else None
        if configured and int(configured, 16):
            node(source, symbol + '\nconfigured source', 'verified configured source; not feed health verification', configured, address, 0)
            edge(valuation, source, 'oracle getSourceOfAsset mapping for this asset', reads[0], 'verified')
        else:
            node(source, symbol + ('\nno direct source' if configured else '\nsource unknown'),
                 'zero configured direct source; fallback unresolved' if configured else 'source mapping not retrieved', asset=address, layer=0)
            edge(valuation, source, 'fallback dependencies unresolved' if configured else 'oracle-source mapping unavailable', level='unknown')
    return g


def analyze_wallet_incident(observation, asset_address, incident='market_price', shock=0, *, historical_replay=False, now=None):
    """Connect graph selection to the existing engine without duplicating math."""
    try:
        graph = build_wallet_graph(observation)
        if asset_address not in graph.graph['asset_order']:
            raise SourceError('Selected exposure is absent from the imported wallet.')
    except (SourceError, ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
        return {'graph': None, 'financial': None, 'status': 'MONITOR / INSUFFICIENT MODEL COVERAGE', 'reason': str(exc)}
    focus = {'market_price': 'exposure:' + asset_address,
             'oracle_source_outage': 'source:' + asset_address,
             'oracle_outage': 'oracle', 'rpc_outage': 'position'}.get(incident)
    if focus is None:
        return {'graph': graph, 'financial': None, 'status': 'MONITOR / INSUFFICIENT MODEL COVERAGE', 'reason': 'Unsupported incident.'}
    affected = sorted(nx.ancestors(graph, focus) | {focus})
    dependencies = sorted(nx.descendants(graph, 'exposure:' + asset_address))
    paths = list(nx.all_simple_paths(graph, 'position', focus)) if focus != 'position' else [['position']]
    dependency_paths = [path for leaf in dependencies if graph.out_degree(leaf) == 0
                        for path in nx.all_simple_paths(graph, 'exposure:' + asset_address, leaf)]
    financial = analyze_wallet_stress(observation, asset_address, shock, now=now, historical_replay=historical_replay) if incident == 'market_price' else None
    return {'graph': graph, 'focus': focus, 'affected': affected, 'dependencies': dependencies,
            'incident_paths': paths, 'dependency_paths': dependency_paths,
            'asset_address': asset_address, 'incident': incident, 'shock': shock if incident == 'market_price' else None,
            'financial': financial,
            'status': financial['status'] if financial else 'MONITOR / INSUFFICIENT MODEL COVERAGE',
            'reason': financial['reason'] if financial else 'Technical outage impact is unquantified; no market-price shock is inferred. '
            + ('Source mapping is unknown; this is an inspection of an unresolved dependency.' if incident == 'oracle_source_outage' and not graph.nodes[focus].get('contract_address') else '')
            + (' RPC unavailability prevents re-observation; it does not imply Ethereum or Pool failure.' if incident == 'rpc_outage' else '')}
