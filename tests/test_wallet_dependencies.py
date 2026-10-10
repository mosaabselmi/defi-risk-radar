"""Connected-wallet tests; only deterministic imported RPC observations."""
import copy

import networkx as nx
import pytest

from src.sources import SourceError
from src.wallet import WALLET_SELECTORS
from src.wallet_dependencies import build_wallet_graph, analyze_wallet_incident
from src.wallet_risk import analyze_wallet_stress
from src.wallet_viz import plot_wallet_graph, wallet_layout
from src.viz import get_palette
from tests.test_wallet import WalletRPC, fetch, abi, addr
from tests.test_wallet_risk import multiple_collateral, archived_observation


@pytest.fixture
def observed():
    return fetch(WalletRPC())


def add_source(observed, index=0, source=addr(500)):
    asset = observed['assets'][index]['reserve_address']
    observed['rpc_evidence'].append({'jsonrpc':'2.0','id':999+index,'method':'eth_call',
        'params':[{'to':observed['oracle'],'data':WALLET_SELECTORS['getSourceOfAsset(address)']+asset[2:].rjust(64,'0')},hex(observed['block']['number'])],
        'result':abi(int(source,16))})


def test_dynamic_graph_complete_all_debts_no_demo_assets(observed):
    original = copy.deepcopy(observed)
    graph = build_wallet_graph(observed)
    assert nx.is_directed_acyclic_graph(graph)
    assert graph.graph['wallet_address'] == observed['wallet_address']
    assert graph.graph['block'] == observed['block']
    assert not any('USDC' in d['label'] for n,d in graph.nodes(data=True))
    for asset in observed['assets']:
        address = asset['reserve_address']
        for prefix in ('exposure','reserve','token','valuation','source'):
            assert prefix+':'+address in graph
        assert graph.has_edge('position','exposure:'+address)
        assert graph.has_edge('exposure:'+address,'reserve:'+address)
        assert graph.has_edge('reserve:'+address,'valuation:'+address)
        assert graph.has_edge('valuation:'+address,'oracle')
        assert graph.nodes['exposure:'+address]['debt_units'] == asset['variable_debt_units']
        assert ('a_token:'+address in graph) == bool(int(asset['supplied_raw']))
        assert ('debt_token:'+address in graph) == bool(int(asset['variable_debt_raw']))
    assert observed == original


def test_multiple_collateral_and_debt_components():
    observed = multiple_collateral()
    graph = build_wallet_graph(observed)
    usdt = observed['assets'][1]['reserve_address']
    assert 'a_token:'+usdt in graph and 'debt_token:'+usdt in graph
    assert 'enabled collateral' in graph.nodes['exposure:'+usdt]['label']


def test_only_read_backed_edges_are_verified(observed):
    graph = build_wallet_graph(observed)
    for u,v,e in graph.edges(data=True):
        if e['verified_onchain']:
            assert e['call_evidence'] and e['evidence_level'] == 'verified'
            assert e['block_hash'] == observed['block']['hash']
        else:
            assert e['evidence_level'] in ('modeled','unknown')
    assert graph['pool']['ethereum']['evidence_level'] == 'modeled'
    for a in observed['assets']:
        address = a['reserve_address']
        assert graph['reserve:'+address]['valuation:'+address]['evidence_level'] == 'modeled'
        assert graph['valuation:'+address]['source:'+address]['evidence_level'] == 'unknown'
        assert graph.nodes['source:'+address]['contract_address'] is None


def test_source_mapping_is_pinned_and_asset_specific(observed):
    add_source(observed)
    a,b,c = [a['reserve_address'] for a in observed['assets']]
    graph = build_wallet_graph(observed)
    edge = graph['valuation:'+a]['source:'+a]
    assert edge['verified_onchain'] and edge['call_evidence']['params'][1] == hex(observed['block']['number'])
    assert graph.nodes['source:'+a]['contract_address'] == addr(500)
    ancestors = nx.ancestors(graph,'source:'+a)
    assert 'exposure:'+a in ancestors and 'position' in ancestors
    assert 'exposure:'+b not in ancestors and 'exposure:'+c not in ancestors


def test_zero_source_does_not_fabricate_feed_or_fallback(observed):
    add_source(observed, source=addr(0))
    graph = build_wallet_graph(observed)
    source = 'source:'+observed['assets'][0]['reserve_address']
    assert graph.nodes[source]['contract_address'] is None
    assert 'fallback unresolved' in graph.nodes[source]['kind']
    assert graph['valuation:'+observed['assets'][0]['reserve_address']][source]['evidence_level'] == 'unknown'


@pytest.mark.parametrize('change', [
    lambda o:o.update(complete=False), lambda o:o['assets'].pop(),
    lambda o:o['assets'][0].update(variable_debt_raw='1'),
    lambda o:o['assets'][0]['provenance'].update(block_hash='0x'+'ff'*32),
    lambda o:o['assets'][0].update(decimals=6),
    lambda o:o['assets'][0].update(a_token_address=addr(888)),
    lambda o:o['assets'][0].update(oracle_price_base_raw='0'),
    lambda o:o.update(rpc_evidence=[]),
    lambda o:(o['assets'].pop(),o['enumerated_reserves'].pop()),
])
def test_graph_fails_closed_for_incomplete_or_inconsistent_evidence(observed,change):
    change(observed)
    result = analyze_wallet_incident(observed,addr(100),shock=-.3)
    assert result['graph'] is None and result['financial'] is None
    assert result['status'] == 'MONITOR / INSUFFICIENT MODEL COVERAGE'


