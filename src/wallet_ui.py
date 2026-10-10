"""Session-only wallet evidence and separate stress panel; no synthetic integration."""
from datetime import datetime, timezone

import streamlit as st

from src.sources import SourceError
from src.wallet import WalletProviderError, fetch_wallet_position, wallet_quality_issues
from src.wallet_dependencies import analyze_wallet_incident
from src.wallet_viz import plot_wallet_graph
from src.wallet_decision_ui import render_wallet_decision


def render_wallet_stress(observation):
    with st.container(border=True):
        st.subheader('Connected real-wallet analysis · separate from synthetic demo')
        st.caption(f"Imported account: {observation['wallet_address']} · Ethereum block {observation['block']['number']:,}. "
                   'The graph and analysis below use this same observation.')
        st.caption('A hypothetical market-price shock, not an oracle malfunction or outage. '
                   'Uses only pinned Aave oracle prices; this does not change the synthetic simulator.')
        incidents = {'market_price': 'Collateral market-price shock', 'oracle_source_outage': 'Asset oracle-source outage · unquantified',
                     'oracle_outage': 'Shared Aave oracle outage · unquantified', 'rpc_outage': 'RPC retrieval outage · unquantified'}
        incident = st.selectbox('Real-wallet incident', list(incidents), format_func=incidents.get, key='wallet_incident')
        choices = {a['reserve_address']: a for a in observation['assets']
                   if incident != 'market_price' or a['collateral_enabled'] and int(a['supplied_raw']) > 0}
        if not choices:
            st.info('MONITOR / INSUFFICIENT MODEL COVERAGE · No enabled nonzero collateral to stress.')
            choices = {a['reserve_address']: a for a in observation['assets']}
            if not choices:
                return
        # Retain selection across reruns; clear only if a new wallet lacks that reserve.
        if st.session_state.get('wallet_stress_asset') not in choices:
            st.session_state.pop('wallet_stress_asset', None)
        selected = st.selectbox('Enabled wallet collateral to shock' if incident == 'market_price' else 'Wallet exposure to inspect', list(choices),
                                format_func=lambda address: choices[address]['symbol'] + ' · ' + address,
                                key='wallet_stress_asset')
        shock = st.slider('Wallet collateral market-price shock (%)', -95, 100, -30,
                          key='wallet_stress_shock', disabled=incident != 'market_price')
        replay = st.checkbox('Historical scenario replay · NOT LIVE', key='wallet_stress_replay')
        if replay:
            st.warning('HISTORICAL / NOT LIVE · Retrospective analysis only; no current STAY/EXIT decision.')
            st.caption(f"Original block: {observation['block']['number']:,} · Observed: {observation['retrieved_at']}")
        linked = analyze_wallet_incident(observation, selected, incident, shock / 100, historical_replay=replay)
        if linked['graph'] is not None:
            graph = linked['graph']
            st.caption('Arrows: dependent → dependency. Logical reserve and valuation nodes represent records and queries, not extra contracts.')
            st.plotly_chart(plot_wallet_graph(graph, linked.get('focus'), linked.get('affected', ()),
                                              theme=st.context.theme.type or 'dark'), width='stretch', theme=None,
                            config={'displayModeBar': False}, key='wallet_dependency_chart')
            st.caption('Solid edge: verified pinned contract read.')
            st.caption('Dashed edge: modeled analytical dependency.')
            st.caption('Dotted edge: unknown / unresolved connection.')
            st.caption('Highlighted node: selected exposure or potentially affected dependency path; not proof of a protocol-wide outage.')
            st.caption('Source contracts are shown only when matching getSourceOfAsset evidence exists in this observation. '
                       'A configured address does not establish source health or underlying aggregator relationships.')
            with st.expander('Connected wallet relationship evidence and selected trace'):
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
        result = linked['financial']
        if result is None:
            st.warning(linked['status'])
            st.caption(linked['reason'])
            st.info('Financial impact: NOT QUANTIFIED · No dollar loss, HF change or liquidation result is inferred from a technical outage.')
            return
        if not result['coverage']:
            st.warning(result['status'])
            st.caption(result['reason'])
            st.caption('No quantified stress result is shown when evidence is stale, incomplete or unsupported.')
            return
        before, after = result['baseline'], result['stressed']
        if after['liquidation_eligible']:
            st.error('HIGH MODELED RISK · Definite liquidation eligibility across reconciled HF conventions (HF < 1). '
                     'This is not guaranteed liquidation or a realized loss.')
        elif after['liquidation_possible']:
            st.warning('POSSIBLE LIQUIDATION ELIGIBILITY · Reconciled HF conventions straddle 1. '
                       'The conservative HF is below 1; eligibility is uncertain.')
        elif after['health_factor'] is None:
            st.info('No debt at this block: liquidation eligibility is not modeled as a finite HF.')
        else:
            st.info('All reconciled stressed HFs ≥ 1: no modeled liquidation eligibility in this scenario.')
        st.info('Stress-model coverage: COMPLETE · Baseline reconciled against Aave.')
        st.caption('Execution decision: UNAVAILABLE · ' + result['status'] + ' · ' + result['reason'])
        columns = st.columns(3)
        columns[0].metric('Stressed collateral', f"${after['collateral_usd']:,.2f}")
        columns[1].metric('All wallet debt', f"${after['debt_usd']:,.2f}")
        columns[2].metric('Stressed HF · conservative', 'N/A · no debt' if after['health_factor'] is None
                          else f"{after['health_factor']:.4f}")
        if after['health_factor'] is not None:
            st.caption(f"Stressed HF range: {after['health_factor_min']:.10f}–{after['health_factor_max']:.10f} · HF = 1 is the eligibility boundary.")
        st.dataframe([{'Measure': label, 'Baseline': f'${before[key]:,.2f}', 'Stressed': f'${after[key]:,.2f}'}
                      for label, key in [('Enabled collateral', 'collateral_usd'), ('All supplies', 'supplied_usd'),
                                         ('All debt', 'debt_usd'), ('Marked equity', 'equity_usd')]],
                     hide_index=True, width='stretch')
        st.metric('Change in marked equity', f"${result['equity_change_usd']:,.2f}")
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
        render_wallet_decision(linked)


