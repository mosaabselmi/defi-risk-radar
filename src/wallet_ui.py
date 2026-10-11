"""Session-only wallet evidence and separate stress panel; no synthetic integration."""
from datetime import datetime, timezone

import streamlit as st

from src.sources import SourceError
from src.wallet import WalletProviderError, fetch_wallet_position, wallet_quality_issues
from src.wallet_dependencies import analyze_wallet_incident
from src.wallet_viz import plot_wallet_graph
from src.wallet_semantics import overview_graph, node_semantics, category_color
from src.viz import get_palette
from src.wallet_decision_ui import render_wallet_decision
from src.wallet_mitigation_ui import render_wallet_mitigation
from src.wallet_safety_ui import render_wallet_safety
from src.wallet_inspection_ui import select_dependency, render_dependency_inspection
from src.wallet_replay import load_historical_example, EXPECTED_SHA256, FIXTURE_VERSION
from src.presentation import metric_cards


PUBLIC_EXAMPLE = '0x24a416e5c00c7b47a5f8a300984579b53919330b'


def _use_public_example():
    st.session_state.wallet_address_input = PUBLIC_EXAMPLE


def _load_historical_example():
    # Explicit user selection only; never invoked as a live-import fallback.
    st.session_state.wallet_observation = None
    st.session_state.wallet_observation_origin = None
    st.session_state.wallet_error = None
    st.session_state.wallet_error_kind = None
    try:
        observation = load_historical_example()
        st.session_state.wallet_observation = observation
        st.session_state.wallet_observation_origin = 'verified_historical_example'
        st.session_state.wallet_address_input = observation['wallet_address']
        st.session_state.wallet_stress_replay = True
        st.session_state.wallet_incident = 'market_price'
        st.session_state.wallet_stress_shock = -30
        st.session_state.wallet_stress_asset = next(a['reserve_address'] for a in observation['assets']
                                                  if a['collateral_enabled'] and int(a['supplied_raw']))
    except SourceError as exc:
        st.session_state.wallet_error = str(exc)
        st.session_state.wallet_error_kind = 'Historical example integrity/evidence validation failure'


def _render_graph(observation, linked, selected, incident, shock, incidents):
    st.subheader('Dependency intelligence')
    if linked['graph'] is not None:
        graph = linked['graph']
        inspection = select_dependency(observation, linked)
        view = st.radio('Dependency view', ['Overview', 'Detailed'], horizontal=True,
                         key='wallet_dependency_view')
        displayed = overview_graph(graph, inspection.get('component')) if view == 'Overview' else graph
        st.caption('Dependent → dependency · Captured graph for this account; highlights follow the inspected component.')
        st.plotly_chart(plot_wallet_graph(displayed, inspection.get('component'), inspection['highlighted_nodes'],
                                          theme=st.context.theme.type or 'dark'), width='stretch', theme=None,
                        config={'displayModeBar': False}, key='wallet_dependency_chart')
        verified, modeled, unresolved = st.columns(3)
        verified.caption('Solid · verified pinned read')
        modeled.caption('Long dashed · modeled dependency')
        unresolved.caption('Dotted · unresolved connection')
        theme = st.context.theme.type or 'dark'
        palette = get_palette(theme)
        categories = sorted({node_semantics(key,data)['category'] for key,data in displayed.nodes(data=True)})
        st.html('<div style="display:flex;flex-wrap:wrap;gap:8px;font-size:.85rem;line-height:1.5">' +
                ''.join(f'<span style="padding:3px 9px;border-radius:5px;background:{category_color(category,theme)};color:{palette["ink"]}">{category}</span>'
                        for category in categories) + '</div>')
        st.caption('Colors show functional categories only. Outlines highlight inspection paths; edge styles show relationship evidence.')
        if view == 'Overview':
            st.caption('Overview hides token/query details. Summarized paths are modeled or unresolved, never direct verified links. '
                       'The inspected component stays visible; Detailed retains every captured component.')
            with st.expander('Overview projection · exact original paths', expanded=False):
                st.json([{'dependent':u,'dependency':v,'evidence_status':edge['evidence_level'],
                          'original_paths':edge['projection_paths'],'constituent_evidence':edge['path_evidence']}
                         for u,v,edge in displayed.edges(data=True) if edge.get('projected')])
        mappings = graph.graph['oracle_sources']
        verified_count = sum(m['status'] == 'verified' for m in mappings.values())
        st.caption(f'Configured source addresses: {verified_count}/{len(mappings)} verified at this wallet block. '
                   'Supplementary mapping failures do not erase complete account evidence. Feed health/freshness is not verified.')
        st.caption('Highlighted nodes: inspected component and potentially affected paths; not proof of a protocol-wide outage.')
        st.caption('Source contracts are shown only when matching getSourceOfAsset evidence exists in this observation. '
                   'A configured address does not establish source health or underlying aggregator relationships.')
        render_dependency_inspection(observation, linked, inspection)


