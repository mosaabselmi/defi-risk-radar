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
from src.viz import plot_graph, plot_health_curve

BASE_DIR = Path(__file__).resolve().parent
SAVED = BASE_DIR / 'data' / 'latest_source_snapshot.json'

st.set_page_config(
    page_title='DeFi Risk Radar | BSA Hackathon Prototype', page_icon='◈',
    layout='wide', initial_sidebar_state='expanded',
)
st.markdown('''
<style>
/* Native controls use config.toml. These selectors belong to the prototype. */
.kicker {letter-spacing:.14em;text-transform:uppercase;color:#A8BAC8;font-weight:600;font-size:.75rem;}
.hero {font-weight:650;font-size:clamp(1.85rem,3vw,2.4rem);letter-spacing:-.035em;line-height:1.18;margin:.35rem 0 .55rem;color:#F2F3F1;}
.sub {color:#BCCAD5;font-size:1rem;line-height:1.55;margin-bottom:.8rem;}
.chip {display:inline-block;border:1px solid #465364;background:#202734;color:#D5DEE5;font-size:.72rem;letter-spacing:.04em;font-weight:550;padding:.35rem .7rem;border-radius:6px;margin-right:.4rem;margin-bottom:.5rem;}
.chip-muted {color:#A8BAC8;}
.mode-banner {padding:14px 18px;border-radius:8px;font-size:.9rem;line-height:1.6;margin:.6rem 0 .5rem;border:1px solid;overflow-wrap:anywhere;}
.mode-demo {background:#352F24;border-color:#806D49;color:#E7C995;}
.mode-live {background:#202734;border-color:#62778A;color:#D5DEE5;}
.section-eyebrow {font-size:.75rem;text-transform:uppercase;letter-spacing:.1em;color:#A8BAC8;font-weight:600;margin:0 0 .35rem;}
.section-title {font-size:1.2rem;font-weight:600;letter-spacing:-.015em;color:#F2F3F1;}
.rule-small {color:#A8BAC8;font-size:.85rem;line-height:1.6;}
.legend-dot {display:inline-block;width:9px;height:9px;border-radius:50%;margin:0 5px 0 11px;vertical-align:middle;}
.legend {color:#BCCAD5;font-size:.82rem;line-height:1.6;margin:.35rem 0 .6rem;}
.decision {padding:20px;border-radius:8px;border:1px solid;border-left-width:4px;margin:.75rem 0 1rem;overflow-wrap:anywhere;}
.decision strong {display:block;font-size:clamp(1.2rem,1.6vw,1.7rem);font-weight:700;letter-spacing:.025em;line-height:1.35;}
.decision span {display:block;color:#F2F3F1;font-size:.95rem;line-height:1.6;margin-top:.65rem;}
.decision-exit {background:#39252D;border-color:#DA7D85;color:#F0B0B5;}
.decision-stay {background:#19332C;border-color:#77B394;color:#A5D3B9;}
.decision-unknown {background:#352F24;border-color:#D4AD6C;color:#E7C995;}
.smallprint {color:#A8BAC8;font-size:.85rem;line-height:1.65;}
.st-key-dependency_panel,.st-key-response_panel {background:#202734;border-radius:8px;}
</style>
''', unsafe_allow_html=True)

if 'snapshot' not in st.session_state:
    st.session_state.snapshot = DEMO_SNAPSHOT
if 'last_error' not in st.session_state:
    st.session_state.last_error = None
if 'snapshot_origin' not in st.session_state:
    st.session_state.snapshot_origin = 'session'