def test_wrong_block_oracle_mapping_rejected(observed):
    add_source(observed)
    observed['rpc_evidence'][-1]['params'][1] = 'latest'
    with pytest.raises(SourceError):
        build_wallet_graph(observed)


def test_market_incident_links_unchanged_financial_engine(observed):
    asset = observed['assets'][0]['reserve_address']
    result = analyze_wallet_incident(observed,asset,shock=-.3)
    assert result['financial'] == analyze_wallet_stress(observed,asset,-.3)
    assert result['focus'] == 'exposure:'+asset and result['shock'] == -.3
    assert 'source:'+asset in result['dependencies']
    assert result['financial']['stressed']['debt_usd'] == analyze_wallet_stress(observed,asset,0)['stressed']['debt_usd']
    assert result['financial']['stressed']['liquidation_eligible']
    for path in result['incident_paths'] + result['dependency_paths']:
        assert all(result['graph'].has_edge(u,v) for u,v in zip(path,path[1:]))


def test_collateral_switch_changes_actual_wallet_analysis():
    observed = multiple_collateral()
    a,b = [a['reserve_address'] for a in observed['assets'][:2]]
    one = analyze_wallet_incident(observed,a,shock=-.3)
    two = analyze_wallet_incident(observed,b,shock=-.3)
    assert one['focus'] != two['focus']
    assert one['financial']['equity_change_usd'] != two['financial']['equity_change_usd']


@pytest.mark.parametrize('incident',['oracle_source_outage','oracle_outage','rpc_outage','unsupported'])
def test_technical_incidents_never_invent_price_loss(observed,incident,monkeypatch):
    def forbidden(*args,**kwargs):
        raise AssertionError('Technical outage must not call the financial engine')
    monkeypatch.setattr('src.wallet_dependencies.analyze_wallet_stress',forbidden)
    result = analyze_wallet_incident(observed,observed['assets'][0]['reserve_address'],incident,-.99)
    assert result['financial'] is None and result['status'] == 'MONITOR / INSUFFICIENT MODEL COVERAGE'


def test_historical_graph_does_not_promote_freshness():
    observed = archived_observation()
    original = copy.deepcopy(observed)
    asset = observed['assets'][0]['reserve_address']
    result = analyze_wallet_incident(observed,asset,shock=-.3)
    assert result['graph'] is not None and not result['financial']['coverage']
    replay = analyze_wallet_incident(observed,asset,shock=-.3,historical_replay=True)
    assert replay['financial']['coverage'] and replay['financial']['source_label'] == 'HISTORICAL / NOT LIVE'
    assert observed == original


@pytest.mark.parametrize('theme',['dark','light'])
def test_plot_theme_nodes_labels_arrows_and_evidence_styles(observed,theme):
    add_source(observed)
    graph = build_wallet_graph(observed)
    figure = plot_wallet_graph(graph,'exposure:'+observed['assets'][0]['reserve_address'],theme=theme)
    colors = get_palette(theme)
    assert figure.layout.paper_bgcolor == colors['surface']
    assert figure.layout.hoverlabel.font.color == colors['ink']
    node_traces = [t for t in figure.data if t.fill == 'toself']
    assert len(node_traces) == len(graph)
    assert all(len(t.x)>30 for t in node_traces)
    assert len([a for a in figure.layout.annotations if a.showarrow]) == graph.number_of_edges()
    assert {t.line.dash for t in figure.data if t.meta} == {'solid','dash','dot'}
    assert all(a.font.color == colors['ink'] for a in figure.layout.annotations if not a.showarrow)


def test_dynamic_layout_handles_more_than_three_assets():
    rpc = WalletRPC()
    rpc.reserves = [addr(100+i) for i in range(5)]
    rpc.symbols = ['TOKEN'+str(i) for i in range(5)]
    rpc.supplied = [10**18]*5
    rpc.debts = [0]*5
    rpc.decimals = [18]*5
    rpc.prices = [10**8]*5
    rpc.flags = sum(2 << (2*i) for i in range(5))
    graph = build_wallet_graph(fetch(rpc))
    positions,sizes = wallet_layout(graph)
    assert len(positions) == len(graph)
    for u in graph:
        for v in graph:
            if u != v:
                x,y = positions[u]; a,b = positions[v]
                assert abs(x-a) >= (sizes[u][0]+sizes[v][0])/2 or abs(y-b) >= (sizes[u][1]+sizes[v][1])/2
    assert plot_wallet_graph(graph).layout.height > 560


def test_connected_ui_technical_incident_and_isolation(observed):
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_string('import streamlit as st\nfrom src.wallet_ui import render_wallet_stress\nrender_wallet_stress(st.session_state.observation)')
    app.session_state.observation = observed
    app.session_state.synthetic_sentinel = {'WETH':4,'USDC':6000}
    app.run()
    assert not app.exception and app.get('plotly_chart')
    app.selectbox(key='wallet_incident').select('oracle_source_outage').run()
    assert not app.exception and not app.metric
    assert any('Financial impact: NOT QUANTIFIED' in i.value for i in app.info)
    assert any('MONITOR / INSUFFICIENT MODEL COVERAGE' in w.value for w in app.warning)
    assert app.slider(key='wallet_stress_shock').disabled
    assert app.session_state.synthetic_sentinel == {'WETH':4,'USDC':6000}
    assert app.session_state.observation == observed
    app.selectbox(key='wallet_incident').select('market_price').run()
    assert not app.exception and app.metric