def _render_relationship_evidence(linked, selected, incident, shock, incidents):
    if linked['graph'] is None:
        return
    graph = linked['graph']
    with st.expander('Connected wallet relationship evidence and selected trace'):
        st.dataframe([{'Asset': graph.nodes['exposure:' + address]['label'].split('\n')[0],
                       'Underlying asset': address, 'Configured source': mapping['source_address'],
                       'Mapping status': mapping['status'], 'Block': graph.graph['block']['number'],
                       'Oracle': graph.nodes['oracle']['contract_address'],
                       'Scope': 'Configured mapping only; feed health/freshness unverified'}
                      for address, mapping in graph.graph['oracle_sources'].items()],
                     hide_index=True, width='stretch')
        st.write('Selected incident: ' + incidents[incident])
        st.write('Selected reserve: ' + selected)
        if incident == 'market_price':
            st.write(f'Selected market-price shock: {shock:+d}% · All other asset prices held constant.')
        for path in linked.get('incident_paths', ()):
            st.write('Incident trace: ' + ' → '.join(graph.nodes[n]['label'].replace('\n', ' ') for n in path))
        for path in linked.get('dependency_paths', ()):
            st.write('Selected exposure dependencies: ' + ' → '.join(graph.nodes[n]['label'].replace('\n', ' ') for n in path))
        st.dataframe([{'Dependent': graph.nodes[u]['label'].replace('\n',' '),
                       'Dependency': graph.nodes[v]['label'].replace('\n',' '),
                       'Relationship': e['relation'], 'Evidence': e['evidence_level'],
                       'Block': e['block_number'], 'Block hash': e['block_hash']}
                      for u,v,e in graph.edges(data=True)], hide_index=True, width='stretch')
        st.json([{'dependent': u, 'dependency': v, **e} for u,v,e in graph.edges(data=True)])


