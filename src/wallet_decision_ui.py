"""Assumption inputs and conservative conclusions for connected wallet analysis."""
import streamlit as st
from src.wallet_decision import WalletDecisionAssumptions, compare_wallet_economics


def render_wallet_decision(analysis):
    st.subheader('Conditional wallet economics · assumptions only')
    st.caption('Compare the modeled mark-to-market component with a hypothetical mitigation cost. '
               'Execution feasibility is unverified; this does not establish an executable full-wallet exit.')
    summary = st.container()
    with st.expander('User assumptions · edit probability, mitigation and transaction costs', expanded=False):
        left, right = st.columns(2)
        probability = left.number_input('Assumed 24h scenario probability (%)', min_value=0.0, max_value=100.0, value=10.0, key='wallet_decision_probability')
        avoided = right.number_input('Assumed downside avoided (%)', min_value=0.0, max_value=100.0, value=100.0, key='wallet_decision_avoidance')
        st.caption('Probability is a user assumption, not a measured incident rate. Avoidance is assumed efficacy, not a verified mitigation plan.')
        notional = st.number_input('Aggregate assumed traded notional · USD', min_value=0.0, value=0.0, key='wallet_decision_notional')
        st.caption('Enter the sum of notionals across assumed trading legs. Collateral value is not directly withdrawable liquidity; equity is not a traded notional.')
        a,b,c = st.columns(3)
        spread = a.number_input('Assumed spread · bps', min_value=0.0, max_value=10000.0, value=5.0, key='wallet_decision_spread')
        slippage = b.number_input('Assumed slippage · bps', min_value=0.0, max_value=10000.0, value=20.0, key='wallet_decision_slippage')
        fee = c.number_input('Assumed trading fee · bps', min_value=0.0, max_value=10000.0, value=5.0, key='wallet_decision_fee')
        gas = st.number_input('Assumed aggregate gas · USD', min_value=0.0, value=15.0, key='wallet_decision_gas')
    result = compare_wallet_economics(analysis, WalletDecisionAssumptions(probability,avoided,notional,spread,slippage,fee,gas))
    with summary:
        if not result['comparison_available']:
            st.warning(result['recommendation'])
            st.caption(result['reason'])
            return
        if result['historical_replay']:
            st.warning('HISTORICAL / NOT LIVE · Retrospective economic comparison only.')
        st.info('CONDITIONAL COMPARISON · ' + result['conclusion'])
        st.warning(result['recommendation'])
        st.caption(result['reason'])
        if result['risk_severity'] in ('DEFINITE_LIQUIDATION_ELIGIBILITY', 'POSSIBLE_LIQUIDATION_ELIGIBILITY'):
            st.warning('LIQUIDATION MECHANICS NOT PRICED · Eligibility is modeled or possible. This comparison excludes penalties and timing; it is not total expected loss or proof of a feasible exit.')
        if result['baseline_equity_usd'] <= 0 or result['stressed_equity_usd'] <= 0:
            st.warning('NON-POSITIVE MARKED EQUITY · Do not assume collateral sale proceeds can fund debt repayment.')
        cards = st.columns(3)
        cards[0].metric('Expected downside', f"${result['expected_scenario_downside_usd']:,.2f}", border=True)
        cards[1].metric('Assumed transaction cost', f"${result['hypothetical_cost_usd']:,.2f}", border=True)
        cards[2].metric('Expected avoided downside', f"${result['expected_avoided_downside_usd']:,.2f}", border=True)
        st.caption('Probability-weighted stress marks · not realized losses. Transaction cost is a hypothetical approximation, not a quote.')
        threshold = result['breakeven_probability']
        if threshold is None:
            st.caption('Break-even probability: N/A · No positive modeled downside can be avoided under these assumptions.')
        elif threshold > 1:
            st.caption(f'Break-even probability: {threshold*100:.2f}% · No probability from 0–100% reaches break-even under this cost/avoidance assumption.')
        else:
            st.caption(f'Break-even assumed 24h probability: {threshold*100:.2f}% · Equality is break-even; higher probabilities favor the assumed mitigation cost comparison.')
        if result['required_avoidance_fraction'] is not None:
            fraction = result['required_avoidance_fraction']
            st.caption(f'Required downside avoidance at selected probability: {fraction*100:.2f}%'
                       + (' · Above 100%: not achievable in this mark-to-market comparison.' if fraction > 1 else ' · Efficacy remains unverified.'))
        if result['maximum_trade_notional_usd'] is not None:
            st.caption(f"Break-even aggregate notional ceiling at assumed bps/gas: ${result['maximum_trade_notional_usd']:,.2f}. This is algebra, not available liquidity.")
        if result['debt_repayment_required']:
            st.warning(f"DEBT REPAYMENT CONSTRAINT · Pinned baseline debt mark ${result['pinned_debt_mark_usd']:,.2f}; not a repayment quote. Full withdrawal requires repayment or another unwind route.")
    with st.expander('Methodology, cost components and missing execution evidence'):
        st.dataframe([{'Component':label,'USD':f'${result[key]:,.2f}'} for label,key in [
            ('Mark-to-market stress downside (not realized damage)','mark_to_market_downside_usd'),
            ('Favorable stress outcome (reported separately)','favorable_stress_usd'),
            ('Expected favorable outcome · assumed probability','expected_favorable_usd'),
            ('Expected scenario downside · assumed probability','expected_scenario_downside_usd'),
            ('Expected avoided downside · assumed efficacy','expected_avoided_downside_usd'),
            ('Hypothetical transaction cost · not a quote','hypothetical_cost_usd'),
            ('Expected avoided downside minus assumed cost','conditional_margin_usd')]], hide_index=True, width='stretch')
        st.caption('Downside = max(−change in marked equity, 0). Expected scenario downside = probability × downside. '
                   'Expected avoided downside = expected downside × assumed avoidance fraction. '
                   'Costs = aggregate traded notional × (spread + slippage + fee) / 10,000 + aggregate gas USD. '
                   'Break-even probability = costs / (downside × avoidance). Debt principal is a liability, not a fee. '
                   'No liquidation penalty is added, avoiding a fabricated or double-counted realized loss.')
        st.dataframe([{'Assumed cost component':k,'USD':f'${v:,.2f}'} for k,v in result['cost_components'].items()],hide_index=True,width='stretch')
        for limitation in result['constraints']:
            st.caption(limitation)
