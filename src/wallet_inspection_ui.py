"""Independent inspection controls and evidence-backed wallet exposure traces."""
import streamlit as st

from src.wallet_inspection import inspect_wallet_dependency
from src.wallet_failure_ui import render_failure_impact
from src.wallet_semantics import node_semantics, inspection_evidence_rows


def select_dependency(observation, analysis):
    graph = analysis['graph']
    nodes = list(graph.nodes)
    key = 'wallet_dependency_inspection'
    if st.session_state.get(key) not in nodes:
        st.session_state.pop(key, None)
    component = st.selectbox('Inspect a dependency · independent of economic scenario', nodes,
                             index=nodes.index(analysis['focus']) if analysis.get('focus') in nodes else 0,
                             format_func=lambda n: graph.nodes[n]['label'].replace('\n', ' · '), key=key, persist_state='session')
    st.caption('Inspection highlights evidence paths. Your economic scenario stays unchanged; apply a collateral explicitly in Scenarios.')
    return inspect_wallet_dependency(observation, component)


def _apply_collateral(address):
    # Explicit button callback runs before the next rerun's scenario widgets.
    st.session_state.wallet_incident = 'market_price'
    st.session_state.wallet_stress_asset = address


def render_dependency_inspection(observation, analysis, inspection, *, include_failure=True):
    if not inspection['available']:
        st.warning(inspection['status'])
        st.caption(inspection['reason'])
        return
    graph = analysis['graph']
    semantics = node_semantics(inspection['component'], graph.nodes[inspection['component']])
    st.markdown('**Inspected component:** ' + inspection['label'].replace('\n', ' · '))
    st.caption('Function: ' + semantics['category'] + ' · Identity: ' + semantics['technical_identity'])
    st.dataframe(inspection_evidence_rows(graph, inspection), hide_index=True, width='stretch',
                 column_config={name:None for name in ('Component','Technical identity','Observed position','Relationship evidence')},
                 alt='Evidence summary for the inspected wallet dependency')
    st.caption(inspection['component_type'] + ' · ' + inspection['component_evidence_status'] +
               ' · Trace relationships: ' + ', '.join(inspection['trace_evidence_levels']))
    address = inspection['contract_address']
    if address:
        st.caption('Account address (not ownership proof):' if inspection['component'] == 'position' else 'Contract address:')
        st.code(address, language=None, wrap_lines=True)
    else:
        st.caption('No separate contract address established for this component.')
    st.caption(inspection['reason'])
    if include_failure:
        render_failure_impact(observation, analysis, inspection, on_apply=_apply_collateral)
    with st.expander('Dependency trace · observed exposure paths', expanded=False):
        st.dataframe([{'Exposure': row['asset'], 'Supplied units': row['supplied_units'],
                   'Enabled collateral units': row['enabled_collateral_units'], 'Outstanding debt units': row['debt_units'],
                   'Reachability / scope': row['scope']}
                      for row in inspection['exposures']], hide_index=True, width='stretch')
        if not inspection['exposures']:
            st.info('No active asset exposure reaches this component in the captured graph. This does not establish absence of external risks.')
        paths = inspection['position_paths']
        for path in paths[:3]:
            st.caption('Dependency trace: ' + ' → '.join(graph.nodes[n]['label'].replace('\n', ' ') for n in path))
        if len(paths) > 3:
            st.caption(f'{len(paths)} position-to-component paths; full paths and deduplicated relationships are in the evidence below.')
        financial = analysis.get('financial')
        if financial and financial.get('coverage'):
            selected = financial['selected_reserve']
            asset = next(a for a in observation['assets'] if a['reserve_address'] == selected)
            matches = any(row['reserve_address'] == selected for row in inspection['exposures'])
            hf = financial['stressed']['health_factor']
            st.caption(f"Independent market scenario: {asset['symbol']} {financial['shock']*100:+.1f}% · "
                       f"Stressed HF: {'N/A (no debt)' if hf is None else format(hf, '.4f')} · "
                       f"Marked equity change: ${financial['equity_change_usd']:,.2f}. " +
                       ('The selected collateral is among these potentially affected exposures. ' if matches else
                        'The selected collateral is outside this inspected dependency scope. ') +
                       'These scenario results are not attributed to a dependency outage. Repayment modeling in Decision uses the same selected collateral and shock.')
        else:
            st.caption('Current incident economics: UNQUANTIFIED / INSUFFICIENT MODEL COVERAGE. No unsupported outage or missing-evidence loss is inferred.')
    with st.expander('Inspected dependency · full paths and pinned RPC provenance'):
        st.caption(f"Account {inspection['provenance']['wallet_address']} · block {inspection['provenance']['block']['number']:,} · "
                   f"Observed {inspection['provenance']['retrieved_at']} · hash {inspection['provenance']['block']['hash']}")
        st.json({'position_paths': paths, 'exposure_paths': inspection['exposures'],
                 'relationships': inspection['relationships'], 'source_mappings': inspection['source_mappings'],
                 'provenance': inspection['provenance']})