def render_wallet_stress(observation, *, force_historical_replay=False):
    overview = st.container(key='wallet_overview')
    incidents = {'market_price': 'Collateral market-price shock', 'oracle_source_outage': 'Asset oracle-source outage · unquantified',
                 'oracle_outage': 'Shared Aave oracle outage · unquantified', 'rpc_outage': 'RPC retrieval outage · unquantified'}
    with st.container(border=True):
        st.markdown('#### Incident scenario')
        incident_column, asset_column = st.columns(2, gap='medium')
        incident = incident_column.selectbox('Real-wallet incident', list(incidents), format_func=incidents.get, key='wallet_incident')
        choices = {a['reserve_address']: a for a in observation['assets']
                   if incident != 'market_price' or a['collateral_enabled'] and int(a['supplied_raw']) > 0}
        if not choices:
            st.info('MONITOR / INSUFFICIENT MODEL COVERAGE · No enabled nonzero collateral to stress.')
            choices = {a['reserve_address']: a for a in observation['assets']}
            if not choices:
                return
        if st.session_state.get('wallet_stress_asset') not in choices:
            st.session_state.pop('wallet_stress_asset', None)
        selected = asset_column.selectbox('Enabled wallet collateral to shock' if incident == 'market_price' else 'Wallet exposure to inspect', list(choices),
            format_func=lambda address: choices[address]['symbol'] + ' · ' + address[:8] + '…' + address[-4:], key='wallet_stress_asset')
        shock_column, replay_column = st.columns([2,1], vertical_alignment='center')
        shock = shock_column.slider('Wallet collateral market-price shock (%)', -95, 100, -30, key='wallet_stress_shock', disabled=incident != 'market_price')
        if force_historical_replay:
            st.session_state.wallet_stress_replay = True
        replay = replay_column.checkbox('Historical scenario replay · NOT LIVE', key='wallet_stress_replay',
                                        disabled=force_historical_replay, wrap=True)
        st.caption('Market-price shocks are hypothetical. An oracle or RPC outage never implies a price crash. Historical replay does not refresh the evidence.')
    linked = analyze_wallet_incident(observation, selected, incident, shock / 100, historical_replay=replay)
    result = linked['financial']
    with overview:
        if replay:
            st.warning('HISTORICAL / NOT LIVE · Retrospective analysis only; no current STAY/EXIT decision.')
            st.caption(f"Original block: {observation['block']['number']:,} · Observed: {observation['retrieved_at']}")
        if result is None:
            st.warning(linked['status'])
            st.caption(linked['reason'])
            st.info('Financial impact: NOT QUANTIFIED · No dollar loss, HF change or liquidation result is inferred from a technical outage.')
        elif not result['coverage']:
            st.warning(result['status'])
            st.caption(result['reason'])
            st.caption('No quantified stress result is shown when evidence is stale, incomplete or unsupported.')
        else:
            before, after = result['baseline'], result['stressed']
            st.markdown('#### Portfolio risk overview')
            a,b,c,d = metric_cards(4, key='wallet_risk_metrics')
            a.metric('Contract-reported HF', 'N/A · no debt' if result['reported_health_factor'] is None else f"{result['reported_health_factor']:.4f}", border=True)
            b.metric('Stressed HF · conservative', 'N/A · no debt' if after['health_factor'] is None else f"{after['health_factor']:.4f}", border=True)
            c.metric('Change in marked equity', f"${result['equity_change_usd']:,.2f}", border=True)
            d.metric('All wallet debt', f"${after['debt_usd']:,.2f}", border=True)
            if after['liquidation_eligible']:
                st.error('HIGH MODELED RISK · Definite liquidation eligibility across reconciled HF conventions (HF < 1). This is not guaranteed liquidation or a realized loss.')
            elif after['liquidation_possible']:
                st.warning('POSSIBLE LIQUIDATION ELIGIBILITY · Reconciled HF conventions straddle 1. The conservative HF is below 1; eligibility is uncertain.')
            elif after['health_factor'] is None:
                st.info('No debt at this block: liquidation eligibility is not modeled as a finite HF.')
            else:
                st.info('All reconciled stressed HFs ≥ 1: no modeled liquidation eligibility in this scenario.')
            st.caption('Stress-model coverage: COMPLETE · Baseline reconciled against Aave. Only enabled collateral contributes to HF. Equity change is a stress mark, not realized damage. '
                       'Execution decision: UNAVAILABLE · ' + result['status'] + '. Wallet execution feasibility remains unverified.')
            if after['health_factor'] is not None:
                st.caption(f"Stressed HF range: {after['health_factor_min']:.10f}–{after['health_factor_max']:.10f} · HF = 1 is the eligibility boundary.")
    _render_graph(observation, linked, selected, incident, shock, incidents)
    render_wallet_safety(observation, linked)
    if result is None or not result['coverage']:
        _render_relationship_evidence(linked, selected, incident, shock, incidents)
        return
    render_wallet_decision(linked)
    render_wallet_mitigation(observation, linked)
    with st.expander('Portfolio arithmetic and model limitations'):
        st.dataframe([{'Measure': label, 'Baseline': f'${before[key]:,.2f}', 'Stressed': f'${after[key]:,.2f}'}
                      for label, key in [('Enabled collateral', 'collateral_usd'), ('All supplies', 'supplied_usd'),
                                         ('All debt', 'debt_usd'), ('Marked equity', 'equity_usd')]],
                     hide_index=True, width='stretch')
        st.caption(f"Baseline reconstructed HF: {'N/A' if before['health_factor'] is None else format(before['health_factor'], '.8f')} · "
                   f"Pool HF: {'N/A' if result['reported_health_factor'] is None else format(result['reported_health_factor'], '.8f')} · "
                   f"Absolute reconciliation tolerance: {result['hf_tolerance']:.3E} · {result['hf_convention']}.")
        with st.expander('Real-wallet stress calculation breakdown'):
            st.dataframe([{'HF convention': name, 'Stressed HF': str(value) if value is not None else 'N/A'}
                          for name, value in after['health_factors_by_convention'].items()],
                         hide_index=True, width='stretch')
            st.dataframe([{'Asset': row['asset'], 'Reserve': row['reserve_address'],
                           'Supplied units': str(row['supplied_units']), 'Debt units': str(row['debt_units']),
                           'Stressed Aave price USD': str(row['price_usd']),
                           'Enabled collateral USD': str(row['enabled_collateral_usd']),
                           'Debt USD (includes tiny balances)': str(row['debt_usd']),
                           'Collateral enabled': row['collateral_enabled'],
                           'Effective LT bps': row['liquidation_threshold_bps']}
                          for row in after['assets']], hide_index=True, width='stretch')
        st.caption('Equity = all supplied token values minus all debt values. Only enabled collateral contributes '
                   'to HF. All occurrences of the selected asset share the shocked price, including debt in that asset. '
                   'Other prices, balances and thresholds remain constant; interest accrual, liquidation penalties, '
                   'exit costs, liquidity and execution are not simulated. Nonzero eMode is unsupported. '
                   'Debt uses conservative Aave base-unit rounding. When baseline evidence cannot distinguish '
                   'Aave HF rounding conventions, the lower stressed HF and full range are shown; a range '
                   'crossing 1 means possible, not definite, liquidation eligibility. '
                   'Oracle answers are pinned observations, not independently verified feed-freshness guarantees.')

    _render_relationship_evidence(linked, selected, incident, shock, incidents)


