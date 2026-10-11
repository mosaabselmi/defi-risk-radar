"""Compact hypothetical failure investigation, independent of price controls."""
import streamlit as st

from src.presentation import responsive_row
from src.wallet_failure import INCIDENTS, analyze_dependency_failure


def render_failure_impact(observation, analysis, inspection, *, on_apply):
    st.markdown('#### Failure → potential impact')
    controls = responsive_row('wallet_failure_controls', 340)
    category_column, asset_column = controls.container(), controls.container()
    incident = category_column.selectbox('Hypothetical failure mechanism', list(INCIDENTS),
                                         format_func=INCIDENTS.get, key='wallet_failure_incident', persist_state='session')
    choices = {row['reserve_address']: row['asset'] for row in inspection['exposures']}
    target = None
    if incident in ('market_price', 'stablecoin_depeg') and choices:
        key = 'wallet_dependency_market_target'
        if st.session_state.get(key) not in choices:
            st.session_state.pop(key, None)
        selected = analysis.get('asset_address')
        target = asset_column.selectbox('Explicit asset-price scenario', list(choices),
            index=list(choices).index(selected) if selected in choices else 0,
            format_func=lambda a: choices[a] + ' · ' + a[:8] + '…' + a[-4:], key=key, persist_state='session')
    else:
        asset_column.caption('Technical reachability identifies potential exposures. The independent market scenario above remains unchanged.')
    result = analyze_dependency_failure(observation, inspection, analysis, incident, target)
    if result['historical']:
        st.caption('HISTORICAL / NOT LIVE · Pinned retrospective evidence; no current STAY/EXIT signal.')
    st.info('**' + result['quantification'] + '** · ' + result['consequence'])
    st.caption('Evidence limitation: ' + result['limitation'])
    if result['exposures']:
        st.caption('Connected observed exposures: ' + ', '.join(row['asset'] for row in result['exposures']) +
                   '. Paths indicate possible exposure, not realized loss.')
    financial = result['financial']
    if financial:
        before, after = financial['baseline'], financial['stressed']
        hf = lambda value: 'N/A (no debt)' if value is None else format(value, '.4f')
        st.caption(f"Explicit shock: {financial['shock']*100:+.1f}% · HF {hf(before['health_factor'])} → {hf(after['health_factor'])} · "
                   f"Liquidation severity: {financial['risk_severity'].replace('_', ' ')}. "
                   'This is the independent market scenario, not a loss attributed to dependency failure.')
    if result['can_apply_market']:
        st.button('Apply collateral to market scenario', on_click=on_apply, args=(target,),
                  key='wallet_dependency_apply_market', wrap=True)
        st.caption('Explicit action only: selects this enabled collateral and preserves the shock above. '
                   + ('Peg/stablecoin identity is user-assumed; a depeg uses this asset-price scenario. ' if incident == 'stablecoin_depeg' else '')
                   + 'No feed failure is inferred.')
    with st.expander('Failure mechanism · exposure roles and consequence details', expanded=False):
        st.write('Inspected dependency: ' + inspection['label'].replace('\n', ' · '))
        st.caption(inspection['component_type'] + ' · ' + inspection['component_evidence_status'])
        st.write('Potential mechanism: ' + result['mechanism'])
        st.dataframe([{'Impact dimension': key, 'Potential consequence / scope': value}
                      for key, value in result['dimensions'].items()], hide_index=True, width='stretch')
        st.dataframe([{'Asset': row['asset'], 'Underlying address': row['reserve_address'],
                       'Supplied units': row['supplied_units'], 'Enabled collateral units': row['enabled_collateral_units'],
                       'Outstanding debt units': row['debt_units'], 'Evidence path scope': row['scope']}
                      for row in result['exposures']], hide_index=True, width='stretch')
        st.caption('No exploit probability, oracle error magnitude, congestion duration, available liquidity '
                   'or executable mitigation cost is measured. Financial marks use only the existing covered price scenario.')
    return result