with st.sidebar:
    st.markdown('### 01 / Data source')
    if st.button('↻  Fetch public data', width='stretch', type='primary'):
        with st.spinner('Requesting published sources…'):
            try:
                snap = fetch_market_snapshot()
                save_source_snapshot(snap, SAVED)
                st.session_state.snapshot = snap
                st.session_state.snapshot_origin = 'fetch'
                st.session_state.last_error = None
                st.success('Snapshot fetched and saved.')
            except (SourceError, OSError, ValueError, TypeError) as exc:
                st.session_state.last_error = str(exc)
    if SAVED.exists() and st.button('Load saved source snapshot', width='stretch'):
        try:
            stored = json.loads(SAVED.read_text(encoding='utf-8'))
            validate_snapshot(stored)
            st.session_state.snapshot = stored
            st.session_state.snapshot_origin = 'archive'
            st.session_state.last_error = None
        except (SourceError, OSError, ValueError) as exc:
            st.session_state.last_error = str(exc)
    if st.button('Use illustrative demo', width='stretch'):
        st.session_state.snapshot = DEMO_SNAPSHOT
        st.session_state.snapshot_origin = 'demo'
        st.session_state.last_error = None
    if st.session_state.last_error:
        st.error('Source refresh failed: ' + st.session_state.last_error)
        st.caption('No replacement data was loaded or saved. Retry Fetch public data, '
                   'load the last saved source snapshot (if available), or use the illustrative demo above.')
    st.divider()
    st.markdown('### 02 / Position')
    st.caption('Synthetic investor balances, not a connected wallet.')
    collateral = st.number_input('WETH collateral · units', min_value=0.0, value=4.0, step=0.25)
    debt = st.number_input('USDC debt · units', min_value=0.0, value=6000.0, step=500.0)
    reserve_config = st.session_state.snapshot.get('reserve_configuration')
    if reserve_config:
        lt = reserve_config['reserves']['WETH']['liquidation_threshold_bps'] / 100
        st.caption(f'WETH base liquidation threshold · {lt:g}% '
                   f"(on-chain snapshot, block {reserve_config['block']['number']:,}; no eMode)")
    else:
        lt = st.slider('Assumed liquidation threshold · %', 10, 95, 80)
    bonus = ((reserve_config['reserves']['WETH']['liquidation_bonus_bps'] - 10000) / 10000
             if reserve_config and reserve_config['reserves']['WETH']['liquidation_bonus_bps'] >= 10000
             else None)
    st.caption('Sourced WETH liquidation bonus · ' + (f'{bonus:.2%}' if bonus is not None else 'unavailable'))
    st.divider()
    st.markdown('### 03 / Economic scenario')
    kind = ScenarioType(st.selectbox('Economic scenario', [item.value for item in ScenarioType],
                                    key='economic_scenario'))
    if kind.oracle_only:
        shock, prob = 0, 0
        st.caption('Oracle-only outage: no price shock, event loss or loss probability is inferred.')
    else:
        shock = st.slider(f'{kind.asset} market-price shock · %', -95, 100,
                          -20 if kind.asset == 'WETH' else -10, step=5, key=f'{kind.asset}_shock')
        prob = st.slider('Assumed scenario probability · 24h (%)', 0, 100, 10,
                         key='scenario_probability')
        st.caption('Probability is a user assumption for this single scenario, not a measured incident rate.')
    liquidation_fraction = None
    if not kind.oracle_only and bonus is not None:
        if st.checkbox('Include assumed liquidation penalty', key='include_liquidation'):
            liquidation_fraction = st.slider('Assumed debt fraction liquidated · %', 0, 100, 50,
                                             key='liquidation_fraction') / 100
            st.caption('Conditional assumption, not an actual close factor or guaranteed liquidation. '
                       'Only incremental bonus cost is added to market loss.')
    st.markdown('#### Graph inspection')
    incident = st.selectbox('Independent graph inspection', [
        'WETH Feed', 'USDC Feed', 'Aave Price Oracle', 'Aave Pool',
        'Ethereum', 'WETH', 'USDC',
    ], key='graph_component')
    st.caption('Graph inspection never changes the economic scenario. Market shocks do not imply an oracle malfunction.')
    st.markdown('#### Assumed execution costs')
    spot = st.session_state.snapshot['prices']
    try:
        default_notional = collateral * spot['WETH']['usd'] + debt * spot['USDC']['usd']
        if not isfinite(default_notional) or default_notional < 0:
            raise ValueError('Invalid position notional')
    except (TypeError, KeyError, ValueError, OverflowError):
        st.error('SCENARIO RESULT · MONITOR: prices or position values are invalid.')
        st.stop()
    notional = st.number_input('Aggregate traded notional · USD (assumption)', min_value=0.0,
                               value=float(default_notional), step=100.0)
    st.caption('Default assumes gross collateral sale plus debt purchase as trading legs. '
               'Edit for the intended unwind; repayment principal is not an execution fee.')
    spread = st.number_input('Spread · basis points (assumption)', min_value=0.0, max_value=10000.0, value=10.0)
    slippage = st.number_input('Slippage · basis points (assumption)', min_value=0.0, max_value=10000.0, value=20.0)
    trading_fee = st.number_input('Trading fee · basis points (assumption)', min_value=0.0, max_value=10000.0, value=5.0)
    gas = st.number_input('Aggregate gas · USD (assumption)', min_value=0.0, value=10.0, step=5.0)
    st.caption('Execution approximation only, not a live or executable quote. '
               'Debt must be repaid or otherwise unwound before a full collateral withdrawal.')