def render_wallet_discovery():
    st.button('Load verified historical example — NOT LIVE', on_click=_load_historical_example,
              width='stretch', key='wallet_load_historical_example')
    st.caption('Instant offline replay of captured public-account evidence. No RPC calls. '
               'Live imports below remain separate; this example is not your wallet or current market data.')
    import_panel = (st.expander('Change account / public example controls', expanded=False)
                    if st.session_state.get('wallet_observation') else st.container(border=True))
    with import_panel:
        title, example = st.columns([3,1], vertical_alignment='center')
        title.markdown('#### Import a real Aave portfolio')
        example.button('Use public example', on_click=_use_public_example, width='stretch', wrap=True)
        with st.form('wallet_discovery_form', border=False):
            field, action = st.columns([4,1], vertical_alignment='bottom')
            wallet = field.text_input('Ethereum wallet address', key='wallet_address_input', placeholder=PUBLIC_EXAMPLE)
            submitted = action.form_submit_button('Inspect Aave wallet', type='primary', width='stretch', wrap=True)
            with st.expander('Historical block and read-only safeguards'):
                historical = st.checkbox('Read historical block 26,163,470 · NOT LIVE', key='wallet_historical')
                st.caption('Historical reads require an archive-capable ETH_RPC_URL. A failure never substitutes the current block. Public example holdings are not assumed to be current. An entered address is not a claim of ownership. No wallet connection or signing.')
    if submitted:
        # A failed or different-address fetch must not leave a previous position
        # looking like the requested wallet's successful current observation.
        st.session_state.wallet_observation = None
        st.session_state.wallet_observation_origin = None
        st.session_state.wallet_stress_replay = False
        st.session_state.wallet_error = None
        st.session_state.wallet_error_kind = None
        with st.spinner('Reading all Aave reserves at one Ethereum block…'):
            try:
                st.session_state.wallet_observation = (fetch_wallet_position(wallet, block_number=26163470)
                                                       if historical else fetch_wallet_position(wallet))
                # Collapse import controls after success so the actual portfolio
                # overview is visible immediately, without a second user action.
                st.rerun()
            except SourceError as exc:
                st.session_state.wallet_error = str(exc)
                st.session_state.wallet_error_kind = ('Provider/network failure' if isinstance(exc, WalletProviderError)
                                                     else 'Address, account or evidence validation failure')
    if st.session_state.get('wallet_error'):
        st.error('Wallet discovery failed: ' + st.session_state.wallet_error)
        st.caption(st.session_state.get('wallet_error_kind', 'Retrieval failure'))
        st.caption('No complete wallet position was imported; unknown balances are not zero balances. '
                   'Check the address or configure an appropriate ETH_RPC_URL and retry. '
                   'The simulator and saved market snapshot are unaffected.')
    observation = st.session_state.get('wallet_observation')
    if not observation:
        st.info('Ready to inspect a portfolio · Enter an Ethereum address or select the public example above.')
        st.caption('A successful import checks all reserves at one block. No partial position, fabricated balance or automatic trading action is displayed.')
        return

    issues = wallet_quality_issues(observation)
    historical_example = st.session_state.get('wallet_observation_origin') == 'verified_historical_example'
    if historical_example:
        st.warning('HISTORICAL / NOT LIVE · VERIFIED EXAMPLE ACCOUNT · Retrospective analysis only; no current STAY/EXIT signal.')
    elif issues:
        st.warning('HISTORICAL WALLET OBSERVATION / NOT LIVE · INSUFFICIENT CURRENT DATA')
    else:
        st.info('READ-ONLY WALLET OBSERVATION · DATED ON-CHAIN EVIDENCE')
    block = observation['block']
    st.caption(f"Account {observation['wallet_address']} · Ethereum block {block['number']:,} · Observed {observation['retrieved_at']} · {len(observation['assets'])} active assets · eMode {observation['emode']['category_id']}")
    if observation['empty']:
        st.info('No nonzero supplied or borrowed Aave V3 assets were found at this block. No wallet risk score or recommendation is generated.')
    else:
        render_wallet_stress(observation, force_historical_replay=historical_example)
    with st.expander('Wallet evidence · full reserve enumeration and raw RPC reads'):
        if historical_example:
            st.caption(f'Immutable demo fixture v{FIXTURE_VERSION} · SHA-256 checked: {EXPECTED_SHA256}')
            st.caption('The digest checks captured-file integrity, not independent proof of blockchain authenticity. '
                       'Public RPC observations are preserved unchanged; feed health/freshness and execution liquidity are not established.')
        for issue in issues:
            st.caption(issue)
        st.caption(f"Chain ID 1 · block hash {block['hash']} · Block time {datetime.fromtimestamp(block['timestamp'], timezone.utc).isoformat()} · RPC host {observation['rpc_host']}")
        st.caption(f"Complete enumeration: {len(observation['enumerated_reserves'])} initialized reserves checked. Aave USD base unit: 100,000,000; HF uses 10¹⁸ units.")
        st.dataframe([{'Asset':a['symbol'], 'Supplied units':a['supplied_units'], 'Variable debt units':a['variable_debt_units'],
                       'Collateral enabled':a['collateral_enabled'], 'Reserve address':a['reserve_address'], 'Decimals':a['decimals'],
                       'Balances cross-checked':a['balances_cross_checked']} for a in observation['assets']], hide_index=True, width='stretch')
        st.caption(observation['limitations'])
        st.json(observation, expanded=False)
