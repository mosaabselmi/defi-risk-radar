"""Compact navigation from applicable investigations to the existing wallet panels."""
from hashlib import sha256

import streamlit as st

from src.wallet_risk_brief import causal_trace, discover_scenarios
from src.website import open_analysis


def _scope(observation):
    return (observation['wallet_address'], observation['block']['hash'])


def selected_investigation(observation, scenarios):
    if st.session_state.get('wallet_brief_scope') != _scope(observation):
        return None
    return next((item for item in scenarios if item['id'] == st.session_state.get('wallet_brief_choice')), None)


def clear_investigation():
    st.session_state.pop('wallet_brief_choice', None)
    st.session_state.pop('wallet_brief_scope', None)


def _investigate(observation, scenario):
    st.session_state.wallet_brief_scope = _scope(observation)
    st.session_state.wallet_brief_choice = scenario['id']
    if scenario['kind'] in ('collateral_price', 'asset_price') and scenario['quantification'] == 'SUPPORTED SCENARIO':
        st.session_state.wallet_incident = 'market_price'
        st.session_state.wallet_stress_asset = scenario['exposures'][0]['address']
    elif scenario['kind'] in ('oracle_source', 'aave_oracle'):
        st.session_state.wallet_incident = ('oracle_source_outage' if scenario['kind'] == 'oracle_source'
                                            else 'oracle_outage')
        st.session_state.wallet_stress_asset = scenario['exposures'][0]['address']
    # All other mechanisms are inspection-only. Do not change the independent
    # market-price incident, shock, probability, trading cost or repayment inputs.
    open_analysis('Scenarios')


def scenario_list(observation, linked, *, replay):
    return discover_scenarios(observation, linked.get('graph'), historical_replay=replay)


def render_brief(observation, scenarios):
    st.subheader('Automatic risk brief')
    st.caption('Applicable investigations from this imported position, ordered by model coverage, then number of connected observed exposures. '
               'This is not a probability, incident alert or overall risk score.')
    if not scenarios:
        st.info('No complete position-specific dependency investigation is available.')
        return
    # A covered collateral scenario is a starting point, not a claim that it is
    # more likely than an operational failure.
    featured = next((s for s in scenarios if s['quantification'] == 'SUPPORTED SCENARIO'), scenarios[0])
    remaining = [s for s in scenarios if s is not featured]
    with st.container(border=True):
        st.caption('SUGGESTED START · ' + featured['quantification'])
        st.markdown('**' + featured['name'] + '**')
        st.caption(featured['why'] + ' ' + featured['consequence'])
        st.caption('Dependency: ' + featured['dependency'] + ' · ' + featured['evidence'])
        st.caption('Affected: ' + ', '.join(r['asset'] + ' (' + ', '.join(r['roles']) + ')' for r in featured['exposures']))
        st.caption('Limit: ' + featured['limitation'])
        st.button('Investigate this scenario', key='wallet_brief_' + sha256(featured['id'].encode()).hexdigest()[:12],
                  on_click=_investigate, args=(observation, featured), type='primary')
    with st.expander(f'Other applicable investigations ({len(remaining)})', expanded=False):
        for item in remaining:
            st.markdown('**' + item['name'] + '** · ' + item['quantification'])
            st.caption(item['dependency'] + ' · ' + item['evidence'] + ' · ' +
                       ', '.join(r['asset'] + ' (' + ', '.join(r['roles']) + ')' for r in item['exposures']))
            st.caption(item['why'] + ' ' + item['consequence'])
            st.caption('Limit: ' + item['limitation'])
            st.button('Investigate ' + item['name'], key='wallet_brief_' + sha256(item['id'].encode()).hexdigest()[:12],
                      on_click=_investigate, args=(observation, item))


def render_selected(scenario, financial, *, decision=False):
    if scenario is None:
        return
    st.markdown('**Selected investigation: ' + scenario['name'] + '**')
    st.caption(scenario['dependency'] + ' · ' + scenario['evidence'] + ' · ' + scenario['quantification'])
    st.caption('Affected observed exposures: ' + ', '.join(
        r['asset'] + ' (' + ', '.join(r['roles']) + ')' for r in scenario['exposures']))
    if scenario['quantification'] != 'SUPPORTED SCENARIO':
        st.warning('Economic effect: UNQUANTIFIED · No incident loss or executable decision is established.')
        st.caption(scenario['consequence'] + ' ' + scenario['limitation'])
        if decision:
            st.caption('The conditional STAY/EXIT comparison requires a separately selected, covered market-price shock.')
        return
    if not decision:
        if scenario['kind'] == 'collateral_price' and financial and financial.get('coverage') and financial['shock'] >= 0:
            st.warning('The suggested decline is selected, but your unchanged shock is zero or positive. '
                       'The figures below describe that selected input, not a price decline.')
        # Keep each causal step readable; escape dollar signs so Streamlit does
        # not interpret adjacent USD amounts as inline mathematics.
        st.markdown('  \n'.join('→ ' + step for step in causal_trace(scenario, financial)).replace('$', '\\$'))
        st.caption('Market-price shock only. No oracle failure or realized liquidation is implied. ' + scenario['limitation'])
    else:
        if financial and financial.get('coverage'):
            st.caption(f"Existing stress result: marked equity change ${financial['equity_change_usd']:,.2f}; "
                       'hypothetical, not realized loss. HF = 1 is the liquidation-eligibility boundary.')
        st.caption('The comparison below uses the existing covered stress result and your unchanged probability, avoidance and cost assumptions. '
                   'Debt repayment, liquidity, routes and execution remain unverified.')