snapshot = st.session_state.snapshot
sourced = snapshot.get('mode') == 'sourced'
archived = sourced and st.session_state.snapshot_origin == 'archive'
quality_issues = snapshot_quality_issues(snapshot)
if archived:
    quality_issues += ('Saved snapshot is a historical replay, not a current refresh.',)
if sourced and st.session_state.last_error:
    quality_issues += ('Latest refresh failed; current source availability is unconfirmed.',)
prices = snapshot['prices']
try:
    p = Position(collateral, debt, prices['WETH']['usd'], prices['USDC']['usd'], lt / 100)
    scenario = Scenario(kind, shock)
    execution = ExecutionAssumptions(notional, spread, slippage, trading_fee, gas)
    d = assess_scenario(p, scenario, prob, execution, liquidation_bonus=bonus,
                        liquidation_fraction=liquidation_fraction,
                        quality_issues=quality_issues)
except (ValueError, TypeError, KeyError) as exc:
    st.error('SCENARIO RESULT · MONITOR: invalid inputs — ' + str(exc))
    st.stop()
s = d.impact.stressed
impact = d.impact
g = build_dependency_graph(snapshot)
affected = affected_by_failure(g, incident)

st.markdown('<div class="kicker">BSA EPFL 2026 / BELEM CAPITAL TRACK</div>', unsafe_allow_html=True)
st.markdown('<div class="hero">DeFi Risk Radar</div>', unsafe_allow_html=True)
st.markdown('<div class="sub">Trace what a position depends on. Simulate a disruption. Explain the risk decision.</div>', unsafe_allow_html=True)
st.caption('Independent BSA hackathon prototype inspired by the sponsor visual identity. Not an official Belem Capital product.')
st.markdown('<span class="chip">AAVE V3 · ETHEREUM</span><span class="chip chip-muted">SINGLE-POSITION RESEARCH</span><span class="chip chip-muted">NO LIVE TRADES</span>', unsafe_allow_html=True)

if sourced:
    historical = archived or bool(quality_issues)
    label = 'HISTORICAL SNAPSHOT / NOT LIVE' if historical else 'PUBLIC-SOURCE MODE · DATED OBSERVATIONS'
    mode_class = 'mode-demo' if historical else 'mode-live'
    block_label = f"Ethereum block {reserve_config['block']['number']:,}" if reserve_config else 'No reserve block evidence'
    st.markdown(f'<div class="mode-banner {mode_class}"><b>{label}</b><br>'
                f'{escape(block_label)} · Original retrieval: {escape(snapshot["retrieved_at"])}<br>'
                'Oracle-source edges are verified only where block evidence is shown; other relationships remain modeled.</div>', unsafe_allow_html=True)
    if historical:
        st.caption('Historical scenario arithmetic remains available. These observations do not support a current STAY or EXIT decision. Fetch public data to reassess current input quality.')
else:
    st.markdown('<div class="mode-banner mode-demo"><b>ILLUSTRATIVE MODE</b> · No live source data loaded. Select <b>Fetch public data</b> in the sidebar to retrieve published registry records and spot-price observations.</div>', unsafe_allow_html=True)
st.caption('Key separation: base reserve parameters and configured oracle sources are sourced only when block evidence is shown. Other relationships, holdings and incident outcomes remain modeled; spot prices are not Aave oracle prices.')

