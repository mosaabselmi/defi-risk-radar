from tests.ui_navigation import control, go_page
from tests.ui_navigation import analysis_app, navigate
"""Presentation-only categorization and faithful graph reduction tests."""
import copy
from pathlib import Path

import networkx as nx
import pytest
from streamlit.testing.v1 import AppTest

from src.wallet_semantics import CATEGORIES, node_semantics, category_color, overview_graph, inspection_evidence_rows
from src.wallet_dependencies import build_wallet_graph, analyze_wallet_incident
from src.wallet_inspection import inspect_wallet_dependency
from src.wallet_viz import plot_wallet_graph, wallet_layout, _compact_route
from src.wallet_replay import load_historical_example
from src.viz import get_palette
from tests.test_wallet import WalletRPC, fetch, addr
from tests.test_wallet_oracles import SourceRPC


@pytest.fixture
def observed():
    return fetch(SourceRPC())


@pytest.mark.parametrize('key,category,identity', [
    ('position','Position','account'), ('pool','Protocol','smart contract'),
    ('oracle','Oracle','smart contract'), ('ethereum','Blockchain','network'),
    ('exposure:x','Position','exposure'), ('reserve:x','Lending Market','not a separate contract'),
    ('token:x','Asset/Token','ERC-20'), ('a_token:x','Asset/Token','ERC-20'),
    ('debt_token:x','Asset/Token','ERC-20'), ('valuation:x','Oracle','not a separate contract'),
    ('source:x','Oracle','health unverified'), ('source:shared:x','Oracle','health unverified'),
])
def test_function_is_separate_from_technical_identity(key, category, identity):
    result = node_semantics(key, {'contract_address':addr(888),'kind':'reserve token component'})
    assert result['category'] == category and identity in result['technical_identity']


def test_missing_source_is_boundary_not_a_verified_oracle():
    result = node_semantics('source:x', {'contract_address':None,'kind':'unavailable'})
    assert result['category'] == 'Unknown/Boundary'
    assert 'not established' in result['technical_identity']


@pytest.mark.parametrize('value', [None,'made-up issuer','', 'verified'])
def test_invalid_category_metadata_stays_unknown(value):
    assert node_semantics('external', {'functional_category':value})['category'] == 'Unknown/Boundary'
    assert category_color(value) == category_color('Unknown/Boundary')


@pytest.mark.parametrize('category', CATEGORIES)
def test_taxonomy_supports_roles_without_inventing_components(category):
    assert node_semantics('declared-component', {'functional_category':category})['category'] == category


def test_no_vault_bridge_amm_or_issuer_is_inferred_from_a_token_name():
    result = node_semantics('token:x', {'kind':'EURCV vault bridge issuer'})
    assert result['category'] == 'Asset/Token'
    assert node_semantics('mystery', {'kind':'vault'})['category'] == 'Unknown/Boundary'


def test_projection_retains_exposure_meaning_original_edges_and_full_paths(observed):
    graph = build_wallet_graph(observed)
    original = copy.deepcopy(graph)
    overview = overview_graph(graph)
    assert len(overview) < len(graph)
    assert set(overview) <= set(graph)
    assert {key for key in graph if key.startswith(('exposure:','reserve:','source:'))} <= set(overview)
    for key, data in overview.nodes(data=True):
        assert data == graph.nodes[key]
    for u,v,edge in overview.edges(data=True):
        assert edge['projection_paths']
        for path in edge['projection_paths']:
            assert path[0] == u and path[-1] == v
            assert all(graph.has_edge(a,b) for a,b in zip(path,path[1:]))
            assert all(node not in overview for node in path[1:-1])
        if edge['projected']:
            assert not graph.has_edge(u,v)
            assert edge['evidence_level'] != 'verified' and not edge['verified_onchain']
            assert 'not a direct' in edge['relation']
        else:
            assert all(edge[field] == value for field,value in graph[u][v].items())
    assert graph.graph == original.graph
    assert list(graph.nodes(data=True)) == list(original.nodes(data=True))
    assert list(graph.edges(data=True)) == list(original.edges(data=True))