def render_wallet_discovery():
    with st.container(border=True):
        st.subheader('Real Aave wallet discovery · read-only')
        st.caption('Inspect a public Ethereum address without connecting a wallet or signing. '
                   'An entered address is not a claim of ownership. These observations do not change '
                   'the synthetic scenario simulator or its STAY / EXIT / MONITOR decisions.')
        with st.form('wallet_discovery_form'):
            wallet = st.text_input('Ethereum wallet address', key='wallet_address_input',
                                   placeholder='0x24a416e5c00c7b47a5f8a300984579b53919330b')
            historical = st.checkbox('Read historical block 26,163,470 · NOT LIVE', key='wallet_historical')
            st.caption('Historical reads require an archive-capable ETH_RPC_URL. '
                       'A failure never substitutes the current block.')
            submitted = st.form_submit_button('Inspect Aave wallet')
        if submitted:
            # A failed or different-address fetch must not leave a previous position
            # looking like the requested wallet's successful current observation.
            st.session_state.wallet_observation = None
            st.session_state.wallet_error = None
            st.session_state.wallet_error_kind = None
            with st.spinner('Reading all Aave reserves at one Ethereum block…'):
                try:
                    st.session_state.wallet_observation = (fetch_wallet_position(wallet, block_number=26163470)
                                                           if historical else fetch_wallet_position(wallet))
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
            return
        issues = wallet_quality_issues(observation)
        if issues:
            st.warning('HISTORICAL WALLET OBSERVATION / NOT LIVE · INSUFFICIENT CURRENT DATA')
            for issue in issues:
                st.caption(issue)
        else:
            st.info('READ-ONLY WALLET OBSERVATION · DATED ON-CHAIN EVIDENCE')
        block = observation['block']
        st.code(observation['wallet_address'], language=None)
        st.caption(f"Ethereum mainnet · chain ID 1 · block {block['number']:,} · hash {block['hash']}")
        block_time = datetime.fromtimestamp(block['timestamp'], timezone.utc).isoformat()
        st.caption(f"Block time: {block_time} · Observed: {observation['retrieved_at']} · RPC host: {observation['rpc_host']}")
        st.caption(f"Complete enumeration: {len(observation['enumerated_reserves'])} initialized reserves checked. "
                   f"Account eMode category: {observation['emode']['category_id']}. "
                   'USD totals use the verified Aave oracle base unit of 100,000,000; HF uses 10¹⁸ units.')
        if observation['empty']:
            st.info('No nonzero supplied or borrowed Aave V3 assets were found at this block. '
                    'No wallet risk score or recommendation is generated.')
        else:
            collateral, debt, hf = st.columns(3)
            collateral.metric('Wallet collateral · USD', f"${float(observation['collateral_usd']):,.2f}")
            debt.metric('Wallet debt · USD', f"${float(observation['debt_usd']):,.2f}")
            hf.metric('Contract-reported HF', 'N/A · no debt' if observation['health_factor'] is None
                      else f"{float(observation['health_factor']):.4f}")
            st.caption('Account totals and HF are reported by Aave, not by the synthetic scenario model. '
                       'Supplied assets contribute to collateral only when enabled for this account. '
                       'No debt means the contract returns the maximum uint256 HF sentinel, not a risk score.')
            st.dataframe([{
                'Asset': asset['symbol'], 'Reserve address': asset['reserve_address'],
                'Decimals': asset['decimals'], 'Supplied units': asset['supplied_units'],
                'Variable debt units': asset['variable_debt_units'],
                'Collateral enabled': asset['collateral_enabled'],
                'Balances cross-checked': asset['balances_cross_checked'],
            } for asset in observation['assets']], hide_index=True, width='stretch')
        st.caption(observation['limitations'])
        with st.expander('Wallet evidence · full reserve enumeration and raw RPC reads'):
            st.json(observation)
        if not observation['empty']:
            render_wallet_stress(observation)