m1, m2, m3, m4 = st.columns(4, gap='medium')
m1.metric('Collateral value', f'${p.collateral_usd:,.0f}', f'{collateral:g} WETH', delta_color='off', border=True)
m2.metric('Borrowed value', f'${p.debt_usd:,.0f}', f'{debt:,.0f} USDC', delta_color='off', border=True)
m3.metric('Base HF', '∞' if p.health_factor == float('inf') else f'{p.health_factor:.2f}', border=True)
m4.metric('Reference HF' if kind.oracle_only else 'Stressed HF',
          '∞' if s.health_factor == float('inf') else f'{s.health_factor:.2f}',
          delta=None if p.health_factor == float('inf') else f'{s.health_factor-p.health_factor:+.2f}',
          delta_color='normal', border=True)  # Falling health factor is bad (red), not good (green).
m3.caption('Health factor before the selected scenario.')
m4.caption('Outage impact is not quantified; observed HF is unchanged.' if kind.oracle_only
           else 'Health factor after the selected asset-price shock.')

st.write('')
left, right = st.columns([1.2, 1], gap='medium')
with left:
    with st.container(border=True, key='dependency_panel'):
        st.markdown('<div class="section-eyebrow">01 / Position structure</div><div class="section-title">Dependency explorer</div>', unsafe_allow_html=True)
        graph_focus = kind.asset + ' Feed' if kind.oracle_only else incident
        st.plotly_chart(plot_graph(g, graph_focus), width='stretch',
                        config={'displayModeBar': False})
        st.markdown('<div class="legend"><span class="legend-dot" style="background:#DA7D85;margin-left:0"></span>Graph focus<span class="legend-dot" style="background:#D4AD6C"></span>Upstream dependents<span class="legend-dot" style="background:#BCCAD5"></span>Position</div>', unsafe_allow_html=True)
        st.caption('A → B means A depends on B. Oracle-to-feed edges use discovered source mappings when available; all other relationships remain modeled. See Sources & evidence for dated verification.')
        if kind.oracle_only:
            st.caption(f'Graph displays the outage scenario focus: {graph_focus}. '
                       f'The independent inspection control remains {incident}; its trace is shown alongside.')
with right:
    with st.container(border=True, key='response_panel'):
        st.markdown('<div class="section-eyebrow">03 / Portfolio response</div><div class="section-title">Stay, exit or monitor?</div>', unsafe_allow_html=True)
        recommendation, reason = d.recommendation, d.rationale
        cls = ('decision-exit' if recommendation == 'EXIT' else
               'decision-stay' if recommendation == 'STAY' else 'decision-unknown')
        st.markdown(f'<div class="decision {cls}"><strong>SCENARIO RESULT · {recommendation}</strong><span>{escape(reason)}</span></div>', unsafe_allow_html=True)
        if quality_issues:
            st.warning('MONITOR / INSUFFICIENT CURRENT DATA')
        c1, c2 = st.columns(2, gap='small')
        c1.metric('Expected downside', 'N/A' if d.expected_downside_usd is None
                  else f'${d.expected_downside_usd:,.0f}', border=True)
        c1.caption('Not quantified: an oracle outage alone does not establish a dollar loss.'
                   if kind.oracle_only else '24h assumption × conditional downside; applied once.')
        c2.metric('Exit cost', f'${d.exit_cost_usd:,.0f}', border=True)
        c2.caption('Execution assumptions only; not an executable quote.')
        if d.breakeven_probability is None:
            break_even = 'Not applicable (unquantified or zero downside)'
        elif d.breakeven_probability > 1:
            break_even = '>100% · unreachable under this conditional downside'
        else:
            break_even = f'{100*d.breakeven_probability:.1f}%'
        st.markdown(f'**Break-even 24h scenario probability:** {break_even}')
        st.caption('This comparison is conditional on the entered execution assumptions. Full withdrawal requires '
                   'repayment or an unwind of Aave debt first; available funding, liquidity and transaction ordering are not verified.')
        for issue in d.issues:
            st.caption('Input quality: ' + issue)
        st.divider()
        st.markdown('<div class="section-eyebrow">02 / Failure trace</div><div class="section-title">Incident propagation</div>', unsafe_allow_html=True)
        st.markdown(f'**Independent graph inspection:** {incident}')
        st.markdown('**Potentially affected:** ' + (', '.join(affected) or 'None identified'))
        if 'Position' in affected:
            st.caption('A dependency path reaches the position. Reachability does not establish a realized financial loss.')
        else:
            st.caption('No dependency path reaches the position in the current graph.')
        if kind.oracle_only:
            source_node = kind.asset + ' Feed'
            paths = list(nx.all_simple_paths(g, 'Position', source_node))
            st.markdown(f'**Economic outage scenario:** {source_node}')
            st.markdown('**Affected paths:** ' + ('; '.join(' → '.join(path) for path in paths)
                                                or 'No configured direct-source path identified.'))
            st.caption('These paths combine source evidence and modeled relationships. '
                       'No realized price change, dollar loss or liquidation is inferred from an outage.')
        else:
            st.caption('Graph inspection is separate from this market shock; no oracle malfunction is assumed.')

