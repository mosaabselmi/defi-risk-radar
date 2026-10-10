"""DeFi Risk Radar · transparent research prototype for the Belem challenge."""
import json
from pathlib import Path

import streamlit as st

from src.dependencies import affected_by_failure, build_dependency_graph, graph_provenance
from src.risk import Position, compare_stay_exit, stress_position
from src.sources import DEMO_SNAPSHOT, SourceError, fetch_market_snapshot, validate_snapshot
from src.viz import plot_graph, plot_health_curve

BASE_DIR = Path(__file__).resolve().parent
SAVED = BASE_DIR / 'data' / 'latest_source_snapshot.json'

st.set_page_config(
    page_title='DeFi Risk Radar | Belem Capital', page_icon='◈',
    layout='wide', initial_sidebar_state='expanded',
)
st.markdown('''
<style>
:root {--accent:#43cfbc;--sub:#91a8bd;--ink:#e8f0f7;--line:#263c50;}
.stApp {background:radial-gradient(ellipse at 87% -14%,#14283c 0%,#08121f 43%,#07101b 95%);}
.block-container {max-width:1390px;padding-top:1.25rem;padding-bottom:3rem;}
[data-testid="stSidebar"] {background:#0d1b2b;border-right:1px solid #25394c;}
[data-testid="stSidebar"] .block-container {padding-top:1.5rem;}
[data-testid="stMetric"] {background:#102337;border:1px solid #2a4157;border-radius:13px;
    padding:15px 17px;min-height:117px;}
[data-testid="stMetricLabel"] {color:#a7bdd1;}
[data-testid="stMetricValue"] {color:#f0f6fb;}
[data-testid="stVerticalBlockBorderWrapper"] {background:rgba(15,30,46,.50);
    border-color:#294052 !important;border-radius:16px !important;}
[data-testid="stVerticalBlockBorderWrapper"] > div {padding-bottom:0.3rem;}
[data-testid="stAlert"] {border-radius:11px;}
.stButton > button[kind="primary"] {background:#43cfbc;color:#07232b;border:0;font-weight:750;}
.stButton > button[kind="primary"]:hover {background:#5de5d3;color:#06252a;}
.stButton > button:not([kind="primary"]) {border-color:#345066;}
[data-testid="stSidebar"] label,[data-testid="stSidebar"] p {color:#bdcddd;}
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p {color:#c7d8e7;font-weight:560;}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h3 {font-size:1.03rem;letter-spacing:-.02em;}
[data-testid="stSidebar"] hr {border-color:#294156;}
[data-testid="stMarkdownContainer"] h3 {font-weight:720;letter-spacing:-.025em;}
[data-testid="stPlotlyChart"] {overflow:visible;}
[data-testid="stTabs"] button {font-weight:650;}
[data-testid="stCaptionContainer"] {color:#8da3b8;}
.kicker {letter-spacing:.17em;text-transform:uppercase;color:#53c9c6;
    font-weight:750;font-size:.72rem;}
.hero {font-weight:800;font-size:2.45rem;letter-spacing:-.055em;line-height:1.14;
    margin:.30rem 0 .45rem;color:#edf6fb;}
.sub {color:#a7c0d5;font-size:.99rem; margin-bottom:.9rem;}
.chip {display:inline-block;border:1px solid #255268;background:#0c2937;color:#81ddd8;
    font-size:.68rem;letter-spacing:.055em;font-weight:700;padding:.33rem .70rem;
    border-radius:50px;margin-right:.35rem;margin-bottom:.5rem;}
.chip-muted {border-color:#33465d;background:#142333;color:#bdc8d5;}
.mode-banner {padding:12px 16px;border-radius:11px;font-size:.89rem;
    line-height:1.55;margin:.6rem 0 .45rem;border:1px solid;}
.mode-demo {background:rgba(211,155,63,.10);border-color:rgba(211,155,63,.28);color:#f0c881;}
.mode-live {background:rgba(67,207,188,.10);border-color:rgba(67,207,188,.27);color:#86e4d5;}
.section-eyebrow {font-size:.72rem;text-transform:uppercase;letter-spacing:.12em;
    color:#6ebbc5;font-weight:750;margin:0 0 .3rem;}
.section-title {font-size:1.20rem;font-weight:750;letter-spacing:-.025em;color:#edf5fa;}
.rule-small {color:#91a8bd;font-size:.79rem;line-height:1.56;}
.legend-dot {display:inline-block;width:9px;height:9px;border-radius:50%;
    margin:0 5px 0 11px;vertical-align:middle;}
.legend {color:#a8bfd1;font-size:.76rem;margin:.2rem 0 .55rem;}
.decision {padding:14px 16px;border-radius:12px;border:1px solid;
    margin:.65rem 0 .7rem;}
.decision strong {font-size:1.08rem;letter-spacing:.05em;}
.decision span {display:block;font-size:.82rem;line-height:1.50;margin-top:.3rem;}
.decision-exit {background:rgba(255,113,133,.11);border-color:rgba(255,113,133,.42);color:#ff9daa;}
.decision-stay {background:rgba(67,207,188,.10);border-color:rgba(67,207,188,.37);color:#8ce6d9;}
.decision-unknown {background:rgba(244,188,103,.10);border-color:rgba(244,188,103,.34);color:#f4d49c;}
.smallprint {color:#91a4b7;font-size:.79rem;line-height:1.65;}
</style>
''', unsafe_allow_html=True)

