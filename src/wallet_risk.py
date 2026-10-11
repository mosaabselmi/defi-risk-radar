"""Pure, fail-closed stress arithmetic for complete dated Aave wallet evidence.

Reference: aave-dao/aave-v3-origin GenericLogic.calculateUserAccountData.
Supports category zero only. No network access, persistence or exit decisions.
"""
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext, ROUND_FLOOR, ROUND_CEILING

from src.sources import SourceError, _words
from src.wallet import MAX_UINT256, WALLET_SELECTORS, wallet_quality_issues, _address_array


class CoverageError(ValueError):
    pass


def _integer(value, maximum=MAX_UINT256):
    if isinstance(value, bool) or not re.fullmatch(r'[0-9]+', str(value)):
        raise CoverageError('Invalid unsigned integer evidence.')
    result = int(value)
    if result > maximum:
        raise CoverageError('Integer evidence exceeds supported range.')
    return result


def _read(observation, target, signature, argument=''):
    data = WALLET_SELECTORS[signature] + argument
    matches = [entry['result'] for entry in observation['rpc_evidence']
               if entry.get('method') == 'eth_call' and entry['params'][0] == {'to': target, 'data': data}]
    if len(matches) != 1:
        raise CoverageError('Missing or ambiguous pinned contract evidence: ' + signature)
    return matches[0]


def _number(value):
    if isinstance(value, bool):
        raise CoverageError('Invalid market shock.')
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise CoverageError('Invalid market shock.')
    if not number.is_finite() or not Decimal('-1') < number <= Decimal('10'):
        raise CoverageError('Market shock must be greater than -100% and at most +1000%.')
    return number


def _valuation(assets, shock_asset=None, shock=Decimal(0)):
    collateral = debt = weighted = supply = Decimal(0)
    rows = []
    for asset in assets:
        price = Decimal(asset['price']) * (1 + shock if asset['address'] == shock_asset else 1)
        supplied = Decimal(asset['supplied']) * price / 10**asset['decimals']
        borrowed = Decimal(asset['borrowed']) * price / 10**asset['decimals']
        c = supplied.to_integral_value(rounding=ROUND_FLOOR) if asset['enabled'] else Decimal(0)
        d = borrowed.to_integral_value(rounding=ROUND_CEILING)
        collateral += c
        debt += d
        weighted += c * asset['threshold']
        supply += supplied
        rows.append({'asset': asset['symbol'], 'reserve_address': asset['address'],
                     'supplied_units': Decimal(asset['supplied']) / 10**asset['decimals'],
                     'debt_units': Decimal(asset['borrowed']) / 10**asset['decimals'],
                     'price_usd': price / 10**8, 'supplied_usd': supplied / 10**8,
                     'enabled_collateral_usd': c / 10**8, 'debt_usd': d / 10**8,
                     'collateral_enabled': asset['enabled'],
                     'liquidation_threshold_bps': asset['threshold']})
    return {'collateral': collateral, 'debt': debt, 'weighted': weighted,
            'supplied': supply, 'rows': rows}


def _hf(values, convention):
    if not values['debt']:
        return None
    if convention == 'weighted thresholds':
        return values['weighted'] / 10000 / values['debt']
    if convention == 'conservative ambiguous rounding':
        return min(_hf(values, name) for name in ('weighted thresholds', 'truncated average threshold'))
    average = (values['weighted'] / values['collateral']).to_integral_value(rounding=ROUND_FLOOR) if values['collateral'] else Decimal(0)
    # Older Aave implementations truncate the weighted-average threshold first.
    adjusted = ((values['collateral'] * average + 5000) / 10000).to_integral_value(rounding=ROUND_FLOOR)
    return adjusted / values['debt']


def _public(values, convention):
    conventions = ('weighted thresholds', 'truncated average threshold') if convention == 'conservative ambiguous rounding' else (convention,)
    candidates = {name: _hf(values, name) for name in conventions}
    finite = [value for value in candidates.values() if value is not None]
    low, high = (min(finite), max(finite)) if finite else (None, None)
    definite = high is not None and high < 1
    possible = low is not None and low < 1
    severity = ('NO_DEBT' if low is None else 'DEFINITE_LIQUIDATION_ELIGIBILITY' if definite
                else 'POSSIBLE_LIQUIDATION_ELIGIBILITY' if possible else 'NO_MODELED_LIQUIDATION_ELIGIBILITY')
    return {'collateral_usd': values['collateral'] / 10**8,
            'supplied_usd': values['supplied'] / 10**8,
            'debt_usd': values['debt'] / 10**8,
            'equity_usd': (values['supplied'] - values['debt']) / 10**8,
            'health_factor': low, 'health_factor_min': low, 'health_factor_max': high,
            'health_factors_by_convention': candidates,
            'liquidation_eligible': definite, 'liquidation_possible': possible,
            'liquidation_severity': severity,
            'assets': values['rows']}


