from tests.ui_navigation import control, go_page
"""Offline website journeys, section isolation and durable analytical inputs."""
import copy
import hashlib
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.website import REPOSITORY, SECTIONS
from tests.ui_navigation import navigate

ROOT = Path(__file__).resolve().parents[1]
APP = str(ROOT / 'app.py')


@pytest.fixture(autouse=True)
def no_external_reads(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Website navigation or historical replay made an external request')
    monkeypatch.setattr('requests.sessions.Session.request', forbidden)


def home_app():
    app = AppTest.from_file(APP).run(timeout=20)
    assert not app.exception
    return app


def demo_app():
    app = home_app()
    app.button(key='home_demo').click().run(timeout=20)
    assert not app.exception
    app.switch_page('app_pages/analyze.py')
    return app


def test_home_is_a_real_landing_page_with_functional_actions():
    app = home_app()
    assert app.session_state.website_page == 'Home'
    assert app.button(key='home_analyze').label == 'Analyze a Wallet'
    assert app.button(key='home_demo').label == 'Try Verified Historical Demo'
    assert not app.metric and not app.get('plotly_chart') and not app.text_input
    assert any('See the dependencies' in e.proto.body for e in app.get('html'))
    app.button(key='home_analyze').click().run(timeout=20)
    assert not app.exception
    app.switch_page('app_pages/analyze.py')
    assert app.session_state.website_page == 'Analyze'
    assert app.text_input(key='wallet_address_input').value == ''
    assert not app.metric


@pytest.mark.parametrize('section', SECTIONS)
def test_section_exclusivity_and_historical_provenance(section):
    app = demo_app()
    navigate(app, section)
    assert any('HISTORICAL / NOT LIVE' in w.value for w in app.warning)
    assert app.session_state.wallet_observation['block']['number'] == 26165176
    assert len(app.get('plotly_chart')) == (1 if section == 'Dependencies' else 0)
    sliders = {s.key for s in app.slider}
    assert ('wallet_stress_shock' in sliders) == (section == 'Scenarios')
    assert ('wallet_mitigation_pct' in sliders) == (section == 'Decision')
    if section == 'Overview':
        assert len(app.metric) == 4
        assert [m.value for m in app.metric] == ['1.1949', '0.8365', '$-46,357.80', '$107,333.57']
        assert not app.dataframe
    if section == 'Dependencies':
        assert not app.metric
        assert not any(s.key == 'wallet_failure_incident' for s in app.selectbox)
    if section == 'Evidence':
        assert not app.metric
        assert any(e.label.startswith('Wallet evidence') for e in app.expander)
        assert any('integrity, not independent proof' in c.value for c in app.caption)


def test_home_demo_round_trip_retains_every_analytical_input():
    app = demo_app()
    original = copy.deepcopy(app.session_state.wallet_observation)
    navigate(app, 'Scenarios')
    app.slider(key='wallet_stress_shock').set_value(-20).run(timeout=20)
    app.selectbox(key='wallet_failure_incident').set_value('oracle_disruption').run(timeout=20)
    app.number_input(key='wallet_safety_target').set_value(1.5).run(timeout=20)
    app.number_input(key='wallet_safety_funding').set_value(1234.0).run(timeout=20)
    navigate(app, 'Dependencies')
    app.selectbox(key='wallet_dependency_inspection').set_value('oracle').run(timeout=20)
    control(app, 'wallet_dependency_view').set_value('Detailed').run(timeout=20)
    navigate(app, 'Decision')
    for key, value in {'wallet_decision_probability': 25.0, 'wallet_decision_avoidance': 60.0,
                       'wallet_decision_notional': 100000.0, 'wallet_decision_spread': 8.0,
                       'wallet_decision_slippage': 30.0, 'wallet_decision_fee': 7.0,
                       'wallet_decision_gas': 25.0, 'wallet_mitigation_funding': 30000.0}.items():
        app.number_input(key=key).set_value(value).run(timeout=20)
    app.slider(key='wallet_mitigation_pct').set_value(35).run(timeout=20)
    inputs = {k: app.session_state[k] for k in (
        'wallet_incident', 'wallet_stress_asset', 'wallet_stress_shock', 'wallet_stress_replay',
        'wallet_failure_incident', 'wallet_dependency_inspection', 'wallet_dependency_view',
        'wallet_safety_target', 'wallet_safety_funding', 'wallet_decision_probability',
        'wallet_decision_avoidance', 'wallet_decision_notional', 'wallet_decision_spread',
        'wallet_decision_slippage', 'wallet_decision_fee', 'wallet_decision_gas',
        'wallet_mitigation_funding', 'wallet_mitigation_pct')}
    for page in ('Home', 'About', 'Analyze'):
        go_page(app, page)
        assert not app.exception
    for section in SECTIONS:
        navigate(app, section)
        assert {k: app.session_state[k] for k in inputs} == inputs
        assert app.session_state.wallet_observation == original
        assert app.session_state.wallet_observation_origin == 'verified_historical_example'


def test_about_contact_uses_only_real_repository_and_returns_to_analysis():
    app = demo_app()
    original = copy.deepcopy(app.session_state.wallet_observation)
    go_page(app, 'About')
    assert any(m.value == 'Contact' for m in app.subheader)
    assert any(b.proto.url == REPOSITORY for b in app.get('link_button'))
    assert not app.text_input and not app.metric
    app.button(key='about_analyze').click().run(timeout=20)
    assert not app.exception and app.session_state.wallet_observation == original
    app.switch_page('app_pages/analyze.py')
    assert control(app, 'analysis_section').value == 'Overview'


def test_modes_retain_separate_inputs_and_protected_files():
    paths = [ROOT / 'data/latest_source_snapshot.json', ROOT / 'data/demo/aave_wallet_v1.json']
    hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
    app = demo_app()
    navigate(app, 'Scenarios')
    app.slider(key='wallet_stress_shock').set_value(-10).run(timeout=20)
    observation = copy.deepcopy(app.session_state.wallet_observation)
    control(app, 'application_mode').set_value('SYNTHETIC PLAYGROUND').run(timeout=20)
    app.number_input(key='synthetic_collateral').set_value(8.0).run(timeout=20)
    app.selectbox(key='graph_component').set_value('Ethereum').run(timeout=20)
    go_page(app, 'Home')
    go_page(app, 'Analyze')
    assert app.number_input(key='synthetic_collateral').value == 8.0
    assert app.selectbox(key='graph_component').value == 'Ethereum'
    control(app, 'application_mode').set_value('REAL PORTFOLIO ANALYSIS').run(timeout=20)
    assert app.slider(key='wallet_stress_shock').value == -10
    assert app.session_state.wallet_observation == observation
    assert [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths] == hashes


def test_import_runs_only_on_explicit_form_submission_and_navigation_never_reimports(monkeypatch):
    from tests.test_wallet import WalletRPC, fetch
    observation = fetch(WalletRPC())
    calls = []
    def imported(address):
        calls.append(address)
        return copy.deepcopy(observation)
    monkeypatch.setattr('src.wallet_ui.fetch_wallet_position', imported)
    app = home_app()
    app.button(key='home_analyze').click().run(timeout=20)
    assert not calls
    app.switch_page('app_pages/analyze.py')
    app.text_input(key='wallet_address_input').set_value(observation['wallet_address'])
    next(b for b in app.button if b.label == 'Inspect Aave wallet').click().run(timeout=20)
    assert not app.exception and calls == [observation['wallet_address']]
    app.switch_page('app_pages/analyze.py')
    for section in SECTIONS:
        navigate(app, section)
    assert calls == [observation['wallet_address']]
    assert app.session_state.wallet_observation == observation


@pytest.mark.parametrize('incident', ['oracle_source_outage', 'oracle_outage', 'rpc_outage'])
def test_technical_incidents_remain_unquantified_in_every_section(incident):
    app = demo_app()
    navigate(app, 'Scenarios')
    app.selectbox(key='wallet_incident').set_value(incident).run(timeout=20)
    for section in SECTIONS:
        navigate(app, section)
        assert not app.metric
        assert app.session_state.wallet_analysis_result['financial'] is None
        assert app.session_state.wallet_incident == incident


def test_no_sidebar_or_manual_theme_workaround():
    app = demo_app()
    assert not app.sidebar.children
    assert not app.radio
    assert not any(b.key == 'refresh_theme' for b in app.button)
    assert not any(e.label == 'Advanced appearance' for e in app.expander)
    navigate(app, 'Dependencies')
    assert app.get('plotly_chart')[0].proto.theme == 'streamlit'


def test_native_navigation_binding_supports_url_routes_without_loading_data():
    app = AppTest.from_file(APP)
    app.query_params['website_page'] = 'Analyze'
    app.query_params['analysis_section'] = 'Evidence'
    app.run(timeout=20)
    assert not app.exception
    app.switch_page('app_pages/analyze.py')
    assert app.session_state.website_page == 'Analyze'
    assert control(app, 'analysis_section').value == 'Evidence'
    assert not app.metric and app.text_input(key='wallet_address_input')
