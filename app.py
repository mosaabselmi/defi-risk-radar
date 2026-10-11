"""DeFi Risk Radar · transparent research prototype for the Belem challenge."""
import json
from html import escape
from math import isfinite
from pathlib import Path

import networkx as nx
import streamlit as st

from src.dependencies import affected_by_failure, build_dependency_graph, graph_provenance
from src.risk import (Position, Scenario, ScenarioType, ExecutionAssumptions,
                      assess_scenario, snapshot_quality_issues)
from src.sources import (DEMO_SNAPSHOT, SourceError, fetch_market_snapshot,
                         save_source_snapshot, validate_snapshot)
from src.viz import get_palette, plot_graph, plot_health_curve
from src.wallet_ui import render_wallet_discovery
from src.presentation import metric_cards, responsive_row, theme_chart
from src.website import header, home, about, footer, SECTIONS, SECTION_PURPOSE, WEBSITE_ROOT

BASE_DIR = Path(__file__).resolve().parent
SAVED = BASE_DIR / 'data' / 'latest_source_snapshot.json'

st.set_page_config(
    page_title='DeFi Risk Radar | BSA Hackathon Prototype', page_icon='◈',
    layout='wide', initial_sidebar_state='collapsed',
)
# Streamlit's supported read-only API; no config mutation, DOM scripting or fake selector.
# Native Plotly theming follows frontend changes; paired custom surfaces stay
# readable while Python's read-only context catches up on the next interaction.
theme_mode = st.context.theme.type or 'dark'
palette = get_palette(theme_mode)
presentation_css = '''
<style>
/* Owned product classes and key-scoped layouts; native controls retain theme tokens. */
@media (min-width: 900px) {
    .stAppHeader {
        position: relative !important;
    }

    .stAppHeader .rc-overflow {
        position: absolute !important;
        left: 50% !important;
        top: 8px !important;
        transform: translateX(-50%) !important;
        width: 360px !important;
        min-width: 360px !important;
        margin: 0 !important;
        display: flex !important;
        justify-content: center !important;
    }
}
.st-key-website_content {max-width:1440px;margin-inline:auto;margin-top:-3rem;}
.st-key-home_capabilities {margin-top:1.5rem;align-items:stretch;}
.st-key-home_capabilities > div {height:100%;}
.product-subtitle {font-size:.8rem;opacity:.65;letter-spacing:.06em;margin-top:.25rem;}
.brand-mark {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 52px;
    height: 52px;
    border: 1px solid currentColor;
    border-radius: 13px;
    margin-right: 14px;
    font-size: 1.6rem;
    vertical-align: middle;
}
.st-key-application_mode {margin:.5rem 0 .4rem;}
.st-key-analysis_section {padding-bottom:.8rem;border-bottom:1px solid color-mix(in srgb,currentColor 18%,transparent);margin-bottom:.5rem;}
.st-key-analysis_section button[aria-checked="true"],
.st-key-application_mode button[aria-checked="true"],
.st-key-wallet_dependency_view button[aria-checked="true"] {color:inherit !important;font-weight:700;background:color-mix(in srgb,currentColor 10%,transparent);box-shadow:inset 0 -3px 0 currentColor;}

.product-wordmark {
    font-size: clamp(2rem, 3.4vw, 2.8rem);
    font-weight: 750;
    letter-spacing: -0.04em;
    line-height: 1.2;
    margin: 0 0 1.2rem;
    color: inherit;
}

.landing-hero {max-width:1040px;padding:1.4rem 0 .8rem;}
.landing-hero h1 {font-size:clamp(2.4rem,4.2vw,4.4rem);font-weight:650;letter-spacing:-.045em;line-height:1.12;margin:.6rem 0 1.1rem;color:inherit;}
.landing-hero p {max-width:740px;font-size:1.1rem;line-height:1.65;color:inherit;}
.process-strip {display:flex;flex-wrap:wrap;gap:1.1rem;align-items:center;padding:1.3rem 0;font-size:1rem;font-weight:600;color:inherit;}
.process-strip b {opacity:.5;font-weight:400;}

.kicker {letter-spacing:.14em;text-transform:uppercase;color:inherit;font-weight:600;font-size:.75rem;}
.hero {font-weight:650;font-size:clamp(1.85rem,3vw,2.15rem);letter-spacing:-.035em;line-height:1.18;margin:.25rem 0 .4rem;color:inherit;}
.sub {color:inherit;font-size:1rem;line-height:1.55;margin-bottom:.5rem;}
.chip {display:inline-block;border:1px solid #465364;background:#202734;color:#D5DEE5;font-size:.72rem;letter-spacing:.04em;font-weight:550;padding:.35rem .7rem;border-radius:6px;margin-right:.4rem;margin-bottom:.5rem;}
.chip-muted {color:#A8BAC8;}
.mode-banner {padding:10px 14px;border-radius:8px;font-size:.9rem;line-height:1.5;margin:.4rem 0;border:1px solid;overflow-wrap:anywhere;}
.mode-demo {background:#352F24;border-color:#806D49;color:#E7C995;}
.mode-live {background:#202734;border-color:#62778A;color:#D5DEE5;}
.section-eyebrow {font-size:.75rem;text-transform:uppercase;letter-spacing:.1em;color:inherit;font-weight:600;margin:0 0 .35rem;}
.section-title {font-size:1.2rem;font-weight:600;letter-spacing:-.015em;color:inherit;}
.rule-small {color:inherit;font-size:.85rem;line-height:1.6;}
.legend-dot {display:inline-block;width:9px;height:9px;border-radius:50%;margin:0 5px 0 11px;vertical-align:middle;}
.legend {color:inherit;font-size:.82rem;line-height:1.6;margin:.35rem 0 .6rem;}
.decision {padding:14px;border-radius:8px;border:1px solid;border-left-width:4px;margin:.5rem 0 .65rem;overflow-wrap:anywhere;}
.decision strong {display:block;font-size:clamp(1.2rem,1.6vw,1.7rem);font-weight:700;letter-spacing:.025em;line-height:1.35;}
.decision strong {color:inherit;}
.decision span {display:block;color:inherit;font-size:.95rem;line-height:1.6;margin-top:.65rem;}
.mode-banner strong {color:inherit;}
.decision-exit {background:#39252D;border-color:#DA7D85;color:#F0B0B5;}
.decision-stay {background:#19332C;border-color:#77B394;color:#A5D3B9;}
.decision-unknown {background:#352F24;border-color:#D4AD6C;color:#E7C995;}
.smallprint {color:inherit;font-size:.85rem;line-height:1.65;}
/* Reduce space between navigation and page title */
.stMainBlockContainer {
    padding-top: 1.5rem !important;
}

@media (min-width: 900px) {
    .stMainBlockContainer {
        padding-top: 3.5rem !important;
    }
}
/* Native containers and metrics keep native surfaces and text, including during theme changes. */
</style>
'''
for original, role in {
    '#A8BAC8': 'muted', '#F2F3F1': 'ink', '#BCCAD5': 'accent',
    '#465364': 'border', '#202734': 'surface', '#D5DEE5': 'ink',
    '#352F24': 'monitor_bg', '#806D49': 'amber', '#E7C995': 'monitor_text',
    '#62778A': 'border', '#39252D': 'exit_bg', '#DA7D85': 'red',
    '#F0B0B5': 'exit_text', '#19332C': 'stay_bg', '#77B394': 'stay_border',
    '#A5D3B9': 'stay_text', '#D4AD6C': 'amber',
}.items():
    presentation_css = presentation_css.replace(original, palette[role])
