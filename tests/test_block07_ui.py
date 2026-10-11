from tests.ui_navigation import control, go_page
from tests.ui_navigation import analysis_app, navigate
"""Presentation and mode-isolation regressions; no live calls or source writes."""
import copy
from pathlib import Path
import tomllib

import pytest

from streamlit.testing.v1 import AppTest
from tests.test_wallet import WalletRPC, fetch


APP = str(Path(__file__).resolve().parents[1] / 'app.py')


def test_default_real_mode_has_no_synthetic_controls_or_automatic_fetch(monkeypatch):
    def forbidden(*args,**kwargs):
        raise AssertionError('Opening the UI must not fetch live data')
    monkeypatch.setattr('src.wallet_ui.fetch_wallet_position', forbidden)
    monkeypatch.setattr('src.sources.fetch_market_snapshot', forbidden)
    app = analysis_app(APP).run(timeout=20)
    assert not app.exception
    assert control(app, 'application_mode').value == 'REAL PORTFOLIO ANALYSIS'
    assert any(button.label == 'Use public example' for button in app.button)
    assert not any(field.label == 'WETH collateral · units' for field in app.number_input)
    assert not any(button.label == '↻  Fetch public data' for button in app.button)
    assert not app.metric and not app.get('plotly_chart')


def test_example_prefills_address_without_inventing_observation(monkeypatch):
    def forbidden(*args,**kwargs):
        raise AssertionError('Prefilling an address must not fetch')
    monkeypatch.setattr('src.wallet_ui.fetch_wallet_position', forbidden)
    app = analysis_app(APP).run(timeout=20)
    next(button for button in app.button if button.label == 'Use public example').click().run(timeout=20)
    assert not app.exception
    assert app.text_input(key='wallet_address_input').value == '0x24a416e5c00c7b47a5f8a300984579b53919330b'
    assert not app.metric


def test_successful_import_immediately_collapses_controls_and_shows_risk(monkeypatch):
    observation = fetch(WalletRPC())
    calls = []
    def imported(address):
        calls.append(address)
        return observation
    monkeypatch.setattr('src.wallet_ui.fetch_wallet_position', imported)
    app = analysis_app(APP).run(timeout=20)
    app.text_input(key='wallet_address_input').set_value(observation['wallet_address'])
    next(button for button in app.button if button.label == 'Inspect Aave wallet').click().run(timeout=20)
    assert not app.exception
    assert calls == [observation['wallet_address']]
    controls = next(e for e in app.expander if e.label == 'Change account / public example controls')
    assert not controls.proto.expanded
    assert len(app.get_by_key('wallet_overview').metric) == 4


def test_real_summary_precedes_graph_and_evidence_is_collapsed():
    observation = fetch(WalletRPC())
    app = analysis_app(APP)
    app.session_state.wallet_observation = observation
    app.run(timeout=20)
    assert not app.exception
    overview = app.get_by_key('wallet_overview')
    assert overview.metric[0].label == 'Contract-reported HF'
    assert len(overview.metric) == 4
    assert overview.metric[1].value == '0.9683'
    assert any('HIGH MODELED RISK' in error.value for error in overview.error)
    assert not app.get('plotly_chart')
    navigate(app, 'Dependencies')
    assert app.get('plotly_chart')
    assert not any(field.label == 'WETH collateral · units' for field in app.number_input)
    assert all(not e.proto.expanded for e in app.expander)
    assert observation == app.session_state.wallet_observation


def test_mode_switch_preserves_wallet_and_separate_synthetic_choices():
    observation = fetch(WalletRPC())
    original = copy.deepcopy(observation)
    app = analysis_app(APP)
    app.session_state.wallet_observation = observation
    app.run(timeout=20)
    navigate(app, 'Scenarios')
    app.slider(key='wallet_stress_shock').set_value(-20).run(timeout=20)
    control(app, 'application_mode').set_value('SYNTHETIC PLAYGROUND').run(timeout=20)
    assert not app.exception
    assert not any(field.key == 'wallet_stress_shock' for field in app.slider)
    app.number_input(key='synthetic_collateral').set_value(8.0).run(timeout=20)
    control(app, 'application_mode').set_value('REAL PORTFOLIO ANALYSIS').run(timeout=20)
    assert not app.exception
    assert app.slider(key='wallet_stress_shock').value == -20
    assert app.session_state.wallet_observation == original
    assert any(metric.label == 'Change in marked equity' and metric.value == '$-2,000.00' for metric in app.metric)
    control(app, 'application_mode').set_value('SYNTHETIC PLAYGROUND').run(timeout=20)
    assert app.number_input(key='synthetic_collateral').value == 8.0


def test_reorganized_ui_does_not_hide_historical_safeguards():
    from tests.test_wallet_risk import archived_observation
    app = analysis_app(APP)
    app.session_state.wallet_observation = archived_observation()
    app.run(timeout=20)
    assert not app.exception and not app.metric
    assert any('HISTORICAL WALLET OBSERVATION / NOT LIVE' in warning.value for warning in app.warning)
    navigate(app, 'Scenarios')
    app.checkbox(key='wallet_stress_replay').check().run(timeout=20)
    assert not app.exception and app.metric
    assert any('HISTORICAL / NOT LIVE' in warning.value for warning in app.warning)


@pytest.mark.parametrize('theme', ['dark', 'light'])
def test_wallet_graph_legibility_and_unresolved_source_style(theme):
    from src.wallet_dependencies import build_wallet_graph
    from src.wallet_viz import plot_wallet_graph
    graph = build_wallet_graph(fetch(WalletRPC()))
    original = copy.deepcopy(graph)
    figure = plot_wallet_graph(graph, theme=theme)
    assert all(a.font.size >= 14 for a in figure.layout.annotations if not a.showarrow)
    nodes = {trace.name: trace for trace in figure.data if trace.fill == 'toself'}
    for key, data in graph.nodes(data=True):
        unresolved = key.startswith('source:') and not data.get('contract_address')
        assert nodes[key].line.dash == ('dot' if unresolved else 'solid')
    assert list(graph.edges(data=True)) == list(original.edges(data=True))
    assert list(graph.nodes(data=True)) == list(original.nodes(data=True))


def test_native_information_banner_colors_have_contrast_in_both_themes():
    from tests.test_ui_refinement import contrast
    config = tomllib.loads((Path(APP).parent / '.streamlit/config.toml').read_text(encoding='utf-8'))['theme']
    for colors in (config, config['light']):
        assert contrast(colors['blueTextColor'], colors['blueBackgroundColor']) >= 4.5
