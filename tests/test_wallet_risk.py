"""Deterministic real-wallet stress tests; no live calls or snapshot writes."""
import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.wallet import MAX_UINT256
from src.wallet_risk import analyze_wallet_stress
from tests.test_wallet import WalletRPC, abi, fetch


@pytest.fixture
def observed():
    return fetch(WalletRPC())


def stress(observed, shock=-0.3, selected=None, **kwargs):
    return analyze_wallet_stress(observed, selected or observed['assets'][0]['reserve_address'], shock, **kwargs)


def replace_read(observed, signature, values, target=None):
    from src.wallet import WALLET_SELECTORS
    for entry in observed['rpc_evidence']:
        if entry['method'] == 'eth_call' and entry['params'][0]['data'].startswith(WALLET_SELECTORS[signature]):
            if target is None or entry['params'][0]['to'] == target:
                entry['result'] = abi(*values)


def test_weth_usdt_and_tiny_gho_debt(observed):
    original = copy.deepcopy(observed)
    result = stress(observed)
    assert result['coverage'] and result['status'] == 'MONITOR / STRESS ANALYSIS ONLY'
    assert result['baseline']['collateral_usd'] == 10000
    assert result['baseline']['debt_usd'] == Decimal('6000.1')
    assert float(result['baseline']['health_factor']) == pytest.approx(8300/6000.1)
    assert result['stressed']['collateral_usd'] == 7000
    assert result['stressed']['debt_usd'] == Decimal('6000.1')
    assert result['stressed']['liquidation_eligible']
    assert result['equity_change_usd'] == -3000
    assert result['stressed']['assets'][2]['debt_usd'] == Decimal('0.1')
    assert result['provenance']['block'] == observed['block']
    assert observed == original


def test_switching_shock_changes_arithmetic(observed):
    calm, crash = stress(observed, 0), stress(observed, -0.3)
    assert calm['stressed'] == calm['baseline']
    assert not calm['stressed']['liquidation_eligible']
    assert crash['stressed']['health_factor'] < 1


def test_positive_shock(observed):
    result = stress(observed, 0.1)
    assert result['coverage'] and result['equity_change_usd'] == 1000
    assert result['stressed']['health_factor'] > result['baseline']['health_factor']


def test_hf_mismatch_even_if_rpc_agrees_blocks_stress(observed):
    observed['account']['health_factor_raw'] = str(2*10**18)
    values = [int(v) for v in observed['account'].values()]
    replace_read(observed, 'getUserAccountData(address)', values)
    result = stress(observed)
    assert not result['coverage'] and 'health factor does not reconcile' in result['reason']
    assert result['stressed'] is None


def test_contract_hf_tampering_rejected(observed):
    observed['account']['health_factor_raw'] = '100'
    assert not stress(observed)['coverage']


def test_nonzero_emode_fails_closed():
    rpc = WalletRPC()
    rpc.mode = 1
    result = stress(fetch(rpc))
    assert not result['coverage'] and 'eMode' in result['reason']


def test_disabled_supplies_equity_but_not_hf():
    rpc = WalletRPC()
    rpc.supplied[1] = 1000*10**6
    observed = fetch(rpc)
    result = stress(observed)
    assert result['coverage']
    assert result['baseline']['supplied_usd'] == 11000
    assert result['baseline']['collateral_usd'] == 10000
    assert result['baseline']['equity_usd'] == Decimal('4999.9')
    assert not stress(observed, selected=rpc.reserves[1])['coverage']


def multiple_collateral(convention='weighted thresholds'):
    rpc = WalletRPC()
    rpc.supplied[1] = 1001*10**6
    rpc.flags |= 2 << 2
    original_contract = rpc.contract

    def contract(target, signature, argument):
        if signature == 'getReserveConfigurationData(address)' and int(argument[:64], 16) == int(rpc.reserves[1], 16):
            return abi(6, 7000, 8000, 10500, 1000, 1, 1, 0, 1, 0)
        if signature == 'getUserAccountData(address)':
            c, d = 11001*10**8, 600010000000
            weighted = 10000*10**8*8300 + 1001*10**8*8000
            avg = weighted//c
            hf = weighted*10**18//(10000*d) if convention == 'weighted thresholds' else ((c*avg+5000)//10000)*10**18//d
            return abi(c, d, 0, avg, 7000, hf)
        return original_contract(target, signature, argument)
    rpc.contract = contract
    return fetch(rpc)


