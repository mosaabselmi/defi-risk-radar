"""Assumption inputs and conservative conclusions for connected wallet analysis."""
import streamlit as st
from src.wallet_decision import WalletDecisionAssumptions, compare_wallet_economics
from src.presentation import metric_cards


def render_wallet_decision(analysis, *, show_historical_banner=True, investigation_name=None):
    st.subheader('STAY vs EXIT · conditional model')
    if investigation_name:
        st.caption('Selected investigation: ' + investigation_name + ' · hypothetical market-price scenario')
    summary = st.container()
    with st.expander('User assumptions · edit probability, mitigation and transaction costs', expanded=False):
        left, right = st.columns(2)
        probability = left.number_input('Assumed 24h scenario probability (%)', min_value=0.0, max_value=100.0, value=10.0, key='wallet_decision_probability', persist_state='session')
        avoided = right.number_input('Assumed downside avoided (%)', min_value=0.0, max_value=100.0, value=100.0, key='wallet_decision_avoidance', persist_state='session')
        st.caption('Probability is a user assumption, not a measured incident rate. Avoidance is assumed efficacy, not a verified mitigation plan.')
        notional = st.number_input('Aggregate assumed traded notional · USD', min_value=0.0, value=0.0, key='wallet_decision_notional', persist_state='session')
        st.caption('Enter the sum of notionals across assumed trading legs. Collateral value is not directly withdrawable liquidity; equity is not a traded notional.')
        a,b,c = st.columns(3)
        spread = a.number_input('Assumed spread · bps', min_value=0.0, max_value=10000.0, value=5.0, key='wallet_decision_spread', persist_state='session')
        slippage = b.number_input('Assumed slippage · bps', min_value=0.0, max_value=10000.0, value=20.0, key='wallet_decision_slippage', persist_state='session')
        fee = c.number_input('Assumed trading fee · bps', min_value=0.0, max_value=10000.0, value=5.0, key='wallet_decision_fee', persist_state='session')
        gas = st.number_input('Assumed aggregate gas · USD', min_value=0.0, value=15.0, key='wallet_decision_gas', persist_state='session')
    result = compare_wallet_economics(analysis, WalletDecisionAssumptions(probability,avoided,notional,spread,slippage,fee,gas))
    with summary:
        if not result['comparison_available']:
            st.warning('MODELED PREFERENCE: INSUFFICIENT DATA')
            st.warning(result['recommendation'])
            st.caption(result['reason'])
            st.caption('A supported stress and complete user-assumed trade cost are needed before comparing STAY and EXIT. '
                       'No executable quote or exit route has been verified.')
            return
        if result['historical_replay'] and show_historical_banner:
            st.warning('HISTORICAL / NOT LIVE · Retrospective economic comparison only.')
        preference = result['model_preference']
        st.warning('MODELED PREFERENCE: ' + preference + ' — CONDITIONAL')
        threshold = result['breakeven_probability']
        threshold_label = 'N/A' if threshold is None else f'{threshold*100:.2f}%'
        cards = metric_cards(2, key='wallet_choice_metrics')
        cards[0].metric('STAY · modeled 24h downside', f"${result['stay_modeled_cost_usd']:,.2f}", border=False)
        cards[1].metric('EXIT · hypothetical total', f"${result['exit_modeled_cost_usd']:,.2f}", border=False)
        st.caption(f"STAY = {result['assumptions']['probability']*100:.2f}% assumed 24h probability × "
                   f"${result['mark_to_market_downside_usd']:,.2f} conditional mark-to-market downside. "
                   f"EXIT = ${result['residual_expected_downside_usd']:,.2f} residual expected downside after "
                   f"{result['assumptions']['avoided_downside_fraction']*100:.2f}% assumed avoidance + "
                   f"${result['hypothetical_cost_usd']:,.2f} assumed transaction costs.")
        st.caption('Break-even assumed 24h probability: ' + threshold_label + ' · EXECUTION NOT VERIFIED · '
                   'No executable quote, repayment route or withdrawal liquidity confirmed.')
        if preference == 'EXIT':
            why = 'Expected avoided downside exceeds the assumed exit cost, but execution is not verified.'
        elif preference == 'STAY':
            why = 'Expected avoided downside is below the assumed exit cost.'
        else:
            why = 'The two modeled alternatives have equal expected cost under these assumptions.'
        st.info('CONDITIONAL COMPARISON · Why? ' + why)
        st.caption('This compares hypothetical alternatives, not an instruction to trade. '
                   'Avoidance of loss is user-assumed, including when set to 100%; no current STAY/EXIT action is verified.')
        st.caption('Assumed transaction cost includes spread, slippage, trading fee and gas on entered trade notional. '
                   'It excludes debt repayment principal, liquidation penalties, interest accrual, failed transactions '
                   'and market movement during an exit. It is not a verified cost of closing this leveraged wallet.')
        st.warning(result['recommendation'])
        if result['risk_severity'] in ('DEFINITE_LIQUIDATION_ELIGIBILITY', 'POSSIBLE_LIQUIDATION_ELIGIBILITY'):
            st.warning('LIQUIDATION MECHANICS NOT PRICED · Eligibility is modeled or possible. This comparison excludes penalties and timing; it is not total expected loss or proof of a feasible exit.')
        if result['baseline_equity_usd'] <= 0 or result['stressed_equity_usd'] <= 0:
            st.warning('NON-POSITIVE MARKED EQUITY · Do not assume collateral sale proceeds can fund debt repayment.')
        if result['debt_repayment_required']:
            st.warning(f"DEBT REPAYMENT CONSTRAINT · Pinned baseline debt mark ${result['pinned_debt_mark_usd']:,.2f}; not a repayment quote. Full withdrawal requires repayment or another unwind route.")
    with st.expander('Methodology, cost components and missing execution evidence'):
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
        st.dataframe([{'Component':label,'USD':f'${result[key]:,.2f}'} for label,key in [
            ('Mark-to-market stress downside (not realized damage)','mark_to_market_downside_usd'),
            ('Favorable stress outcome (reported separately)','favorable_stress_usd'),
            ('Expected favorable outcome · assumed probability','expected_favorable_usd'),
            ('Expected scenario downside · assumed probability','expected_scenario_downside_usd'),
            ('Expected avoided downside · assumed efficacy','expected_avoided_downside_usd'),
            ('EXIT residual expected downside','residual_expected_downside_usd'),
            ('EXIT total · residual plus assumed costs','exit_modeled_cost_usd'),
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
