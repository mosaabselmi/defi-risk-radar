"""Local, digest-pinned historical demo; no retrieval or financial mathematics.

The digest protects the repository capture's integrity, not its independent
blockchain authenticity. New captures require a new version, never overwriting v1.
"""
import hashlib
import json
from pathlib import Path

from src.sources import SourceError, _contract_code, _decode_address
from src.wallet import _symbol, _units
from src.wallet_dependencies import build_wallet_graph
from src.wallet_risk import _read, analyze_wallet_stress


FIXTURE_PATH = Path(__file__).resolve().parents[1] / 'data/demo/aave_wallet_v1.json'
FIXTURE_VERSION = 1
EXPECTED_SHA256 = 'e3ebae4fbb7394c41825fae29b7c09d76b304bd647dba5e6c22f0572405f9b42'
EXAMPLE_ACCOUNT = '0x24a416e5c00c7b47a5f8a300984579b53919330b'
EXAMPLE_BLOCK = {'number': 26165176,
                 'hash': '0x7df6f9897ddb70d3f3ddc310190bddaa0a2d2873754f1bb74c0a227cec773193',
                 'timestamp': 1791673127}
EXAMPLE_RETRIEVED_AT = '2026-10-10T22:58:58.135243+00:00'
MAX_FIXTURE_BYTES = 2_000_000


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SourceError('Historical fixture contains duplicate JSON keys.')
        result[key] = value
    return result


def _validate_observation(observation):
    """Reuse graph and stress validators, plus captured metadata/market checks."""
    if (observation['wallet_address'] != EXAMPLE_ACCOUNT or observation['block'] != EXAMPLE_BLOCK
            or observation['retrieved_at'] != EXAMPLE_RETRIEVED_AT or observation['empty'] is not False):
        raise SourceError('Historical example account, block or observation provenance mismatch.')
    reads = observation['rpc_evidence']
    if (not reads or any(e.get('jsonrpc') != '2.0' or type(e.get('id')) is not int
                         or 'error' in e or 'result' not in e for e in reads)):
        raise SourceError('Historical example requires successful raw RPC evidence.')
    chain_reads = [e for e in reads if e['method'] == 'eth_chainId']
    if len(chain_reads) != 1 or chain_reads[0]['params'] != [] or int(chain_reads[0]['result'], 16) != 1:
        raise SourceError('Historical example chain evidence mismatch.')
    provider = observation['addresses_provider']
    for signature, key in [('getPool()', 'pool'), ('getPoolDataProvider()', 'data_provider'),
                           ('getPriceOracle()', 'oracle')]:
        if _decode_address(_read(observation, provider, signature)) != observation[key]:
            raise SourceError('Historical example market mapping mismatch.')
    for key in ('pool', 'data_provider', 'oracle'):
        if _decode_address(_read(observation, observation[key], 'ADDRESSES_PROVIDER()')) != provider:
            raise SourceError('Historical example belongs to a different Aave market.')
    if _decode_address(_read(observation, observation['data_provider'], 'POOL()')) != observation['pool']:
        raise SourceError('Historical example data-provider mapping mismatch.')
    targets = {provider, observation['pool'], observation['data_provider'], observation['oracle']}
    for asset in observation['assets']:
        address, decimals = asset['reserve_address'], asset['decimals']
        if _symbol(_read(observation, address, 'symbol()')) != asset['symbol']:
            raise SourceError('Historical example token symbol mismatch.')
        for raw_key, units_key, scale in [('supplied_raw', 'supplied_units', decimals),
                                          ('variable_debt_raw', 'variable_debt_units', decimals),
                                          ('oracle_price_base_raw', 'oracle_price_usd', 8)]:
            if _units(int(asset[raw_key]), scale) != asset[units_key]:
                raise SourceError('Historical example normalized token metadata mismatch.')
        targets.update((address, asset['a_token_address'], asset['variable_debt_token_address']))
    for target in targets:
        codes = [e['result'] for e in reads if e['method'] == 'eth_getCode'
                 and e['params'] == [target, hex(observation['block']['number'])]]
        if len(codes) != 1:
            raise SourceError('Historical example is missing contract-code evidence.')
        _contract_code(codes[0])
    graph = build_wallet_graph(observation)
    if any(m['status'] == 'inconsistent' for m in graph.graph['oracle_sources'].values()):
        raise SourceError('Historical example contains contradictory oracle-source evidence.')
    selected = next(a['reserve_address'] for a in observation['assets']
                    if a['collateral_enabled'] and int(a['supplied_raw']))
    result = analyze_wallet_stress(observation, selected, 0, historical_replay=True)
    if not result['coverage']:
        raise SourceError('Historical example failed stress-model validation: ' + result['reason'])
    # Display totals must agree with the independently validated raw accounting.
    account = observation['account']
    for raw_key, label, scale in [('total_collateral_base_raw', 'collateral_usd', 8),
                                  ('total_debt_base_raw', 'debt_usd', 8),
                                  ('health_factor_raw', 'health_factor', 18)]:
        if _units(int(account[raw_key]), scale) != observation[label]:
            raise SourceError('Historical example display totals mismatch.')
    return observation


def load_historical_example():
    """Load only the registered immutable v1 capture. Never call an RPC/API."""
    try:
        with FIXTURE_PATH.open('rb') as handle:
            raw = handle.read(MAX_FIXTURE_BYTES + 1)
        if len(raw) > MAX_FIXTURE_BYTES:
            raise SourceError('Historical fixture exceeds the supported size.')
        if hashlib.sha256(raw).hexdigest() != EXPECTED_SHA256:
            raise SourceError('Historical fixture SHA-256 integrity check failed; no position loaded.')
        observation = json.loads(raw, object_pairs_hook=_unique_object)
        return _validate_observation(observation)
    except SourceError:
        raise
    except (OSError, ValueError, TypeError, KeyError, IndexError, StopIteration, ArithmeticError) as exc:
        raise SourceError('Historical example is unavailable or incomplete; no position loaded.') from exc