if 'snapshot' not in st.session_state:
    st.session_state.snapshot = DEMO_SNAPSHOT
if 'last_error' not in st.session_state:
    st.session_state.last_error = None

with st.sidebar:
    st.markdown('### 01 / Data source')
    if st.button('↻  Fetch public data', use_container_width=True, type='primary'):
        with st.spinner('Requesting published sources…'):
            try:
                snap = fetch_market_snapshot()
                SAVED.parent.mkdir(exist_ok=True)
                SAVED.write_text(json.dumps(snap, indent=2), encoding='utf-8')
                st.session_state.snapshot = snap
                st.session_state.last_error = None
                st.success('Snapshot fetched and saved.')
            except (SourceError, OSError) as exc:
                st.session_state.last_error = str(exc)
    if SAVED.exists() and st.button('Load saved source snapshot', use_container_width=True):
        try:
            stored = json.loads(SAVED.read_text(encoding='utf-8'))
            validate_snapshot(stored)
            st.session_state.snapshot = stored
            st.session_state.last_error = None
        except (SourceError, OSError, ValueError) as exc:
            st.session_state.last_error = str(exc)
    if st.button('Use illustrative demo', use_container_width=True):
        st.session_state.snapshot = DEMO_SNAPSHOT
        st.session_state.last_error = None
    if st.session_state.last_error:
        st.error('Source refresh failed: ' + st.session_state.last_error)
    st.divider()
    st.markdown('### 02 / Position')
    st.caption('Synthetic investor balances, not a connected wallet.')
    collateral = st.number_input('WETH collateral · units', min_value=0.01, value=4.0, step=0.25)
    debt = st.number_input('USDC debt · units', min_value=0.0, value=6000.0, step=500.0)
    reserve_config = st.session_state.snapshot.get('reserve_configuration')
    if reserve_config:
        lt = reserve_config['reserves']['WETH']['liquidation_threshold_bps'] / 100
        st.caption(f'WETH base liquidation threshold · {lt:g}% '
                   f"(on-chain snapshot, block {reserve_config['block']['number']:,}; no eMode)")
    else:
        lt = st.slider('Assumed liquidation threshold · %', 10, 95, 80)
    st.divider()
    st.markdown('### 03 / Stress scenario')
    incident = st.selectbox('Dependency to disrupt', [
        'WETH Feed', 'USDC Feed', 'Aave Price Oracle', 'Aave Pool',
        'Ethereum', 'WETH', 'USDC',
    ])
    shock = st.slider('Independent WETH price shock · %', -80, 30, -20, step=5)
    prob = st.slider('Assumed event probability · 24h (%)', 0, 100, 10, step=1)
    severity = st.slider('Conditional loss of positive equity · %', 0, 100, 35, step=5)
    exit_cost = st.slider('Exit cost proxy · % of equity', 0.0, 30.0, 2.0, step=0.5)
    fee = st.number_input('Additional fixed exit fee · USD', min_value=0.0, value=10.0, step=5.0)
    st.caption('All scenario probabilities, severity and exit costs are manual assumptions.')