st.write('')
tab_stress, tab_sources, tab_method = st.tabs(['Stress-test diagnostics', 'Sources & evidence', 'Model scope & limitations'])
with tab_stress:
    a, b = st.columns([1.25, 1], gap='large')
    with a:
        st.markdown('#### Market-price health factor sensitivity')
        st.plotly_chart(plot_health_curve(p, shock, asset=kind.asset,
                                          selected_is_scenario=not kind.oracle_only), width='stretch',
                        config={'displayModeBar': False})
        if kind.oracle_only:
            st.info('Oracle outage: plotted market sensitivity is illustrative only. Reference prices and HF are unchanged observations, not a prediction of outage impact.')
        if s.debt_usd == 0:
            st.info('No debt: liquidation eligibility is not applicable in this model.')
        elif s.health_factor < 1:
            st.error(f'Modeled HF = {s.health_factor:.3f} < 1. This signals modeled liquidation eligibility, not guaranteed liquidation. Aave uses its own oracle prices.')
        else:
            st.success(f'Stressed HF = {s.health_factor:.3f} ≥ 1. No modeled liquidation eligibility under this stress.')
    with b:
        st.markdown('#### Financial-impact breakdown')
        def money(value):
            return 'Unquantified' if value is None else f'${value:,.2f}'
        st.code(f'Economic scenario        {kind.value}\n'
                f'Base collateral          {money(p.collateral_usd)}\n'
                f'Base USDC debt           {money(p.debt_usd)}\n'
                f'Base equity              {money(p.equity_usd)}\n'
                f'{"Reference" if kind.oracle_only else "Stressed"} equity         {money(s.equity_usd)}\n'
                f'Market P&L               {money(impact.pnl_usd)}\n'
                f'Collateral market P&L    {money(None if kind.oracle_only else s.collateral_usd - p.collateral_usd)}\n'
                f'Debt value change        {money(None if kind.oracle_only else s.debt_usd - p.debt_usd)}\n'
                f'Price downside           {money(impact.price_downside_usd)}\n'
                f'Assumed bonus penalty    {money(impact.liquidation_penalty_usd)}\n'
                f'Conditional downside     {money(impact.conditional_downside_usd)}\n'
                f'Conditional favorable    {money(impact.favorable_usd)}\n'
                f'Expected downside · 24h  {money(d.expected_downside_usd)}\n'
                f'Expected favorable · 24h {money(d.expected_favorable_usd)}', language='text')
        if not kind.oracle_only:
            st.caption(f'Expected downside = {prob}% assumed scenario probability × conditional downside, applied exactly once. '
                       'A USDC price decline reduces the USD value of this debt; a rise increases it.')
        if impact.liquidation_eligible and not kind.oracle_only and impact.liquidation_penalty_usd is None:
            st.warning('Liquidation damage is unquantified. Conditional and expected downside shown are price-only lower bounds.')
        if liquidation_fraction is not None:
            st.caption(f'Assumed fraction of stressed debt liquidated: {liquidation_fraction:.0%}. '
                       'Incremental bonus cost only; principal repayment is offset by debt reduction. '
                       'Collateral availability caps the estimate. This is not guaranteed damage or a liquidation execution simulation.')
        st.caption('Sourced WETH liquidation bonus: ' + (f'{bonus:.2%}' if bonus is not None else 'unavailable'))
        st.markdown('#### Execution-cost components · assumptions')
        st.dataframe([{'Component': name, 'USD': value} for name, value in execution.components.items()], hide_index=True)
        st.caption(f'Aggregate assumed trade notional: ${notional:,.2f}; cost = notional × '
                   f'({spread:g} + {slippage:g} + {trading_fee:g}) / 10,000 + ${gas:,.2f} gas. '
                   'No live slippage or executable quote is supplied.')
        st.caption('Decision data policy: prices and reserve block at most 1h old, spot confidence ≥0.9, '
                   'future timestamp tolerance 60s. Demo, missing reserve data and unreliable observations return MONITOR.')
        st.caption('The calculation uses the sourced WETH base threshold when available, otherwise a manual assumption. Aave eMode, account-specific rules, liquidation execution and real exit liquidity remain outside this simplified calculation.')