@pytest.mark.parametrize('convention', ['weighted thresholds', 'truncated average threshold'])
def test_multiple_collateral_and_rounding_variants(convention):
    observed = multiple_collateral(convention)
    result = stress(observed)
    assert result['coverage'] and result['hf_convention'] == convention
    assert result['stressed']['collateral_usd'] == 8001
    assert result['stressed']['assets'][1]['price_usd'] == 1
    assert abs(result['baseline']['health_factor'] - result['reported_health_factor']) <= result['hf_tolerance']
    switched = stress(observed, selected=observed['assets'][1]['reserve_address'])
    # USDT is both enabled collateral and debt: the same price applies to both.
    assert switched['stressed']['debt_usd'] == Decimal('4200.1')
    assert switched['stressed']['assets'][0]['price_usd'] == 2500


def test_unknown_symbol_does_not_impose_weth_assumptions():
    rpc = WalletRPC()
    rpc.symbols[0] = 'OTHER'
    rpc.prices[0] = 1500*10**8
    result = stress(fetch(rpc), -0.1)
    assert result['coverage'] and result['equity_change_usd'] == -600


@pytest.mark.parametrize('change', [
    lambda o: o.update(complete=False),
    lambda o: o.update(chain_id=10),
    lambda o: o['assets'].pop(),
    lambda o: o['assets'][0].pop('oracle_price_base_raw'),
    lambda o: o['assets'][0].update(oracle_price_base_raw='0'),
    lambda o: o['assets'][0].update(oracle_price_base_raw='1'),
    lambda o: o['assets'][0].update(decimals=37),
    lambda o: o['assets'][0].update(supplied_raw='-1'),
    lambda o: o['assets'][0].update(stable_debt_raw='1'),
    lambda o: o['assets'][0].update(balances_cross_checked=False),
    lambda o: o['assets'][0]['provenance'].update(block_number=999),
    lambda o: o['assets'][0]['provenance'].update(price_oracle='0xwrong'),
    lambda o: o['assets'][0]['reserve_configuration_raw'].__setitem__(2, 10001),
    lambda o: o.update(rpc_evidence=[]),
    lambda o: o['enumerated_reserves'].pop(),
    lambda o: o['base_currency'].update(unit=10**18),
])
def test_incomplete_invalid_unsupported_evidence(observed, change):
    change(observed)
    result = stress(observed)
    assert result['status'] == 'MONITOR / INSUFFICIENT MODEL COVERAGE'
    assert not result['coverage'] and result['baseline'] is None and result['stressed'] is None


def test_inconsistent_block_tags(observed):
    for entry in observed['rpc_evidence']:
        if entry['method'] == 'eth_call':
            entry['params'][1] = 'latest'
            break
    assert not stress(observed)['coverage']


@pytest.mark.parametrize('shock', [-1, -2, 11, 'NaN', 'Infinity', None, True, 'garbage'])
def test_invalid_shocks(observed, shock):
    assert not stress(observed, shock)['coverage']


def test_stale_and_historical_observations(observed):
    original = copy.deepcopy(observed)
    assert not stress(observed, now=datetime.now(timezone.utc)+timedelta(hours=2))['coverage']
    assert original == observed
    observed['historical'] = True
    assert not stress(observed)['coverage']


def test_zero_debt():
    rpc = WalletRPC()
    rpc.debts = [0, 0, 0]
    rpc.flags = 2
    result = stress(fetch(rpc))
    assert result['coverage'] and result['baseline']['health_factor'] is None
    assert result['stressed']['debt_usd'] == 0
    assert not result['stressed']['liquidation_eligible']


def test_tiny_debt_is_not_omitted():
    rpc = WalletRPC()
    rpc.debts = [0, 0, 1]
    rpc.flags = 2 | 16
    result = stress(fetch(rpc))
    assert result['coverage']
    assert result['baseline']['debt_usd'] == Decimal('0.00000001')
    assert result['baseline']['assets'][1]['debt_units'] == Decimal('0.000000000000000001')


def test_empty_and_disabled_collateral():
    rpc = WalletRPC()
    rpc.supplied = rpc.debts = [0, 0, 0]
    rpc.flags = 0
    result = analyze_wallet_stress(fetch(rpc), rpc.reserves[0], -0.3)
    assert not result['coverage']
    rpc.supplied = [10**18, 0, 0]
    result = analyze_wallet_stress(fetch(rpc), rpc.reserves[0], -0.3)
    assert not result['coverage']


def test_wallet_stress_ui_and_synthetic_isolation(observed):
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_string('import streamlit as st\nfrom src.wallet_ui import render_wallet_stress\nrender_wallet_stress(st.session_state.observation)')
    app.session_state.observation = observed
    app.run()
    assert not app.exception
    assert any('MONITOR / STRESS ANALYSIS ONLY' in c.value for c in app.caption)
    assert any('HIGH MODELED RISK' in e.value for e in app.error)
    assert any('coverage: COMPLETE' in caption.value for caption in app.caption)
    assert any(m.label == 'All wallet debt' and m.value == '$6,000.10' for m in app.metric)
    app.slider(key='wallet_stress_shock').set_value(0).run()
    assert not app.exception
    assert any(m.label == 'Change in marked equity' and m.value == '$0.00' for m in app.metric)
    assert app.session_state.observation == observed


