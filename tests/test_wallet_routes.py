"""Presentation geometry and UI isolation; no financial engine substitutions."""
import copy
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.wallet_dependencies import build_wallet_graph
from src.wallet_replay import load_historical_example
from src.wallet_semantics import overview_graph
from src.wallet_routes import orthogonal_routes, _crosses, _shared_segment
from src.wallet_viz import wallet_layout, plot_wallet_graph
from tests.test_wallet import fetch
from tests.test_wallet_oracles import SourceRPC


@pytest.fixture(params=['Overview', 'Detailed'])
def displayed(request):
    graph = build_wallet_graph(load_historical_example())
    return overview_graph(graph) if request.param == 'Overview' else graph


def test_routes_have_distinct_ports_correct_faces_and_no_node_crossings(displayed):
    positions, sizes = wallet_layout(displayed)
    routes = orthogonal_routes(displayed, positions, sizes)
    endpoints = set()
    for (u,v), route in routes.items():
        for key,point in [(u,route[0]),(v,route[-1])]:
            assert (key,point) not in endpoints
            endpoints.add((key,point))
            x,y = positions[key]; w,h = sizes[key]
            assert abs(point[0]-x) == pytest.approx(w/2) or abs(point[1]-y) == pytest.approx(h/2)
            assert abs(point[0]-x) <= w/2+1e-9 and abs(point[1]-y) <= h/2+1e-9
        for a,b in zip(route,route[1:]):
            assert a[0] == b[0] or a[1] == b[1]
            for key,(x,y) in positions.items():
                w,h = sizes[key]
                # Tiny epsilon excludes floating point boundary artifacts.
                assert not _crosses(a,b,(x-w/2+1e-8,y-h/2+1e-8,x+w/2-1e-8,y+h/2-1e-8)), (u,v,key,a,b)


def test_long_infrastructure_links_have_separate_perimeter_lanes(displayed):
    positions,sizes = wallet_layout(displayed)
    routes = orthogonal_routes(displayed,positions,sizes)
    left = min(x-sizes[key][0]/2 for key,(x,y) in positions.items())
    lanes = [min(p[0] for p in route) for (u,v),route in routes.items() if v == 'ethereum']
    assert len(lanes) >= 2 and len(set(lanes)) == len(lanes)
    assert all(lane < left for lane in lanes)


def test_historical_example_has_no_hidden_overlapping_edge_segments(displayed):
    positions,sizes = wallet_layout(displayed)
    routes = list(orthogonal_routes(displayed,positions,sizes).values())
    for index,route in enumerate(routes):
        for other in routes[index+1:]:
            assert not any(_shared_segment(a,b,c,d)>1e-8 for a,b in zip(route,route[1:])
                           for c,d in zip(other,other[1:]))


@pytest.mark.parametrize('theme', ['light','dark'])
def test_rendering_preserves_identity_direction_evidence_and_arrow_alignment(displayed,theme):
    original = copy.deepcopy(displayed)
    first = plot_wallet_graph(displayed,theme=theme)
    second = plot_wallet_graph(displayed,theme=theme)
    assert first.to_json() == second.to_json()
    assert list(displayed.edges(data=True)) == list(original.edges(data=True))
    assert displayed.graph == original.graph
    edges = [trace for trace in first.data if trace.meta]
    arrows = [a for a in first.layout.annotations if a.showarrow]
    assert len(edges) == len(arrows) == displayed.number_of_edges()
    for trace,arrow in zip(edges,arrows):
        u,v = trace.meta['source'],trace.meta['target']
        level = displayed[u][v]['evidence_level']
        assert trace.meta['evidence_level'] == level
        assert trace.line.dash == {'verified':'solid','modeled':'12px,8px','unknown':'dot'}[level]
        assert (arrow.x,arrow.y) == (trace.x[-1],trace.y[-1])
        assert arrow.x == arrow.ax or arrow.y == arrow.ay


def test_shared_source_has_one_identity_and_separate_converging_ports():
    rpc = SourceRPC()
    rpc.sources = [rpc.sources[0]]*len(rpc.sources)
    graph = build_wallet_graph(fetch(rpc))
    positions,sizes = wallet_layout(graph)
    routes = orthogonal_routes(graph,positions,sizes)
    shared = [key for key in graph if key.startswith('source:shared:')]
    assert len(shared) == 1
    incoming = [route[-1] for (u,v),route in routes.items() if v == shared[0]]
    assert len(incoming) >= 2 and len(incoming) == len(set(incoming))


def test_historical_ui_has_one_primary_warning_and_independent_inspection():
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py')).run(timeout=20)
    app.button(key='wallet_load_historical_example').click().run(timeout=20)
    assert not app.exception
    overview = next(c for c in app.get('container') if c.key == 'wallet_overview')
    assert len(overview.metric) == 4
    assert not overview.warning  # Global banner remains above the overview.
    assert any('HISTORICAL / NOT LIVE' in w.value and 'EXAMPLE ACCOUNT' in w.value for w in app.warning)
    before = {m.label:m.value for m in overview.metric}
    scenario = (app.selectbox(key='wallet_incident').value,app.slider(key='wallet_stress_shock').value)
    app.selectbox(key='wallet_dependency_inspection').set_value('oracle').run(timeout=20)
    app.radio(key='wallet_dependency_view').set_value('Detailed').run(timeout=20)
    assert not app.exception
    overview = next(c for c in app.get('container') if c.key == 'wallet_overview')
    assert {m.label:m.value for m in overview.metric} == before
    assert scenario == (app.selectbox(key='wallet_incident').value,app.slider(key='wallet_stress_shock').value)
    assert any(e.label == 'Graph scope and projection evidence' and not e.proto.expanded for e in app.expander)