snapshot = st.session_state.snapshot
sourced = snapshot.get('mode') == 'sourced'
prices = snapshot['prices']
p = Position(collateral, debt, prices['WETH']['usd'], prices['USDC']['usd'], lt / 100)
s = stress_position(p, shock)
d = compare_stay_exit(max(p.equity_usd, 0), prob, severity, exit_cost, fee)
g = build_dependency_graph(snapshot)
affected = affected_by_failure(g, incident)

st.markdown('<div class="kicker">BSA EPFL 2026 / BELEM CAPITAL TRACK</div>', unsafe_allow_html=True)
st.markdown('<div class="hero">DeFi Risk Radar <span style="color:#43cfbc">◈</span></div>', unsafe_allow_html=True)
st.markdown('<div class="sub">Trace what a position depends on. Simulate a disruption. Explain the risk decision.</div>', unsafe_allow_html=True)
st.markdown('<span class="chip">AAVE V3 · ETHEREUM</span><span class="chip chip-muted">SINGLE-POSITION RESEARCH</span><span class="chip chip-muted">NO LIVE TRADES</span>', unsafe_allow_html=True)

if sourced:
    retrieved = snapshot['retrieved_at'][:19].replace('T', ' ') + ' UTC'
    st.markdown(f'<div class="mode-banner mode-live"><b>PUBLIC-SOURCE MODE</b> · Registry identities and spot prices retrieved {retrieved}. Oracle-source edges are verified only where block evidence is shown; other relationships and portfolio quantities remain modeled.</div>', unsafe_allow_html=True)
else:
    st.markdown('<div class="mode-banner mode-demo"><b>ILLUSTRATIVE MODE</b> · No live source data loaded. Select <b>Fetch public data</b> in the sidebar to retrieve published registry records and spot-price observations.</div>', unsafe_allow_html=True)
st.caption('Key separation: base reserve parameters and configured oracle sources are sourced only when block evidence is shown. Other relationships, holdings and incident outcomes remain modeled; spot prices are not Aave oracle prices.')

m1, m2, m3, m4 = st.columns(4, gap='medium')
m1.metric('Collateral value', f'${p.collateral_usd:,.0f}', f'{collateral:g} WETH', delta_color='off')
m2.metric('Borrowed value', f'${p.debt_usd:,.0f}', f'{debt:,.0f} USDC', delta_color='off')
m3.metric('Base health factor', '∞' if p.health_factor == float('inf') else f'{p.health_factor:.2f}')
m4.metric('Stressed health factor', '∞' if s.health_factor == float('inf') else f'{s.health_factor:.2f}',
          delta=None if p.health_factor == float('inf') else f'{s.health_factor-p.health_factor:+.2f}',
          delta_color='normal')  # Falling health factor is bad (red), not good (green).

st.write('')
left, right = st.columns([1.35, 1], gap='medium')
with left:
    with st.container(border=True):
        st.markdown('<div class="section-eyebrow">01 / Position structure</div><div class="section-title">Dependency explorer</div>', unsafe_allow_html=True)
        st.plotly_chart(plot_graph(g, incident), use_container_width=True,
                        config={'displayModeBar': False})
        st.markdown('<div class="legend"><span class="legend-dot" style="background:#ff7185;margin-left:0"></span>Selected disruption<span class="legend-dot" style="background:#f4bc67"></span>Upstream dependents<span class="legend-dot" style="background:#43cfbc"></span>Position</div>', unsafe_allow_html=True)
        st.caption('A → B means A depends on B. Oracle-to-feed edges use discovered source mappings when available; all other relationships remain modeled. See Sources & evidence for dated verification.')
