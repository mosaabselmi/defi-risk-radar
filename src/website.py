"""Website navigation and editorial presentation; no analytical calculations."""
from pathlib import Path

import streamlit as st

WEBSITE_ROOT = Path(__file__).resolve().parents[1]

from src.presentation import responsive_row

REPOSITORY = 'https://github.com/mosaabselmi/defi-risk-radar'
SECTIONS = ('Overview', 'Dependencies', 'Scenarios', 'Decision', 'Evidence')
SECTION_PURPOSE = {
    'Overview': 'Your observed position, its selected stress and the principal risk.',
    'Dependencies': 'Inspect what this position relies on and the evidence behind each relationship.',
    'Scenarios': 'Explore a failure mechanism or explicitly simulate a collateral or debt asset-price shock.',
    'Decision': 'Compare conditional economics and hypothetical repayment. Execution is not verified.',
    'Evidence': 'Review the captured block, source contracts, calculations and model limitations.',
}


def open_analysis(section='Overview'):
    st.session_state._website_route = 'Analyze'
    st.session_state.application_mode = 'REAL PORTFOLIO ANALYSIS'
    st.session_state.analysis_section = section


def open_demo():
    from src.wallet_ui import _load_historical_example
    _load_historical_example()
    open_analysis()


def open_about():
    st.session_state._website_route = 'About'


def header():
    st.html('<div class="product-wordmark"><span class="brand-mark">◈</span>DeFi Risk Radar'
            '<div class="product-subtitle">EVIDENCE-BACKED DEFI INTELLIGENCE · AAVE V3 / ETHEREUM</div></div>')


def footer():
    st.divider()
    row = st.container(horizontal=True, gap='medium', vertical_alignment='center')
    with row:
        st.caption('DeFi Risk Radar · Independent BSA EPFL hackathon prototype')
        st.link_button('GitHub', REPOSITORY)
        st.button('About / Contact', key='footer_about', on_click=open_about)
    st.caption('Read-only analysis. No sponsor endorsement. Hypothetical scenarios are not investment advice or executable orders.')


def home():
    st.html('''<section class="landing-hero">
      <div class="kicker">DEPENDENCY-AWARE FINANCIAL INTELLIGENCE</div>
      <h1>See the dependencies.<br>Understand the financial risk.</h1>
      <p>Trace an Aave position’s dependencies, explore a market shock and compare
      possible responses — with observed evidence and explicit assumptions.</p>
    </section>''')
    with st.container(horizontal=True, gap='medium'):
        st.button('Analyze a Wallet', type='primary', on_click=open_analysis,
                  key='home_analyze', wrap=True)
        st.button('Try Verified Historical Demo', on_click=open_demo,
                  key='home_demo', wrap=True)
    st.caption('Ethereum mainnet · Aave V3 · Read-only · Historical demo works offline')
    cards = responsive_row('home_capabilities', 280)
    for number, title, body in (
        ('01', 'Discover dependencies', 'See the actual assets, lending components and configured oracle sources behind an observed position.'),
        ('02', 'Simulate financial impact', 'Stress an enabled collateral asset and examine equity, health factor and liquidation eligibility.'),
        ('03', 'Explore decisions', 'Compare assumption-based economics, external funding requirements and hypothetical repayment.'),
    ):
        with cards.container(border=True):
            st.caption(number)
            st.markdown('### ' + title)
            st.write(body)
    st.html('<div class="process-strip"><span>Import</span><b>→</b><span>Discover</span><b>→</b><span>Stress</span><b>→</b><span>Decide</span></div>')
    with st.expander('Evidence, scope and safeguards', expanded=False):
        st.write('Supported scope: Aave V3 on Ethereum mainnet. Public-address inspection requires no wallet connection, signing or private keys.')
        st.write('Verified pinned reads, modeled relationships and unresolved connections remain distinct. '
                 'Scenario losses and economic comparisons depend on explicit assumptions; execution and liquidity are unverified.')
        st.write('The captured historical example is NOT LIVE monitoring. A file-integrity digest is not independent proof of blockchain authenticity.')


def about():
    st.caption('ABOUT / CONTACT')
    st.title('Evidence first. Assumptions in view.')
    st.write('DeFi Risk Radar helps investors investigate what a supported lending position depends on, '
             'how an explicitly selected market scenario affects it and what hypothetical responses would require.')
    cards = responsive_row('about_scope', 320)
    with cards.container(border=True):
        st.subheader('Why dependencies matter')
        st.write('Asset prices, lending contracts, oracle sources and network access affect different parts of a position. '
                 'Technical reachability reveals a possible exposure path; it does not prove a financial loss.')
    with cards.container(border=True):
        st.subheader('Current scope')
        st.write('Aave V3 · Ethereum mainnet. Complete pinned-block wallet observations support collateral stress, '
                 'HF reconciliation, safety-buffer analysis and hypothetical debt repayment.')
    with st.expander('Methodology and read-only safeguards', expanded=False):
        st.write('Observed balances and contract relationships are separate from hypothetical price shocks and user-assumed economics. '
                 'Oracle disruptions, exploits and execution interruptions remain unquantified unless a supported model exists.')
        st.write('No wallet connection, signing, transactions or private keys. No guarantee of feed health, lending-pool liquidity, '
                 'repayment routes or executable withdrawal. Historical replay never becomes a current STAY/EXIT signal.')
    st.subheader('Built for the BSA EPFL hackathon')
    st.write('An independent prototype for the Belem Capital DeFi Risk Dependency Challenge, inspired by an institutional financial-services aesthetic. '
             'This is not an official Belem Capital product or endorsement.')
    st.subheader('Contact')
    st.write('Project source, issues and contact through the public GitHub repository.')
    st.link_button('Visit the project on GitHub', REPOSITORY, type='primary')
    st.button('Return to analysis', on_click=open_analysis, key='about_analyze')
