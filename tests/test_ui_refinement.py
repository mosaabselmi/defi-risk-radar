"""Rendering invariants: topology, evidence, boundaries and theme contrast."""
import copy
import json
from pathlib import Path
import re
from types import SimpleNamespace

import networkx as nx
import pytest

from src.dependencies import build_dependency_graph
from src.sources import DEMO_SNAPSHOT
from src.risk import Position
from src.viz import (LAYOUT, NODE_SIZES, edge_route, get_palette,
                     plot_graph, plot_health_curve)
from test_reserve_configuration import rpc, addresses
from test_block03 import fresh_snapshot


def inside(node, point, tolerance=0):
    """Independent rounded-box membership check for arrow and clearance tests."""
    x, y = LAYOUT[node]
    width, height = NODE_SIZES[node]
    px, py = abs(point[0]-x), abs(point[1]-y)
    if px > width/2+tolerance or py > height/2+tolerance:
        return False
    if px <= width/2-.09 or py <= height/2-.09:
        return True
    return ((px - (width/2-.09))/.09)**2 + ((py - (height/2-.09))/.09)**2 <= 1+tolerance


@pytest.mark.parametrize('theme', ['dark', 'light'])
@pytest.mark.parametrize('focus', ['WETH Feed', 'USDC Feed', 'Ethereum'])
def test_graph_preserves_topology_direction_and_highlighting(fresh_snapshot, theme, focus):
    graph = build_dependency_graph(fresh_snapshot)
    original = copy.deepcopy(graph)
    figure = plot_graph(graph, focus, theme)
    edges = [trace for trace in figure.data if trace.meta]
    assert {(trace.meta['source'], trace.meta['target']) for trace in edges} == set(graph.edges)
    arrows = [annotation for annotation in figure.layout.annotations if annotation.showarrow]
    assert len(arrows) == len(graph.edges)
    for trace, arrow in zip(edges, arrows):
        source, target = trace.meta['source'], trace.meta['target']
        assert tuple(zip(trace.x, trace.y)) == tuple(edge_route(source, target))
        assert (arrow.x, arrow.y) == (trace.x[-1], trace.y[-1])
        # Arrow points along the final segment toward its dependent's dependency.
        dx, dy = trace.x[-1]-trace.x[-2], trace.y[-1]-trace.y[-2]
        assert (arrow.x-arrow.ax)*dx + (arrow.y-arrow.ay)*dy > 0
        verified = graph.edges[source, target]['verified_onchain']
        assert trace.line.dash == ('solid' if verified else 'dash')
        assert trace.meta['verified'] == verified
    nodes = {trace.name: trace for trace in figure.data if trace.fill == 'toself'}
    assert set(nodes) == set(graph.nodes)
    palette = get_palette(theme)
    for node, trace in nodes.items():
        assert len(trace.x) > 4  # Curved corners rather than circular point markers.
        assert trace.mode == 'lines'
        assert trace.line.color == (palette['red'] if node == focus else palette['amber']
                                   if node in nx.ancestors(graph, focus) else palette['accent']
                                   if node == 'Position' else palette['border'])
        assert trace.hoveron == 'fills'
    assert nx.utils.graphs_equal(graph, original)
    assert figure.layout.height < 355


def test_demo_edges_are_all_modeled():
    figure = plot_graph(build_dependency_graph(DEMO_SNAPSHOT), 'WETH Feed')
    edges = [trace for trace in figure.data if trace.meta]
    assert edges and all(trace.line.dash == 'dash' for trace in edges)
    assert all(not trace.meta['verified'] for trace in edges)


def test_routes_touch_boundaries_and_avoid_every_unrelated_node():
    graph = build_dependency_graph(DEMO_SNAPSHOT)
    for source, target in graph.edges:
        route = edge_route(source, target)
        for node, endpoint in [(source, route[0]), (target, route[-1])]:
            x, y = LAYOUT[node]
            dx, dy = endpoint[0]-x, endpoint[1]-y
            assert inside(node, (x+.999*dx, y+.999*dy))
            assert not inside(node, (x+1.001*dx, y+1.001*dy))
        for start, end in zip(route, route[1:]):
            for step in range(1, 100):
                point = (start[0]+(end[0]-start[0])*step/100,
                         start[1]+(end[1]-start[1])*step/100)
                assert not any(inside(node, point) for node in graph if node not in (source, target))