with right:
    with st.container(border=True):
        st.markdown('<div class="section-eyebrow">02 / Failure trace</div><div class="section-title">Incident propagation</div>', unsafe_allow_html=True)
        st.markdown(f'**Selected component:** {incident}')
        st.markdown('**Potentially affected:** ' + (', '.join(affected) or 'None identified'))
        if 'Position' in affected:
            st.caption('A dependency path reaches the position. Reachability does not establish a realized financial loss.')
        else:
            st.caption('No dependency path reaches the position in the current graph.')
        st.divider()
        st.markdown('<div class="section-eyebrow">03 / Portfolio response</div><div class="section-title">Stay or exit?</div>', unsafe_allow_html=True)
        c1, c2 = st.columns(2, gap='small')
        c1.metric('Expected loss · 24h', f'${d.stay_expected_loss_usd:,.0f}')
        c2.metric('Assumed exit cost', f'${d.exit_cost_usd:,.0f}')
        if p.equity_usd <= 0:
            recommendation = 'NOT EVALUATED'
            reason = 'Non-positive equity: this simplified equity-loss model is not applicable.'
        elif 'Position' not in affected:
            recommendation = 'NOT EVALUATED'
            reason = 'No modeled dependency path from this failure to the position.'
        else:
            recommendation = d.recommendation
            reason = ('Assumed 24h expected loss exceeds the exit-cost proxy.' if recommendation == 'EXIT'
                      else 'Assumed exit cost is at least as large as modeled expected loss.')
        cls = ('decision-exit' if recommendation == 'EXIT' else
               'decision-stay' if recommendation == 'STAY' else 'decision-unknown')
        st.markdown(f'<div class="decision {cls}"><strong>SCENARIO RESULT · {recommendation}</strong><span>{reason}</span></div>', unsafe_allow_html=True)
        if d.breakeven_probability is None:
            break_even = 'Undefined (zero assumed conditional loss)'
        elif d.breakeven_probability > 1:
            break_even = '>100% · unreachable under assumed loss severity'
        else:
            break_even = f'{100*d.breakeven_probability:.1f}%'
        st.markdown(f'**Break-even event probability:** {break_even}')
        st.caption('Important: the loss estimate is an independent hypothetical scenario; it is not inferred from the selected component failure. No executable exit quote is available.')

st.write('')
tab_stress, tab_sources, tab_method = st.tabs(['Stress-test diagnostics', 'Sources & evidence', 'Model scope & limitations'])
with tab_stress:
    a, b = st.columns([1.25, 1], gap='large')
    with a:
        st.markdown('#### Health factor sensitivity')
        st.plotly_chart(plot_health_curve(p, shock), use_container_width=True,
                        config={'displayModeBar': False})
        if s.debt_usd == 0:
            st.info('No debt: liquidation eligibility is not applicable in this model.')
        elif s.health_factor < 1:
            st.error(f'Stressed HF = {s.health_factor:.3f} < 1. This signals liquidation eligibility, not guaranteed liquidation.')
        else:
            st.success(f'Stressed HF = {s.health_factor:.3f} ≥ 1. No modeled liquidation eligibility under this stress.')
    with b:
        st.markdown('#### Decision arithmetic')
        st.code(f'Base position equity    ${p.equity_usd:,.2f}\n'
                f'Conditional loss proxy ${max(p.equity_usd,0)*severity/100:,.2f}\n'
                f'Assumed probability     {prob}% in 24h\n'
                f'Expected loss           ${d.stay_expected_loss_usd:,.2f}\n'
                f'Exit cost proxy         ${d.exit_cost_usd:,.2f}', language='text')
        st.caption('Price-shock and event-probability controls are independent. An oracle outage does not automatically imply the entered price shock.')
        st.caption('The calculation uses the sourced WETH base threshold when available, otherwise a manual assumption. Aave eMode, account-specific rules, liquidation execution and real exit liquidity remain outside this simplified calculation.')