st.markdown(presentation_css, unsafe_allow_html=True)

if 'snapshot' not in st.session_state:
    st.session_state.snapshot = DEMO_SNAPSHOT
if 'last_error' not in st.session_state:
    st.session_state.last_error = None
if 'snapshot_origin' not in st.session_state:
    st.session_state.snapshot_origin = 'session'



pages = {
    'Home': st.Page(WEBSITE_ROOT / 'app_pages/home.py', title='Home', icon=':material/home:', default=True),
    'Analyze': st.Page(WEBSITE_ROOT / 'app_pages/analyze.py', title='Analyze', icon=':material/query_stats:', url_path='analyze'),
    'About': st.Page(WEBSITE_ROOT / 'app_pages/about.py', title='About', icon=':material/info:', url_path='about'),
}
st.session_state._source_snapshot_path = str(SAVED)
page = st.navigation(list(pages.values()), position='top')
# CTA callbacks request the same native navigation used by the header.
requested = st.session_state.pop('_website_route', None)
if not st.session_state.get('_website_navigation_initialized'):
    requested = requested or st.session_state.get('website_page') or st.query_params.get('website_page')
st.session_state._website_navigation_initialized = True
if requested in pages and requested != page.title:
    st.switch_page(pages[requested], query_params=dict(st.query_params))
st.session_state.website_page = page.title
with st.container(key='website_content'):
    header()
    page.run()
    footer()