with tab_sources:
    st.markdown('#### Dependency evidence · model versus verified data')
    st.dataframe([
        {'Dependent': u, 'Depends on': v,
         'Evidence': f"Verified · block {meta['block_number']:,}" if meta.get('verified_onchain') else 'Modeled'}
        for u, v, meta in g.edges(data=True)
    ], width='stretch', hide_index=True)
    with st.expander('Full relationship evidence and original snapshot'):
        st.dataframe(graph_provenance(g), width='stretch', hide_index=True)
        st.json(snapshot, expanded=False)
        st.caption('Original timestamps, block hash, raw calls, addresses, reserve flags and price metadata are preserved here without refreshing or promoting their provenance.')
    st.caption('Registry addresses identify published contracts. Only oracle-to-source edges with block evidence are verified on-chain; all other graph edges remain modeled.')
    if sourced:
        st.markdown('**Published registry addresses**')
        st.dataframe([{'Registry key': k, 'Published address': v} for k, v in snapshot['addresses'].items()],
                     width='stretch', hide_index=True)
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
                    'LT (%)': config['liquidation_threshold_bps'] / 100,
                    'Bonus (%)': (config['liquidation_bonus_bps'] - 10000) / 100
                    if config['liquidation_bonus_bps'] else None,
                    'Reserve factor (%)': config['reserve_factor_bps'] / 100,
                    'Collateral enabled': config['usage_as_collateral_enabled'],
                    'Borrowing enabled': config['borrowing_enabled'],
                    'Stable borrowing enabled (deprecated)': config['stable_borrow_rate_enabled'],
                    'Active': config['is_active'], 'Frozen': config['is_frozen'],
                })
            st.dataframe([{key: row[key] for key in ('Asset', 'LTV (%)', 'LT (%)', 'Bonus (%)')}
                          for row in rows], width='stretch', hide_index=True)
            st.caption('LT = liquidation threshold; bonus is the incremental liquidation bonus. Full reserve fields and flags remain in the evidence expander.')
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
                    {'Asset': asset,
                     'Configured source': source['source_address'],
                     'Direct source': int(source['source_address'], 16) != 0}
                    for asset, source in discovery['sources'].items()
                ], width='stretch', hide_index=True)
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
- **Economic scenarios:** asset-specific market shocks change marked prices; oracle outages do not imply price crashes or quantified dollar losses.
- **Loss probability:** user-assumed probability over 24h applied once to conditional downside; favorable outcomes are separate.
- **Liquidation:** eligibility is modeled; optional bonus penalty requires an assumed debt fraction and does not imply guaranteed execution.
- **Exit costs:** assumed traded notional × spread/slippage/fee basis points plus gas; not an executable quote. Debt repayment or unwind precedes full withdrawal.
- **Recommendation:** a sensitivity comparison for research, not financial advice or an automated trading signal.
''')

st.divider()
st.markdown('<div class="smallprint">DeFi Risk Radar · Belem Capital challenge research prototype. No transactions, wallet keys, paid infrastructure or trade execution. Reserve parameters and configured oracle-source mappings are dated on-chain observations when available; other relationships remain modeled.</div>', unsafe_allow_html=True)
