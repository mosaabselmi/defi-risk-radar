from tests.ui_navigation import analysis_app, navigate
"""Presentation-only safeguards for zoom wrapping and graph evidence clarity."""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.viz import MODELED_EDGE_DASH, plot_graph
from src.dependencies import build_dependency_graph
from src.sources import DEMO_SNAPSHOT
from src.wallet_dependencies import build_wallet_graph
from src.wallet_replay import load_historical_example
from src.wallet_viz import plot_wallet_graph


@pytest.mark.parametrize('count', [3, 4])
def test_native_metric_rows_wrap_with_content_height_and_readable_card_width(count):
    app = AppTest.from_string('from src.presentation import metric_cards\n'
                              f'for card in metric_cards({count}, key="test_metrics"):\n'
                              ' card.metric("Marked equity", "$107,333.57", border=True)').run()
    assert not app.exception
    blocks = app.get('flex_container')
    row = blocks[1].proto
    assert row.flex_container.wrap
    assert row.height_config.use_content
    assert all(card.proto.height_config.use_stretch for card in blocks[2:])
    assert all(card.proto.width_config.use_stretch for card in blocks[1:])
    assert [m.value for m in app.metric] == ['$107,333.57'] * count
    assert '@container (max-width:1050px)' in app.get('html')[0].proto.body
    if count == 4:
        assert 'repeat(4,minmax(0,1fr))' in app.get('html')[0].proto.body
        assert 'repeat(2,minmax(0,1fr))' in app.get('html')[0].proto.body


@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_long_gap_modeled_edges_preserve_wallet_directions_and_classifications(theme):
    observation = load_historical_example()
    graph = build_wallet_graph(observation)
    figure = plot_wallet_graph(graph, theme=theme)
    edges = [trace for trace in figure.data if trace.meta]
    assert {(t.meta['source'], t.meta['target']) for t in edges} == set(graph.edges)
    styles = {'verified': 'solid', 'modeled': MODELED_EDGE_DASH, 'unknown': 'dot'}
    for trace in edges:
        assert trace.line.dash == styles[trace.meta['evidence_level']]
    assert MODELED_EDGE_DASH == '12px,8px'
    # Compact two-line token labels retain full component roles in hover.
    labels = [a.text for a in figure.layout.annotations if not a.showarrow]
    assert all('ERC-20 token' not in label for label in labels)
    assert any('underlying token' in t.hovertemplate for t in figure.data if t.fill == 'toself')


def test_synthetic_shared_infrastructure_label_fits_narrow_graph_without_topology_change():
    graph = build_dependency_graph(DEMO_SNAPSHOT)
    figure = plot_graph(graph, 'Ethereum')
    label = next(a for a in figure.layout.annotations if a.text.startswith('Ethereum'))
    assert label.text == 'Ethereum<br>Infrastructure'
    assert all(len(line) <= 14 for line in label.text.split('<br>'))
    assert {(t.meta['source'],t.meta['target']) for t in figure.data if t.meta} == set(graph.edges)


def test_full_dependency_address_is_native_wrapping_copyable_code():
    text = (Path(__file__).resolve().parents[1] / 'src/wallet_inspection_ui.py').read_text(encoding='utf-8')
    assert 'st.code(address, language=None, wrap_lines=True)' in text
    assert 'address[:' not in text


def test_replay_checkbox_explicitly_wraps_in_column_and_no_manual_theme_refresh():
    root = Path(__file__).resolve().parents[1]
    app = analysis_app(str(root / 'app.py')).run(timeout=20)
    app.button(key='wallet_load_historical_example').click().run(timeout=20)
    assert not app.exception
    navigate(app, 'Scenarios')
    assert app.checkbox(key='wallet_stress_replay').proto.wrap
    assert any('HISTORICAL / NOT LIVE' in w.value for w in app.warning)
    assert not any(b.key == 'refresh_theme' for b in app.button)


def test_inspected_oracle_address_preserves_all_characters_and_observation():
    root = Path(__file__).resolve().parents[1]
    observation = load_historical_example()
    app = analysis_app(str(root / 'app.py'))
    app.session_state.wallet_observation = observation
    app.session_state.wallet_stress_replay = True
    app.run(timeout=20)
    navigate(app, 'Dependencies')
    app.selectbox(key='wallet_dependency_inspection').set_value('oracle').run(timeout=20)
    assert not app.exception
    graph = build_wallet_graph(observation)
    assert any(code.value == graph.nodes['oracle']['contract_address'] for code in app.code)
    assert app.session_state.wallet_observation == observation
    assert any('HISTORICAL / NOT LIVE' in warning.value for warning in app.warning)