def analyze_wallet_stress(observation, collateral_address, shock, now=None, *, historical_replay=False):
    """Return MONITOR and no arithmetic if coverage/evidence cannot be reconciled.

    shock is a fractional change (e.g. -0.30). Prices for every occurrence of the
    selected asset change, including any debt in that same asset. Other prices,
    balances, thresholds and interest accrual are held constant. Historical replay
    is an explicit analytical mode, never a freshness upgrade or current decision.
    """
    unavailable = {'status': 'MONITOR / INSUFFICIENT MODEL COVERAGE', 'coverage': False,
                   'coverage_status': 'INSUFFICIENT', 'risk_severity': 'UNAVAILABLE',
                   'execution_decision': 'UNAVAILABLE', 'current_actionable': False,
                   'baseline': None, 'stressed': None}
    try:
        with localcontext() as context:
            context.prec = 120
            return _analyze(observation, collateral_address, _number(shock), now, historical_replay)
    except (SourceError, CoverageError, KeyError, TypeError, ValueError, IndexError, AttributeError, ArithmeticError) as exc:
        return {**unavailable, 'reason': str(exc) or 'Invalid or incomplete wallet evidence.'}


def _replay_timestamps(observation, now):
    """Permit old evidence, while rejecting malformed, future or reversed dates."""
    now = now or datetime.now(timezone.utc)
    retrieved = datetime.fromisoformat(observation['retrieved_at'])
    block_time = _integer(observation['block']['timestamp'])
    if (retrieved.tzinfo is None or now.tzinfo is None or not block_time
            or retrieved.timestamp() > now.timestamp() + 60 or block_time > now.timestamp() + 60
            or retrieved.timestamp() < block_time - 60 or type(observation['historical']) is not bool):
        raise CoverageError('Historical replay requires reliable original block and observation timestamps.')