@pytest.mark.parametrize('level', ['verified','modeled','unknown'])
def test_mixed_hidden_path_never_becomes_a_direct_verified_edge(level):
    graph = nx.DiGraph(block={'number':123,'hash':'0x'+'ab'*32})
    graph.add_nodes_from(['position','hidden','pool'])
    graph.add_edge('position','hidden',evidence_level='verified')
    graph.add_edge('hidden','pool',evidence_level=level)
    overview = overview_graph(graph)
    edge = overview['position']['pool']
    assert edge['evidence_level'] == ('unknown' if level == 'unknown' else 'modeled')
    assert edge['projection_paths'] == [['position','hidden','pool']]
    assert edge['path_evidence'][0][1]['evidence_level'] == level


def test_direct_edge_is_not_upgraded_by_an_alternate_hidden_path():
    graph = nx.DiGraph(block={'number':123,'hash':'x'})
    graph.add_edge('position','pool',evidence_level='unknown',verified_onchain=False)
    graph.add_edge('position','hidden',evidence_level='verified')
    graph.add_edge('hidden','pool',evidence_level='verified')
    assert overview_graph(graph)['position']['pool']['evidence_level'] == 'unknown'


@pytest.mark.parametrize('prefix', ['token:','a_token:','valuation:'])
def test_hidden_inspected_component_is_kept_and_direction_preserved(observed, prefix):
    graph = build_wallet_graph(observed)
    selected = prefix + observed['assets'][0]['reserve_address']
    overview = overview_graph(graph, selected)
    assert selected in overview and selected not in overview.graph['hidden_nodes']
    assert nx.has_path(overview, 'position', selected)
    assert not nx.has_path(overview, selected, 'position')


def test_shared_source_remains_one_component_with_all_affected_exposures():
    rpc = SourceRPC()
    rpc.sources = [addr(500)]*3
    observation = fetch(rpc)
    graph = build_wallet_graph(observation)
    shared = graph.graph['source_nodes'][rpc.reserves[0]]
    overview = overview_graph(graph, shared)
    assert len([node for node in overview if node.startswith('source:')]) == 1
    assert overview.nodes[shared]['shared_assets'] == rpc.reserves
    for asset in rpc.reserves:
        assert nx.has_path(overview, 'exposure:'+asset, shared)
    inspection = inspect_wallet_dependency(observation, shared)
    rows = inspection_evidence_rows(graph, inspection)
    assert rows[0]['Affected exposures'] == 'WETH, USDT, GHO'
    assert len(inspection['exposures']) == 3


def test_unknown_sources_remain_dotted_and_unresolved():
    observation = fetch(WalletRPC())
    graph = build_wallet_graph(observation)
    overview = overview_graph(graph)
    sources = [key for key in overview if key.startswith('source:')]
    assert len(sources) == 3
    for source in sources:
        assert node_semantics(source,overview.nodes[source])['category'] == 'Unknown/Boundary'
        assert all(edge['evidence_level']=='unknown' for _,_,edge in overview.in_edges(source,data=True))


@pytest.mark.parametrize('theme', ['dark','light'])
def test_category_text_contrast_and_selection_do_not_change_evidence(observed, theme):
    def luminance(color):
        values = [int(color[i:i+2],16)/255 for i in (1,3,5)]
        values = [value/12.92 if value<=.04045 else ((value+.055)/1.055)**2.4 for value in values]
        return sum(value*weight for value,weight in zip(values,(.2126,.7152,.0722)))
    ink = luminance(get_palette(theme)['ink'])
    for category in CATEGORIES:
        fill = luminance(category_color(category,theme))
        assert (max(ink,fill)+.05)/(min(ink,fill)+.05) >= 4.5
    graph = build_wallet_graph(observed)
    figure = plot_wallet_graph(graph,'pool',list(graph),theme)
    boxes = {trace.name:trace for trace in figure.data if trace.fill=='toself'}
    assert {trace.meta['evidence_level'] for trace in figure.data if trace.meta} == {'verified','modeled'}
    for key in graph:
        assert boxes[key].fillcolor == category_color(node_semantics(key,graph.nodes[key])['category'],theme)
        assert 'Function:' in boxes[key].hovertemplate and 'Identity:' in boxes[key].hovertemplate
    assert {trace.line.dash for trace in figure.data if trace.meta} == {'solid','12px,8px'}


@pytest.mark.parametrize('view', ['Overview','Detailed'])
def test_graph_boxes_do_not_overlap_and_edge_arrows_are_preserved(observed, view):
    original = build_wallet_graph(observed)
    graph = overview_graph(original) if view == 'Overview' else original
    positions,sizes = wallet_layout(graph)
    for u in graph:
        for v in graph:
            if u != v:
                x,y = positions[u]; a,b = positions[v]
                assert abs(x-a) >= (sizes[u][0]+sizes[v][0])/2 or abs(y-b) >= (sizes[u][1]+sizes[v][1])/2
    figure = plot_wallet_graph(graph)
    assert len([a for a in figure.layout.annotations if a.showarrow]) == graph.number_of_edges()
    assert all(edge.meta['source'] in graph and edge.meta['target'] in graph for edge in figure.data if edge.meta)