with tab_sources:
    st.markdown('#### Dependency evidence · model versus verified data')
    st.dataframe(graph_provenance(g), use_container_width=True, hide_index=True)
    st.caption('Registry addresses identify published contracts. Only oracle-to-source edges with block evidence are verified on-chain; all other graph edges remain modeled.')
    if sourced:
        st.markdown('**Published registry addresses**')
        st.dataframe([{'Registry key': k, 'Published address': v} for k, v in snapshot['addresses'].items()],
                     use_container_width=True, hide_index=True)
        st.markdown('**Sources**')
        st.write('Registry:', snapshot['registry_source'])
        st.write('Spot prices:', snapshot['price_source'])
        st.caption('Fetched: ' + snapshot['retrieved_at'] + '. Price feed timestamps are available in the saved JSON.')
        if reserve_config:
            st.markdown('**Base reserve configuration · on-chain snapshot**')
            rows = []
            for asset, config in reserve_config['reserves'].items():
                rows.append({
                    'Asset': asset, 'Decimals': config['decimals'],
                    'LTV (%)': config['ltv_bps'] / 100,
                    'Liquidation threshold (%)': config['liquidation_threshold_bps'] / 100,
                    'Liquidation bonus (%)': (config['liquidation_bonus_bps'] - 10000) / 100
                    if config['liquidation_bonus_bps'] else None,
                    'Reserve factor (%)': config['reserve_factor_bps'] / 100,
                    'Collateral enabled': config['usage_as_collateral_enabled'],
                    'Borrowing enabled': config['borrowing_enabled'],
                    'Stable borrowing enabled (deprecated)': config['stable_borrow_rate_enabled'],
                    'Active': config['is_active'], 'Frozen': config['is_frozen'],
                })
            st.dataframe(rows, hide_index=True)
            block = reserve_config['block']
            st.caption(f"Ethereum chain ID 1 · block {block['number']:,} · hash {block['hash']} · "
                       f"block timestamp {block['timestamp']} · retrieved {reserve_config['retrieved_at']} · "
                       f"RPC host {reserve_config['rpc_host']}")
            st.write('Data provider:', reserve_config['data_provider'])
            st.caption('Saved evidence records the provider relationships and raw calls at this block. '
                       'Caps and pause status are not included. Loading a saved record does not refresh it on-chain.')
            discovery = reserve_config.get('oracle_sources')
            if discovery:
                st.markdown('**Configured oracle sources · same-block observations**')
                st.write('Verified Aave oracle:', discovery['oracle_address'])
                st.dataframe([
                    {'Asset': asset, 'Underlying': source['underlying_address'],
                     'Configured source': source['source_address'],
                     'Direct source configured': int(source['source_address'], 16) != 0}
                    for asset, source in discovery['sources'].items()
                ], hide_index=True)
                st.caption('Source addresses may be adapters or proxies. Their internal dependencies, '
                           'fallback oracle, price answers and freshness are not verified. '
                           'A zero address means no configured direct source, so no verified feed edge is generated.')
            else:
                st.caption('Legacy reserve snapshot: oracle-to-feed relationships remain modeled.')
        else:
            st.caption('Legacy snapshot: no on-chain reserve configuration; the threshold remains a manual assumption.')
    else:
        st.info('Using illustrative local data. Fetch public data to display published addresses and timestamped spot prices.')
with tab_method:
    st.markdown('#### Interpretation guardrails')
    st.markdown('''
- **Illustrative account:** WETH and USDC balances are entered manually; no wallet is connected.
- **Graph discovery:** oracle-to-feed mappings are discovered on-chain when block evidence is present; other relationships remain modeling rules. A path may combine verified and modeled edges.
- **Prices:** in sourced mode, DefiLlama spot prices are **not** Aave's native oracle prices.
- **Health factor:** simplified single-collateral model using the sourced WETH base threshold when available, otherwise an assumption; no account-specific eMode.
- **Loss probability and severity:** user-provided assumptions, not empirical incident estimates.
- **Exit costs:** assumed proxy on positive equity plus a fixed fee, not a quote for executable trades.
- **Recommendation:** a sensitivity comparison for research, not financial advice or an automated trading signal.
''')

st.divider()
st.markdown('<div class="smallprint">DeFi Risk Radar · Belem Capital challenge research prototype. No transactions, wallet keys, paid infrastructure or trade execution. Reserve parameters and configured oracle-source mappings are dated on-chain observations when available; other relationships remain modeled.</div>', unsafe_allow_html=True)