def _analyze(observation, selected, shock, now, historical_replay):
    if observation.get('complete') is not True or observation['chain_id'] != 1:
        raise CoverageError('A complete verified Ethereum wallet import is required.')
    if type(historical_replay) is not bool:
        raise CoverageError('Historical replay must be explicitly selected as a boolean mode.')
    if historical_replay:
        _replay_timestamps(observation, now)
    else:
        issues = wallet_quality_issues(observation, now)
        if issues:
            raise CoverageError(' '.join(issues))
    if observation['base_currency'] != {'address': '0x' + '0'*40, 'label': 'USD', 'unit': 10**8, 'decimals': 8}:
        raise CoverageError('Unsupported Aave base currency.')
    if observation['emode']['category_id'] != 0:
        raise CoverageError('Nonzero eMode is observed but not modeled; effective category coverage is insufficient.')
    block = observation['block']
    if not re.fullmatch(r'0x[0-9a-f]{64}', block['hash']) or _integer(block['number']) == 0:
        raise CoverageError('Invalid pinned block evidence.')
    for entry in observation['rpc_evidence']:
        if entry.get('method') in ('eth_call', 'eth_getCode') and entry['params'][1] != hex(block['number']):
            raise CoverageError('RPC evidence contains inconsistent block tags.')
    headers = [entry['result'] for entry in observation['rpc_evidence'] if entry.get('method') == 'eth_getBlockByNumber']
    if len(headers) != 2 or any(int(h['number'], 16) != block['number'] or h['hash'].lower() != block['hash']
                               or int(h['timestamp'], 16) != block['timestamp'] for h in headers):
        raise CoverageError('Block header evidence does not reconcile.')
    if _words(_read(observation, observation['oracle'], 'BASE_CURRENCY_UNIT()'), 1)[0] != 10**8 or int(_read(observation, observation['oracle'], 'BASE_CURRENCY()'), 16) != 0:
        raise CoverageError('Oracle base-currency evidence does not reconcile.')
    arg = observation['wallet_address'][2:].rjust(64, '0')
    if _words(_read(observation, observation['pool'], 'getUserEMode(address)', arg), 1)[0] != 0:
        raise CoverageError('eMode evidence disagrees with account metadata.')
    account = observation['account']
    keys = ('total_collateral_base_raw', 'total_debt_base_raw', 'available_borrows_base_raw',
            'liquidation_threshold_bps', 'ltv_bps', 'health_factor_raw')
    raw_account = [_integer(account[key]) for key in keys]
    if _words(_read(observation, observation['pool'], 'getUserAccountData(address)', arg), 6) != raw_account:
        raise CoverageError('Account totals or HF disagree with pinned RPC evidence.')
    flags = _integer(observation['user_configuration_raw'])
    if _words(_read(observation, observation['pool'], 'getUserConfiguration(address)', arg), 1)[0] != flags:
        raise CoverageError('Collateral configuration disagrees with pinned RPC evidence.')
    enumeration = observation['enumerated_reserves']
    addresses = [entry['reserve_address'] for entry in enumeration]
    if len(addresses) != len(set(addresses)):
        raise CoverageError('Duplicate reserve enumeration.')
    if set(_address_array(_read(observation, observation['pool'], 'getReservesList()'))) != set(addresses):
        raise CoverageError('Reserve enumeration differs from pinned contract evidence.')
    count = _words(_read(observation, observation['pool'], 'getReservesCount()'), 1)[0]
    if not 1 <= count <= 128 or count != observation['reserve_count_including_dropped'] or flags >> (2 * count):
        raise CoverageError('Incomplete reserve ID coverage.')
    seen_ids = set()
    by_id = [_words(_read(observation, observation['pool'], 'getReserveAddressById(uint16)', format(i, '064x')), 1)[0]
             for i in range(count)]
    if {int(address, 16) for address in addresses} != {address for address in by_id if address}:
        raise CoverageError('Incomplete initialized reserve coverage.')
    if any(not address and ((flags >> (2 * i)) & 3) for i, address in enumerate(by_id)):
        raise CoverageError('Unsupported flags on a dropped reserve.')
    for entry in enumeration:
        reserve_id = _integer(entry['reserve_id'], count - 1)
        if reserve_id in seen_ids:
            raise CoverageError('Duplicate reserve IDs.')
        seen_ids.add(reserve_id)
        if int(_read(observation, observation['pool'], 'getReserveAddressById(uint16)', format(reserve_id, '064x')), 16) != int(entry['reserve_address'], 16):
            raise CoverageError('Reserve IDs differ from pinned contract evidence.')
        raw = _words(_read(observation, observation['data_provider'], 'getUserReserveData(address,address)',
                           entry['reserve_address'][2:].rjust(64, '0') + arg), 9)
        if raw[:3] != [_integer(entry['supplied_raw']), 0, _integer(entry['variable_debt_raw'])] or raw[8] != int(entry['collateral_enabled']):
            raise CoverageError('Reserve enumeration balances disagree with pinned evidence.')
        if raw[3] or raw[5] or raw[7]:
            raise CoverageError('Unsupported legacy stable-debt evidence.')
    # Independently ensure every nonzero exposure in the imported enumeration is retained.
    active = {entry['reserve_address']: entry for entry in enumeration
              if _integer(entry['supplied_raw']) or _integer(entry['variable_debt_raw'])}
    if {a['reserve_address'] for a in observation['assets']} != set(active):
        raise CoverageError('Partial asset coverage; all supplied and borrowed reserves are required.')
    assets = []
    for a in observation['assets']:
        address = a['reserve_address']
        if any(item['address'] == address for item in assets):
            raise CoverageError('Duplicate wallet asset.')
        entry = active[address]
        reserve_id = _integer(a['reserve_id'], 127)
        supplied, borrowed = _integer(a['supplied_raw']), _integer(a['variable_debt_raw'])
        if (entry['reserve_id'] != reserve_id or _integer(entry['supplied_raw']) != supplied
                or _integer(entry['variable_debt_raw']) != borrowed):
            raise CoverageError('Asset balances disagree with complete enumeration.')
        enabled = a['collateral_enabled']
        if type(enabled) is not bool or enabled != entry['collateral_enabled'] or enabled != bool(flags & (2 << (2 * reserve_id))):
            raise CoverageError('Collateral-enabled flags disagree.')
        if bool(borrowed) != bool(flags & (1 << (2 * reserve_id))) or _integer(a['stable_debt_raw']):
            raise CoverageError('Unsupported debt configuration.')
        if a['balances_cross_checked'] is not True:
            raise CoverageError('Token balances were not cross-checked.')
        provenance = a['provenance']
        if (provenance['chain_id'] != 1 or provenance['block_number'] != block['number']
                or provenance['block_hash'] != block['hash'] or provenance['price_oracle'] != observation['oracle']
                or provenance['metadata_contract'] != address or provenance['block_timestamp'] != block['timestamp']
                or provenance['retrieved_at'] != observation['retrieved_at']):
            raise CoverageError('Asset provenance differs from the pinned wallet observation.')
        argument = address[2:].rjust(64, '0')
        decimals = _integer(a['decimals'], 36)
        if _words(_read(observation, address, 'decimals()'), 1)[0] != decimals:
            raise CoverageError('Token decimals disagree with contract evidence.')
        configuration = [_integer(v) for v in a['reserve_configuration_raw']]
        if _words(_read(observation, observation['data_provider'], 'getReserveConfigurationData(address)', argument), 10) != configuration:
            raise CoverageError('Reserve configuration differs from pinned evidence.')
        if configuration[0] != decimals or not 0 <= configuration[1] <= configuration[2] <= 10000:
            raise CoverageError('Unsupported reserve liquidation configuration.')
        price = _integer(a['oracle_price_base_raw'])
        if not price or _words(_read(observation, observation['oracle'], 'getAssetPrice(address)', argument), 1)[0] != price:
            raise CoverageError('Missing or inconsistent pinned Aave oracle price.')
        user = _words(_read(observation, observation['data_provider'], 'getUserReserveData(address,address)', argument + arg), 9)
        if user[:3] != [supplied, 0, borrowed] or user[8] != int(enabled):
            raise CoverageError('User reserve balances disagree with pinned evidence.')
        for target, balance in ((a['a_token_address'], supplied), (a['variable_debt_token_address'], borrowed)):
            if _words(_read(observation, target, 'balanceOf(address)', arg), 1)[0] != balance:
                raise CoverageError('Token balance cross-check failed.')
        assets.append({'address': address, 'symbol': a['symbol'], 'decimals': decimals,
                       'supplied': supplied, 'borrowed': borrowed, 'enabled': enabled,
                       'price': price, 'threshold': configuration[2]})
    if not any(a['address'] == selected and (a['enabled'] and a['supplied'] or a['borrowed']) for a in assets):
        raise CoverageError('Select a nonzero enabled collateral or borrowed reserve for the market shock.')
    baseline = _valuation(assets)
    # Same strict base-unit tolerance as importer; no USD-sized or percentage allowance.
    rounding = Decimal(2 * len(assets))
    if abs(baseline['collateral'] - raw_account[0]) > rounding or abs(baseline['debt'] - raw_account[1]) > rounding:
        raise CoverageError('Reconstructed collateral/debt totals do not reconcile.')
    avg = int(baseline['weighted'] / baseline['collateral']) if baseline['collateral'] else 0
    if avg != raw_account[3]:
        raise CoverageError('Effective weighted liquidation threshold does not reconcile.')
    reported = None if raw_account[5] == MAX_UINT256 else Decimal(raw_account[5]) / 10**18
    if not baseline['debt']:
        if reported is not None:
            raise CoverageError('Zero debt must have the Aave unlimited-HF sentinel.')
        convention, tolerance = 'weighted thresholds', Decimal(0)
    else:
        if reported is None:
            raise CoverageError('Nonzero debt has an invalid unlimited-HF sentinel.')
        # Propagate only the ACTUAL reconciled base-unit discrepancies, rather
        # than the importer's maximum allowance (unsafe for extremely tiny debt).
        # 1e-12 is an absolute WAD-arithmetic allowance, not a percentage band.
        tolerance = (abs(baseline['collateral'] - raw_account[0]) +
                     abs(baseline['debt'] - raw_account[1]) * abs(reported)) / baseline['debt'] + Decimal('1e-12')
        matching = [name for name in ('weighted thresholds', 'truncated average threshold')
                    if abs(_hf(baseline, name) - reported) <= tolerance]
        if not matching:
            raise CoverageError('Baseline health factor does not reconcile with Pool.getUserAccountData().')
        # A single baseline cannot identify a deployment version when both
        # formulas agree. Use the lower stress HF, and disclose that ambiguity.
        convention = matching[0] if len(matching) == 1 else 'conservative ambiguous rounding'
    stressed = _valuation(assets, selected, shock)
    before, after = _public(baseline, convention), _public(stressed, convention)
    return {'status': 'MONITOR / STRESS ANALYSIS ONLY', 'coverage': True,
            'coverage_status': 'COMPLETE', 'risk_severity': after['liquidation_severity'],
            'execution_decision': 'UNAVAILABLE', 'current_actionable': False,
            'historical_replay': historical_replay,
            'source_label': 'HISTORICAL / NOT LIVE' if historical_replay else 'DATED CURRENT OBSERVATION',
            'reason': 'No STAY/EXIT decision: wallet exit costs and execution feasibility are not modeled.',
            'baseline': before, 'stressed': after, 'equity_change_usd': after['equity_usd'] - before['equity_usd'],
            'reported_health_factor': reported, 'hf_tolerance': tolerance, 'hf_convention': convention,
            'shock': shock, 'selected_reserve': selected,
            'provenance': {'chain_id': 1, 'block': dict(block), 'retrieved_at': observation['retrieved_at'],
                           'oracle': observation['oracle'], 'wallet_address': observation['wallet_address']}}