def test_evidence_rows_reuse_original_inspection_and_preserve_provenance(observed):
    graph = build_wallet_graph(observed)
    inspection = inspect_wallet_dependency(observed,'oracle')
    original = copy.deepcopy(inspection)
    row = inspection_evidence_rows(graph,inspection)[0]
    assert row['Function'] == 'Oracle'
    assert row['Evidence status'] == inspection['component_evidence_status']
    assert row['Observed position'] == observed['wallet_address']
    assert 'pinned RPC' in row['Provenance']
    assert inspection == original
    assert inspection_evidence_rows(graph, {'available':False}) == []


def test_arrow_route_compaction_preserves_turns_and_direction():
    route = [(0,0),(0,1),(0,1),(0,2),(1,2)]
    assert _compact_route(route) == [(0,0),(0,2),(1,2)]
    assert _compact_route([(0,0),(0,1),(0,0)]) == [(0,0),(0,1),(0,0)]


@pytest.mark.parametrize('view', ['Overview','Detailed'])
def test_dual_supply_debt_exposure_has_readable_height_without_node_overlap(view):
    rpc = SourceRPC()
    rpc.supplied[1] = 1000 * 10**6
    graph = build_wallet_graph(fetch(rpc))
    graph = overview_graph(graph) if view=='Overview' else graph
    positions,sizes = wallet_layout(graph)
    assert sizes['exposure:'+rpc.reserves[1]][1] >= 1
    for u in graph:
        for v in graph:
            if u != v:
                x,y = positions[u]; a,b = positions[v]
                assert abs(x-a) >= (sizes[u][0]+sizes[v][0])/2 or abs(y-b) >= (sizes[u][1]+sizes[v][1])/2


def test_historical_overview_keeps_block_timestamp_and_does_not_promote_stress():
    observation = load_historical_example()
    graph = build_wallet_graph(observation)
    overview = overview_graph(graph)
    assert overview.graph['block'] == observation['block']
    assert overview.graph['retrieved_at'] == observation['retrieved_at']
    analysis = analyze_wallet_incident(observation,observation['assets'][0]['reserve_address'],shock=-.3)
    assert not analysis['financial']['coverage']


def test_overview_detailed_and_inspection_leave_incident_and_financial_inputs_unchanged():
    app = analysis_app(str(Path(__file__).resolve().parents[1]/'app.py')).run(timeout=20)
    app.button(key='wallet_load_historical_example').click().run(timeout=20)
    navigate(app, 'Scenarios')
    inputs = (app.selectbox(key='wallet_incident').value,app.selectbox(key='wallet_stress_asset').value,
              app.slider(key='wallet_stress_shock').value,app.selectbox(key='wallet_failure_incident').value)
    metrics = {m.label:m.value for m in app.metric}
    navigate(app, 'Dependencies')
    assert not app.exception and control(app, 'wallet_dependency_view').value=='Overview'
    original = copy.deepcopy(app.session_state.wallet_observation)
    control(app, 'wallet_dependency_view').set_value('Detailed').run(timeout=20)
    selected = 'oracle'
    app.selectbox(key='wallet_dependency_inspection').set_value(selected).run(timeout=20)
    control(app, 'wallet_dependency_view').set_value('Overview').run(timeout=20)
    assert not app.exception and app.session_state.wallet_observation==original
    assert inputs == tuple(app.session_state[key] for key in ('wallet_incident','wallet_stress_asset','wallet_stress_shock','wallet_failure_incident'))
    rows = next(frame.value for frame in app.dataframe if 'Function' in frame.value.columns)
    assert rows.iloc[0]['Function']=='Oracle' and rows.iloc[0]['Affected exposures']=='WETH, USDT, GHO'
    assert any('HISTORICAL / NOT LIVE' in w.value for w in app.warning)
    navigate(app, 'Scenarios')
    assert metrics == {m.label:m.value for m in app.metric}
    control(app, 'application_mode').set_value('SYNTHETIC PLAYGROUND').run(timeout=20)
    assert not any(r.key=='wallet_dependency_view' for r in app.radio)