def test_routes_have_no_crossings_between_unrelated_edges():
    graph = build_dependency_graph(DEMO_SNAPSHOT)
    def cross(a, b, c):
        return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])
    for i, (u, v) in enumerate(graph.edges):
        for s, t in list(graph.edges)[i+1:]:
            if {u, v} & {s, t}:
                continue
            for a, b in zip(edge_route(u, v), edge_route(u, v)[1:]):
                for c, d in zip(edge_route(s, t), edge_route(s, t)[1:]):
                    assert not (cross(a, b, c)*cross(a, b, d) < 0
                                and cross(c, d, a)*cross(c, d, b) < 0)


def contrast(first, second):
    def luminance(color):
        channels = [int(color[i:i+2], 16)/255 for i in (1, 3, 5)]
        linear = [value/12.92 if value <= .04045 else ((value+.055)/1.055)**2.4
                  for value in channels]
        return sum(value*weight for value, weight in zip(linear, (.2126, .7152, .0722)))
    values = sorted([luminance(first), luminance(second)])
    return (values[1]+.05)/(values[0]+.05)


@pytest.mark.parametrize('theme', ['dark', 'light'])
def test_hover_and_status_text_contrast_in_both_themes(theme):
    palette = get_palette(theme)
    assert contrast(palette['ink'], palette['surface']) >= 4.5
    for status in ('stay', 'exit', 'monitor'):
        assert contrast(palette[status+'_text'], palette[status+'_bg']) >= 4.5
        assert contrast(palette['ink'], palette[status+'_bg']) >= 4.5
    graph = plot_graph(build_dependency_graph(DEMO_SNAPSHOT), 'WETH Feed', theme)
    assert graph.layout.hoverlabel.bgcolor == palette['surface']
    assert graph.layout.hoverlabel.font.color == palette['ink']
    curve = plot_health_curve(Position(4, 6000, 2500, 1, .83), -30, theme=theme)
    assert curve.layout.font.color == palette['ink']
    assert curve.layout.hoverlabel.font.color == palette['ink']


def test_native_theme_refresh_preserves_economic_values_and_choice(monkeypatch):
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    mode = SimpleNamespace(type='dark')
    monkeypatch.setattr(type(st.context), 'theme', property(lambda self: mode))
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py'))
    app.session_state['application_mode'] = 'SYNTHETIC PLAYGROUND'
    app.run(timeout=20)
    assert not app.exception
    original = [metric.value for metric in app.metric]
    mode.type = 'light'  # Native browser context after choosing Settings > Theme > Light.
    app.button(key='refresh_theme').click().run(timeout=20)
    assert not app.exception
    assert [metric.value for metric in app.metric] == original
    assert '#172330' in app.markdown[0].value
    assert '#F2F3F1' not in app.markdown[0].value
    app.selectbox(key='graph_component').set_value('USDC Feed').run(timeout=20)
    assert '#172330' in app.markdown[0].value
    assert app.session_state['snapshot'] == DEMO_SNAPSHOT


def test_primary_hierarchy_descends_to_feeds_and_shared_infrastructure():
    assert (LAYOUT['Position'][1] > LAYOUT['Aave Pool'][1]
            > LAYOUT['Aave Price Oracle'][1] > LAYOUT['WETH Feed'][1]
            > LAYOUT['Ethereum'][1])
    assert LAYOUT['WETH Feed'][1] == LAYOUT['USDC Feed'][1]
    figure = plot_graph(build_dependency_graph(DEMO_SNAPSHOT), 'WETH Feed')
    assert any('Ethereum<br>Shared infrastructure' == annotation.text
               for annotation in figure.layout.annotations)
    # No rectangular node footprints overlap, including two-line labels.
    for index, first in enumerate(LAYOUT):
        for second in list(LAYOUT)[index+1:]:
            dx = abs(LAYOUT[first][0] - LAYOUT[second][0])
            dy = abs(LAYOUT[first][1] - LAYOUT[second][1])
            assert (dx > (NODE_SIZES[first][0] + NODE_SIZES[second][0])/2
                    or dy > (NODE_SIZES[first][1] + NODE_SIZES[second][1])/2)


