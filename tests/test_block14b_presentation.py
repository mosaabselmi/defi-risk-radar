"""Final presentation safeguards; analytical engines remain the source of truth."""
import copy
from pathlib import Path

import pytest

from src.presentation import theme_chart
from src.viz import plot_health_curve
from src.risk import Position
from src.wallet_dependencies import build_wallet_graph
from src.wallet_replay import load_historical_example
from src.wallet_viz import plot_wallet_graph
from tests.test_ui_refinement import contrast


ROOT = Path(__file__).resolve().parents[1]


def test_native_navigation_has_no_sidebar_or_refresh_workaround():
    entry = (ROOT / 'app.py').read_text(encoding='utf-8')
    workspace = (ROOT / 'src/analysis_workspace.py').read_text(encoding='utf-8')
    website = (ROOT / 'src/website.py').read_text(encoding='utf-8')
    assert "position='top'" in entry and 'st.navigation' in entry
    assert 'st.radio' not in entry + workspace + website
    assert 'st.sidebar' not in entry + workspace + website
    assert 'Refresh theme styling' not in entry + workspace + website
    assert 'aria-checked="true"' in entry
    assert 'Playground inputs' in workspace


@pytest.mark.parametrize('background', ['#FFFFFF', '#0D1420'])
def test_graph_theme_adapter_keeps_paths_and_legible_edges(background):
    graph = build_wallet_graph(load_historical_example())
    original = plot_wallet_graph(graph, theme='dark')
    before = copy.deepcopy(original.to_plotly_json())
    adapted = theme_chart(original, graph=True)
    assert original.to_plotly_json() == before
    assert adapted.layout.paper_bgcolor == 'rgba(0,0,0,0)'
    for first, second in zip(original.data, adapted.data):
        assert first.x == second.x and first.y == second.y
        assert first.meta == second.meta
        assert first.line.dash == second.line.dash
        if second.meta:
            assert contrast(second.line.color, background) >= 3
        else:
            assert first.fillcolor == second.fillcolor
    for first, second in zip(original.layout.annotations, adapted.layout.annotations):
        assert (first.x, first.y, first.ax, first.ay) == (second.x, second.y, second.ax, second.ay)
        assert first.text == second.text
        if not second.showarrow:
            assert first.font.color == second.font.color


@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_health_chart_theme_adapter_preserves_samples_and_native_text(theme):
    original = plot_health_curve(Position(4, 6000, 2500, 1, .83), -30, theme=theme)
    before = copy.deepcopy(original.to_plotly_json())
    adapted = theme_chart(original)
    assert original.to_plotly_json() == before
    assert adapted.layout.font.color is None
    for first, second in zip(original.data, adapted.data):
        assert first.x == second.x and first.y == second.y
    for first, second in zip(original.layout.shapes, adapted.layout.shapes):
        assert (first.x0, first.x1, first.y0, first.y1) == (second.x0, second.x1, second.y0, second.y1)
    assert all(a.font.color is None and a.bgcolor == 'rgba(0,0,0,0)' for a in adapted.layout.annotations)


def test_metric_grid_uses_supported_stretch_and_balanced_four_two_columns():
    text = (ROOT / 'src/presentation.py').read_text(encoding='utf-8')
    assert "border=True, height='stretch'" in text
    assert 'columns = 4 if count == 4' in text
    assert 'narrow = 2 if count % 2 == 0' in text
    assert '@container (max-width:1050px)' in text
    assert '@container (max-width:470px)' in text
