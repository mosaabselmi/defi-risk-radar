"""Focused presentation checks; financial engines and live sources are unchanged."""
import pytest
from streamlit.testing.v1 import AppTest

from src.risk import Position
from src.viz import plot_health_curve
from src.wallet_dependencies import analyze_wallet_incident
from tests.test_wallet import WalletRPC, fetch


@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_sensitivity_reserves_space_for_axes_and_selected_annotation(theme):
    figure = plot_health_curve(Position(4, 6000, 2500, 1, .83), -30, theme=theme)
    assert figure.layout.margin.l >= 60 and figure.layout.margin.b >= 60
    assert figure.layout.xaxis.title.standoff >= 12
    assert figure.layout.yaxis.title.standoff >= 12
    assert figure.layout.xaxis.automargin and figure.layout.yaxis.automargin
    assert len(figure.layout.xaxis.tickvals) <= 5
    selected = next(a for a in figure.layout.annotations if a.text.startswith('Selected'))
    assert selected.xref == selected.yref == 'paper'
    assert selected.y > 1 and selected.xanchor == 'center'


def test_wallet_economics_exposes_assumptions_and_break_even_without_actionable_exit():
    observation = fetch(WalletRPC())
    analysis = analyze_wallet_incident(observation, observation['assets'][0]['reserve_address'], shock=-.3)
    app = AppTest.from_string('import streamlit as st\nfrom src.wallet_decision_ui import render_wallet_decision\nrender_wallet_decision(st.session_state.analysis)')
    app.session_state.analysis = analysis
    app.session_state.wallet_decision_notional = 16000.0
    app.run()
    assert not app.exception
    values = {metric.label: metric.value for metric in app.metric}
    assert values == {'Expected downside': '$300.00', 'Assumed avoidance': '100.00%',
                      'Hypothetical cost': '$63.00', 'Break-even probability': '2.10%'}
    assert any('EXECUTION NOT VERIFIED' in warning.value for warning in app.warning)
    assert any('Assumed 24h scenario probability: 10.00%' in caption.value for caption in app.caption)
    assert any('DEBT REPAYMENT CONSTRAINT' in warning.value for warning in app.warning)
    app.number_input(key='wallet_decision_avoidance').set_value(50.5).run()
    assert not app.exception
    assert next(metric.value for metric in app.metric if metric.label == 'Assumed avoidance') == '50.50%'
