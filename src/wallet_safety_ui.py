"""Compact, non-actionable safety-buffer presentation."""
import streamlit as st

from src.presentation import metric_cards
from src.wallet_safety import analyze_wallet_safety


def render_wallet_safety(observation, analysis):
    st.markdown('#### Safety Buffer')
    financial = analysis.get('financial') or {}
    if not financial.get('coverage') or analysis.get('incident') != 'market_price':
        st.caption('MONITOR / INSUFFICIENT MODEL COVERAGE · Safety buffer is unquantified for technical incidents or unavailable stress evidence.')
        return
    a, b, c = metric_cards(3, key='wallet_safety_metrics')
    target = b.number_input('Target HF · hypothetical', min_value=1.0, max_value=100.0,
                            value=1.2, step=0.1, key='wallet_safety_target')
    details = st.expander('Safety Buffer · bounds, debt tokens and assumptions', expanded=False)
    with details:
        funding = st.number_input('Assumed external repayment funding · USD', min_value=0.0,
                                  value=0.0, key='wallet_safety_funding')
    result = analyze_wallet_safety(observation, financial['selected_reserve'], financial['shock'], target,
                                  funding, historical_replay=financial['historical_replay'])
    if not result['coverage']:
        st.warning(result['status'])
        st.caption(result['reason'])
        return
    distance, repayment = result['distance'], result['repayment']
    low, high = distance['possible_boundary'], distance['definite_boundary']
    distance_text = ('N/A · no debt' if distance['status'] == 'NO_DEBT' else
                     'Not found' if low is None else '0% · at boundary' if low == 0 else
                     f'{low*100:.2f}%' if high == low else
                     f'{low*100:.2f}–{high*100:.2f}%' if high is not None else f'≈{low*100:.2f}% · possible')
    rounded_range = high is not None and low != high and format(low*100, '.2f') == format(high*100, '.2f')
    if rounded_range:
        distance_text = f'≈{low*100:.2f}%'
    a.metric('Baseline decline to HF 1', distance_text, border=True)
    modeled = repayment['result']
    c.metric('Required repayment · USD', f"${modeled['repayment_requirement_usd']:,.2f}", border=True)
    if result['historical_replay']:
        st.caption('HISTORICAL / NOT LIVE · Retrospective safety arithmetic; no current STAY/EXIT signal.')
    st.caption('Distance uses the selected collateral’s pinned baseline price; repayment uses the selected stressed prices. '
               'Possible/definite boundaries reflect reconciled HF conventions. HF below 1 is eligibility, not actual liquidation.')
    if rounded_range:
        st.caption('Convention boundaries differ below the displayed precision; exact separate bounds are in the details.')
    if distance['status'] == 'NO_CROSSING_FOUND':
        st.caption('No HF=1 crossing found in the bounded positive-price search; this is not proof that liquidation cannot occur.')
    if distance['baseline_severity'] in ('POSSIBLE_LIQUIDATION_ELIGIBILITY', 'DEFINITE_LIQUIDATION_ELIGIBILITY'):
        st.warning('Pinned baseline already has ' + distance['baseline_severity'].replace('_', ' ').lower() + '.')
    post = modeled['post_repayment']
    hf = 'N/A · all nominal debt repaid' if post['health_factor_min'] is None else f"{post['health_factor_min']:.4f}"
    st.caption(f"Post-repayment HF: {hf} · Remaining debt: ${post['debt_usd']:,.2f} · "
               f"Repay {modeled['repayment_fraction']*100:.6f}% of EACH debt token. "
               'Minimum sufficient on a disclosed base-unit-aware search grid.')
    st.caption('MONITOR / EXECUTION NOT VERIFIED · ' +
               ('Assumed funding mathematically covers principal.' if modeled['repayment_funding_sufficient'] else
                f"Assumed funding shortfall: ${modeled['repayment_funding_shortfall_usd']:,.2f}.") +
               ' Token acquisition, fees, gas, approvals and transaction execution remain unverified; collateral is not spendable funding.')
    with details:
        st.dataframe([{'HF convention': name, 'Boundary status': entry['status'],
                       'Lower decline %': None if entry['lower_decline'] is None else str(entry['lower_decline']*100),
                       'Upper decline %': None if entry['upper_decline'] is None else str(entry['upper_decline']*100)}
                      for name, entry in distance['by_convention'].items()], hide_index=True, width='stretch')
        st.caption(distance['limitation'])
        st.dataframe([{'Checked boundary': name.replace('_', ' '),
                       'HF minimum': str(point['health_factor_min']), 'HF maximum': str(point['health_factor_max']),
                       'Eligibility at checked point': point['liquidation_severity']}
                      for name, point in distance['boundary_checks'].items()], hide_index=True, width='stretch')
        st.caption('Reaching HF = 1 is a boundary, not definite eligibility: the existing engine requires HF strictly below 1. Convention ranges remain visible above.')
        st.caption(f"Distance bracket resolution: {distance['resolution']*100:.3E} percentage points. "
                   f"Repayment fraction resolution: {repayment['fraction_resolution']:.3E}. "
                   'These numerical resolutions are not market or execution accuracy.')
        st.dataframe([{'Debt token': row['asset'], 'Underlying': row['reserve_address'],
                       'Original units': str(row['debt_units']), 'Required repayment units': str(row['repayment_units']),
                       'Remaining units': str(row['remaining_units']), 'Repayment oracle USD': str(row['repayment_usd'])}
                      for row in modeled['debt_obligations']], hide_index=True, width='stretch')
        st.caption(repayment.get('limitation', 'Target already met; no modeled repayment required.'))
        st.caption('All supplies, collateral flags and thresholds remain unchanged. Every debt token is retained, '
                   'including dust. External USD funding is assumed convertible into the exact debt tokens at scenario oracle marks. '
                   'Interest after the pinned block, scaled-debt settlement and collateral withdrawals are not modeled.')
        st.caption(f"Pinned block {observation['block']['number']:,} · Observed {observation['retrieved_at']}.")
    return result
