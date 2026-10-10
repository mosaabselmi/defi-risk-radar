"""Compact repayment feasibility alongside conditional wallet economics."""
import streamlit as st

from src.wallet_mitigation import analyze_wallet_mitigation


def render_wallet_mitigation(observation, analysis):
    with st.expander('Repayment feasibility · debt principal and external funding', expanded=False):
        st.caption('Hypothetical external-funded debt repayment only. No collateral withdrawal or asset sale is simulated.')
        funding_column, repayment_column = st.columns(2)
        funding = funding_column.number_input('Assumed external funding · USD', min_value=0.0, value=0.0,
                                              key='wallet_mitigation_funding')
        repayment = repayment_column.slider('Repay this % of EACH debt token', 0, 100, 25,
                                             key='wallet_mitigation_pct')
        stress = analysis.get('financial') or {}
        result = analyze_wallet_mitigation(observation, stress.get('selected_reserve'), stress.get('shock'),
                                          funding, repayment, historical_replay=stress.get('historical_replay', False),
                                          incident=analysis.get('incident'))
        st.warning(result['status'])
        if not result['coverage']:
            st.caption(result['reason'])
            return
        if result['historical_replay']:
            st.caption('HISTORICAL / NOT LIVE · Retrospective repayment arithmetic; no current action signal.')
        post = result['post_repayment']
        a, b, c = st.columns(3)
        a.metric('Full debt · USD', f"${result['pinned_full_debt_usd']:,.2f}", border=True)
        b.metric('Repayment · USD', f"${result['repayment_requirement_usd']:,.2f}", border=True)
        c.metric('Post-repay HF', 'N/A' if post['health_factor'] is None else f"{post['health_factor']:.4f}", border=True)
        st.caption('Every outstanding debt token: ' + (', '.join(r['asset'] for r in result['debt_obligations']) or 'none') +
                   '. Full debt uses pinned prices; selected repayment uses scenario prices. N/A HF means no modeled debt.')
        st.caption(f"Full debt at scenario prices: ${result['scenario_full_debt_usd']:,.2f} · "
                   'Debt obligations are not trading fees. Oracle USD equivalents are not repayment quotes.')
        if result['repayment_funding_sufficient']:
            st.info('SELECTED REPAYMENT MATHEMATICALLY COVERED by assumed external funding · token acquisition and execution unverified.')
        else:
            st.warning(f"ASSUMED FUNDING INSUFFICIENT · Selected repayment shortfall ${result['repayment_funding_shortfall_usd']:,.2f}. "
                     'The HF above assumes the repayment still happens; it is not a funded action.')
        st.caption('Full-repayment principal/value funding: ' +
                   ('mathematically covered under assumptions.' if result['full_funding_sufficient'] else
                    f"shortfall ${result['full_funding_shortfall_usd']:,.2f} at scenario prices.") +
                   ' Fees and gas require additional funding; collateral and equity are not spendable funding.')
        if post['health_factor'] is not None:
            st.caption(f"Scenario HF before repayment: {result['before_repayment']['health_factor']:.4f} · "
                       f"After range: {post['health_factor_min']:.10f}–{post['health_factor_max']:.10f} · HF = 1 is the eligibility boundary.")
        if post['liquidation_eligible']:
            st.error('HIGH MODELED RISK · All reconciled post-repayment HFs remain below 1. Repayment may not precede liquidation.')
        elif post['liquidation_possible']:
            st.warning('POSSIBLE LIQUIDATION ELIGIBILITY · Post-repayment HF conventions straddle 1.')
        else:
            st.caption('No modeled debt' if result['all_debt_nominally_repaid'] else
                       'All reconciled post-repayment HFs ≥ 1; this does not establish executable safety or withdrawable collateral.')
        with st.expander('Every debt obligation · exact token units and repayment methodology'):
            st.dataframe([{'Debt token': row['asset'], 'Underlying': row['reserve_address'],
                           'Outstanding units': str(row['debt_units']), 'Pinned oracle USD': str(row['pinned_debt_usd']),
                           'Scenario USD': str(row['scenario_debt_usd']), 'Assumed repayment units': str(row['repayment_units']),
                           'Remaining units': str(row['remaining_units']), 'Remaining scenario USD': str(row['remaining_debt_usd'])}
                          for row in result['debt_obligations']], hide_index=True, width='stretch')
            st.caption('Each token repayment = floor(raw debt × assumed fraction) in token base units. '
                       'Remaining debt = original raw debt − assumed repayment. All supplies, enabled collateral '
                       'and liquidation thresholds remain unchanged. HF reuses the existing Aave stress accounting '
                       'and reconciled rounding conventions; no financial formula is replaced.')
            st.caption(result['reason'])
            for limitation in result['limitations']:
                st.caption(limitation)
