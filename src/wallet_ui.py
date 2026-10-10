"""Session-only wallet evidence display; never connects to the scenario model."""
from datetime import datetime, timezone

import streamlit as st

from src.sources import SourceError
from src.wallet import WalletProviderError, fetch_wallet_position, wallet_quality_issues


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