def test_wallet_stress_ui_stale_no_quantification(observed):
    from streamlit.testing.v1 import AppTest
    observed['retrieved_at'] = (datetime.now(timezone.utc)-timedelta(hours=2)).isoformat()
    app = AppTest.from_string('import streamlit as st\nfrom src.wallet_ui import render_wallet_stress\nrender_wallet_stress(st.session_state.observation)')
    app.session_state.observation = observed
    app.run()
    assert not app.exception and not app.metric
    assert any('INSUFFICIENT MODEL COVERAGE' in w.value for w in app.warning)


@pytest.mark.parametrize('signature', ['getAssetPrice(address)', 'getReserveConfigurationData(address)', 'getReservesList()'])
def test_malformed_abi_returns_monitor(observed, signature):
    from src.wallet import WALLET_SELECTORS
    for entry in observed['rpc_evidence']:
        if entry['method'] == 'eth_call' and entry['params'][0]['data'].startswith(WALLET_SELECTORS[signature]):
            entry['result'] = '0xbroken'
            break
    assert not stress(observed)['coverage']


@pytest.mark.parametrize('invalid', [None, {}, [], 'invalid'])
def test_invalid_observation_never_crashes(invalid):
    assert not analyze_wallet_stress(invalid, '0x123', -0.3)['coverage']


def test_liquidation_threshold_is_strictly_below_one():
    rpc = WalletRPC()
    rpc.debts = [0, 8300*10**6, 0]
    rpc.flags = 2 | 4
    observed = fetch(rpc)
    result = stress(observed, 0)
    assert result['coverage'] and result['stressed']['health_factor'] == 1
    assert not result['stressed']['liquidation_eligible']
    assert stress(observed, -0.00001)['stressed']['liquidation_eligible']


def test_tiny_debt_hf_mismatch_cannot_hide_in_rounding_allowance():
    rpc = WalletRPC()
    rpc.debts = [0, 0, 1]
    rpc.flags = 2 | 16
    observed = fetch(rpc)
    observed['account']['health_factor_raw'] = str(int(observed['account']['health_factor_raw'])*2)
    replace_read(observed, 'getUserAccountData(address)', [int(v) for v in observed['account'].values()])
    assert not stress(observed)['coverage']


def test_economic_model_never_accepts_a_reported_total_mismatch(observed):
    observed['account']['total_debt_base_raw'] = str(int(observed['account']['total_debt_base_raw'])+100)
    replace_read(observed, 'getUserAccountData(address)', [int(v) for v in observed['account'].values()])
    assert not stress(observed)['coverage']


def test_ambiguous_baseline_discloses_conservative_rounding(observed):
    result = stress(observed)
    assert result['coverage'] and result['hf_convention'] == 'conservative ambiguous rounding'