@pytest.mark.parametrize('theme', ['dark', 'light'])
@pytest.mark.parametrize('asset,shock', [('WETH', -30), ('USDC', -10)])
def test_sensitivity_explains_threshold_and_selected_result_without_changing_samples(theme, asset, shock):
    from src.risk import stress_position
    position = Position(4, 6000, 2500, 1, .83)
    curve = plot_health_curve(position, shock, asset=asset, theme=theme)
    for x, health in zip(curve.data[0].x, curve.data[0].y):
        expected = stress_position(position, x if asset == 'WETH' else 0,
                                   x if asset == 'USDC' else 0).health_factor
        assert health == expected
    selected = stress_position(position, shock if asset == 'WETH' else 0,
                               shock if asset == 'USDC' else 0).health_factor
    assert curve.data[1].x == (shock,)
    assert curve.data[1].y == (selected,)
    assert any('HF = 1' in annotation.text for annotation in curve.layout.annotations)
    assert any(f'Selected {asset} shock: {shock:+g}% · HF {selected:.2f}' == annotation.text
               for annotation in curve.layout.annotations)
    assert any(shape.y0 == 0 and shape.y1 == 1 for shape in curve.layout.shapes)
    assert any(shape.x0 == shock and shape.x1 == shock for shape in curve.layout.shapes)
    assert 1 < curve.layout.yaxis.range[1]
    assert selected < curve.layout.yaxis.range[1]
    palette = get_palette(theme)
    # Matching opaque surfaces stay legible even when Python's theme context temporarily lags.
    assert curve.layout.paper_bgcolor == palette['surface']
    assert contrast(curve.layout.font.color, curve.layout.paper_bgcolor) >= 4.5
    graph = plot_graph(build_dependency_graph(DEMO_SNAPSHOT), 'USDC Feed', theme)
    assert graph.layout.paper_bgcolor == palette['surface']
    assert all(contrast(annotation.font.color, palette['surface']) >= 4.5
               for annotation in graph.layout.annotations if not annotation.showarrow)


@pytest.mark.parametrize('theme', ['dark', 'light', None])
def test_native_page_text_and_metrics_are_not_overridden_by_stale_python_theme(monkeypatch, theme):
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    monkeypatch.setattr(type(st.context), 'theme', property(lambda self: SimpleNamespace(type=theme)))
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py'))
    app.session_state['application_mode'] = 'SYNTHETIC PLAYGROUND'
    app.run(timeout=20)
    assert not app.exception
    css = app.markdown[0].value
    for selector in ('hero', 'kicker', 'sub', 'section-title', 'section-eyebrow', 'smallprint'):
        rule = re.search(r'\.' + selector + r' \{([^}]+)\}', css).group(1)
        assert 'color:inherit' in rule
    assert '.st-key-dependency_panel' not in css
    assert '.st-key-response_panel' not in css
    assert 'stMetric' not in css
    for rule in re.findall(r'\.decision-(?:stay|exit|unknown) \{([^}]+)\}', css):
        background = re.search(r'background:(#[A-Fa-f0-9]+)', rule).group(1)
        text = re.search(r'(?:^|;)color:(#[A-Fa-f0-9]+)', rule).group(1)
        assert contrast(text, background) >= 4.5
    assert '.decision span {display:block;color:inherit' in css
    captions = [caption.value for caption in app.caption]
    assert any(caption.startswith('Solid edge —') for caption in captions)
    assert any(caption.startswith('Dashed edge —') for caption in captions)
    assert any(caption.startswith('Highlighted node —') for caption in captions)
    for chart in app.get('plotly_chart'):
        assert chart.proto.theme == ''  # Explicit Plotly palette cannot be overridden by Streamlit.
        specification = json.loads(chart.proto.spec)
        assert specification['layout']['paper_bgcolor'] == get_palette(theme)['surface']
    assert list(app.table[0].value.columns) == ['Component', 'USD']
    values = dict(zip(app.table[0].value['Component'], app.table[0].value['USD']))
    assert values['Base collateral'] == '$10,000.00'
    assert values['Base USDC debt'] == '$6,000.00'
    assert values['Base equity'] == '$4,000.00'
    assert values['Price downside'] == '$2,000.00'
    assert values['Expected downside · 24h'] == '$200.00'