def ambiguous_boundary_observation():
    rpc = WalletRPC()
    rpc.supplied[1] = 2000*10**6
    rpc.debts[1] = 7409800000
    rpc.flags |= 2 << 2
    original_contract = rpc.contract

    def contract(target, signature, argument):
        if signature == 'getReserveConfigurationData(address)' and int(argument[:64], 16) == int(rpc.reserves[1], 16):
            return abi(6, 7000, 8000, 10500, 1000, 1, 1, 0, 1, 0)
        if signature == 'getUserAccountData(address)':
            # Baseline weighted average is exactly 8250 bps, so both formulas agree.
            collateral, debt = 12000*10**8, 740990000000
            return abi(collateral, debt, 0, 8250, 7000, collateral*8250*10**18//(10000*debt))
        return original_contract(target, signature, argument)
    rpc.contract = contract
    return fetch(rpc)


def test_ambiguous_rounding_straddles_one_without_definitive_claim():
    result = stress(ambiguous_boundary_observation(), '-0.30')
    assert result['coverage'] and result['hf_convention'] == 'conservative ambiguous rounding'
    after = result['stressed']
    assert after['health_factor_min'] < 1 < after['health_factor_max']
    assert after['health_factor'] == after['health_factor_min']
    assert not after['liquidation_eligible'] and after['liquidation_possible']
    assert result['risk_severity'] == 'POSSIBLE_LIQUIDATION_ELIGIBILITY'
    assert len(after['health_factors_by_convention']) == 2


@pytest.mark.parametrize('shock,severity,eligible,possible', [
    ('-0.40', 'DEFINITE_LIQUIDATION_ELIGIBILITY', True, True),
    ('-0.20', 'NO_MODELED_LIQUIDATION_ELIGIBILITY', False, False),
])
def test_ambiguous_range_definite_and_no_eligibility(shock, severity, eligible, possible):
    result = stress(ambiguous_boundary_observation(), shock)
    assert result['coverage'] and result['risk_severity'] == severity
    assert result['stressed']['liquidation_eligible'] is eligible
    assert result['stressed']['liquidation_possible'] is possible
    assert result['execution_decision'] == 'UNAVAILABLE' and not result['current_actionable']


def archived_observation():
    rpc = WalletRPC()
    timestamp = int((datetime.now(timezone.utc)-timedelta(hours=2)).timestamp())
    rpc.block['timestamp'] = hex(timestamp)
    observed = fetch(rpc, block_number=291)
    original_retrieval = datetime.fromtimestamp(timestamp+30, timezone.utc).isoformat()
    observed['retrieved_at'] = original_retrieval
    for asset in observed['assets']:
        asset['provenance']['retrieved_at'] = original_retrieval
    return observed


def test_verified_historical_replay_preserves_dates_and_validation():
    observed = archived_observation()
    original = copy.deepcopy(observed)
    assert not stress(observed)['coverage']
    result = stress(observed, historical_replay=True)
    assert result['coverage'] and result['historical_replay']
    assert result['source_label'] == 'HISTORICAL / NOT LIVE'
    assert result['risk_severity'] == 'DEFINITE_LIQUIDATION_ELIGIBILITY'
    assert result['execution_decision'] == 'UNAVAILABLE' and not result['current_actionable']
    assert result['equity_change_usd'] == -3000
    assert result['provenance']['retrieved_at'] == original['retrieved_at']
    assert observed == original
    assert not stress(observed)['coverage']  # replay never promotes source freshness


@pytest.mark.parametrize('change', [
    lambda o: o.update(complete=False),
    lambda o: o.update(rpc_evidence=[]),
    lambda o: o['assets'].pop(),
    lambda o: o['assets'][0].update(oracle_price_base_raw='0'),
    lambda o: o['account'].update(health_factor_raw='100'),
    lambda o: o['emode'].update(category_id=1),
    lambda o: o['block'].update(hash='0x'+'ff'*32),
    lambda o: o.update(retrieved_at='invalid'),
    lambda o: o.update(retrieved_at=datetime.now().isoformat()),
    lambda o: o.update(retrieved_at=(datetime.now(timezone.utc)+timedelta(hours=2)).isoformat()),
    lambda o: o.update(retrieved_at=(datetime.now(timezone.utc)-timedelta(hours=3)).isoformat()),
    lambda o: o['block'].update(timestamp=int((datetime.now(timezone.utc)+timedelta(hours=2)).timestamp())),
])
def test_historical_replay_does_not_accept_unverified_or_unreliable_observations(change):
    observed = archived_observation()
    change(observed)
    assert not stress(observed, historical_replay=True)['coverage']


def test_replay_mode_requires_explicit_boolean(observed):
    assert not stress(observed, historical_replay='yes')['coverage']


def test_old_current_observation_requires_opt_in_replay():
    observed = archived_observation()
    observed['historical'] = False
    assert not stress(observed)['coverage']
    result = stress(observed, historical_replay=True)
    assert result['coverage'] and result['source_label'] == 'HISTORICAL / NOT LIVE'


def test_possible_liquidation_ui_exposes_range_and_uncertainty():
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_string('import streamlit as st\nfrom src.wallet_ui import render_wallet_stress\nrender_wallet_stress(st.session_state.observation)')
    app.session_state.observation = ambiguous_boundary_observation()
    app.run()
    assert not app.exception and not app.error
    assert any('POSSIBLE LIQUIDATION ELIGIBILITY' in w.value for w in app.warning)
    assert any('Stressed HF range:' in c.value for c in app.caption)
    assert any('Execution decision: UNAVAILABLE' in c.value for c in app.caption)


def test_historical_replay_ui_is_opt_in_and_not_live():
    from streamlit.testing.v1 import AppTest
    observed = archived_observation()
    app = AppTest.from_string('import streamlit as st\nfrom src.wallet_ui import render_wallet_stress\nrender_wallet_stress(st.session_state.observation)')
    app.session_state.observation = observed
    app.run()
    assert not app.exception and not app.metric
    app.checkbox(key='wallet_stress_replay').check().run()
    assert not app.exception and app.metric
    assert any('HISTORICAL / NOT LIVE' in w.value for w in app.warning)
    assert any('HIGH MODELED RISK' in e.value for e in app.error)
    assert any(observed['retrieved_at'] in c.value for c in app.caption)
    app.checkbox(key='wallet_stress_replay').uncheck().run()
    assert not app.exception and not app.metric
