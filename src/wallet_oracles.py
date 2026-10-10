"""Supplementary pinned Aave mappings; configured addresses do not prove feed health.

Official ABI: https://github.com/aave-dao/aave-v3-origin/blob/main/src/contracts/interfaces/IAaveOracle.sol
getSourceOfAsset(address) -> address, selector 0x92bf2be0 (existing verified adapter).
No portfolio accounting, price substitution, writes or snapshot persistence.
"""
from copy import deepcopy
from datetime import datetime, timezone

from src.sources import SELECTORS, SourceError, _decode_address


METHOD = 'getSourceOfAsset(address)'


def _request(oracle, asset, block):
    return [{'to': oracle, 'data': SELECTORS[METHOD] + asset[2:].rjust(64, '0')}, hex(block['number'])]


def _check_header(read, block):
    raw = read['result']
    if (read.get('jsonrpc') != '2.0' or type(read.get('id')) is not int or 'error' in read
            or read['method'] != 'eth_getBlockByNumber' or read['params'] != [hex(block['number']), False]
            or int(raw['number'], 16) != block['number'] or raw['hash'].lower() != block['hash']
            or int(raw['timestamp'], 16) != block['timestamp']):
        raise SourceError('Supplementary source block evidence is inconsistent.')


def discover_oracle_sources(rpc, observation):
    """One bounded sequential call per distinct active asset, two header checks.

    The supplied RPC adapter owns explicit timeout/request/retry limits. Invalid
    reads remain recorded for inspection, outside mandatory wallet RPC evidence.
    """
    block, oracle = observation['block'], observation['oracle']
    assets = list(dict.fromkeys(a['reserve_address'] for a in observation['assets']))
    entries = []
    for asset in assets:
        entries.append({'chain_id': 1, 'asset': asset, 'oracle': oracle, 'method': METHOD,
                        'calldata': _request(oracle, asset, block)[0]['data'],
                        'block_number': block['number'], 'block_hash': block['hash'],
                        'status': 'unavailable', 'source_address': None, 'rpc_evidence': None,
                        'reason': 'Source discovery did not complete.'})
    report = {'chain_id': 1, 'oracle': oracle, 'block': dict(block), 'entries': entries,
              'header_evidence': [], 'rpc_host': rpc.host, 'feed_health_verified': False}
    if not assets:
        report['retrieved_at'] = datetime.now(timezone.utc).isoformat()
        return report
    try:
        rpc.read('eth_getBlockByNumber', [hex(block['number']), False])
        report['header_evidence'].append(deepcopy(rpc.evidence[-1]))
        _check_header(report['header_evidence'][0], block)
        for entry in entries:
            before = len(rpc.evidence)
            try:
                raw = rpc.read('eth_call', _request(oracle, entry['asset'], block))
                entry['rpc_evidence'] = deepcopy(rpc.evidence[-1])
                source = _decode_address(raw, allow_zero=True)
                entry.update(source_address=source,
                             status='verified' if int(source, 16) else 'zero_source',
                             reason='Configured address only; feed health/freshness unverified.'
                             if int(source, 16) else 'No direct configured source; fallback unresolved.')
            except SourceError as exc:
                if len(rpc.evidence) > before:
                    entry['rpc_evidence'] = deepcopy(rpc.evidence[-1])
                    entry['status'] = 'inconsistent'
                entry['reason'] = str(exc).replace('; no complete position imported.', '')
        rpc.read('eth_getBlockByNumber', [hex(block['number']), False])
        report['header_evidence'].append(deepcopy(rpc.evidence[-1]))
        _check_header(report['header_evidence'][-1], block)
    except (SourceError, KeyError, TypeError, ValueError, AttributeError):
        for entry in entries:
            entry.update(status='unavailable', source_address=None,
                         reason='Supplementary block confirmation unavailable or inconsistent; mapping not verified.')
    report.update(retrieved_at=datetime.now(timezone.utc).isoformat(),
                  transport={'http_requests': rpc.http_requests, 'retries': rpc.retries,
                             'max_seconds': rpc.max_seconds, 'max_requests': rpc.max_requests,
                             'timeout_seconds': rpc.timeout_seconds, 'diagnostics': deepcopy(rpc.diagnostics)})
    return report


def validated_source_mappings(observation):
    """Revalidate supplemental provenance on consumption; fail closed per mapping.

    Legacy observations with mapping reads in mandatory evidence remain supported.
    Contradictory optional evidence does not invalidate independent balance/price
    evidence, but it can never become a verified graph relationship.
    """
    block, oracle = observation['block'], observation['oracle']
    assets = [a['reserve_address'] for a in observation['assets']]
    report = observation.get('oracle_sources')
    result = {a: {'status': 'unavailable', 'source_address': None, 'rpc_evidence': None,
                  'reason': 'Source mapping not retrieved.'} for a in assets}
    report_valid = True
    if report is not None:
        try:
            if (type(report['chain_id']) is not int or report['chain_id'] != 1
                    or report['oracle'] != oracle or report['block'] != block
                    or report.get('feed_health_verified') is not False):
                raise SourceError('Source provenance mismatch.')
            if assets:
                # Missing confirmation is an expected optional transport failure.
                # No positive/zero mapping may verify without both headers.
                if len(report['header_evidence']) != 2 and (len(report['header_evidence']) > 2 or
                        any(e.get('status') != 'unavailable' or e.get('source_address') is not None
                            for e in report['entries'])):
                    raise SourceError('Source block confirmation missing.')
                for header in report['header_evidence']:
                    _check_header(header, block)
            if not isinstance(report['entries'], list) or any(e['asset'] not in assets for e in report['entries']):
                raise SourceError('Unexpected source assets.')
        except (SourceError, KeyError, TypeError, ValueError, AttributeError):
            report_valid = False
    for asset in assets:
        legacy = [r for r in observation['rpc_evidence'] if r.get('method') == 'eth_call'
                  and r.get('params', [None])[0] == _request(oracle, asset, block)[0]]
        entries = [] if report is None or not report_valid else [e for e in report['entries'] if e['asset'] == asset]
        try:
            if not report_valid:
                raise SourceError('Supplementary source provenance or block confirmation is inconsistent.')
            if len(entries) > 1 or len(legacy) > 1:
                raise SourceError('Duplicate or contradictory source observations.')
            entry = entries[0] if entries else None
            reads = list(legacy)
            if entry:
                if (type(entry['chain_id']) is not int or entry['chain_id'] != 1
                        or entry['oracle'] != oracle or entry['method'] != METHOD
                        or entry['calldata'] != _request(oracle, asset, block)[0]['data']
                        or entry['block_number'] != block['number'] or entry['block_hash'] != block['hash']):
                    raise SourceError('Source entry provenance mismatch.')
                if entry['status'] in ('verified', 'zero_source'):
                    reads.append(entry['rpc_evidence'])
                elif entry['status'] not in ('unavailable',):
                    raise SourceError('Source response was malformed or inconsistent.')
                elif entry.get('source_address') is not None:
                    raise SourceError('Unreadable source claims a configured address.')
                result[asset] = deepcopy(entry)
            decoded = []
            for read in reads:
                if (read['method'] != 'eth_call' or read['params'] != _request(oracle, asset, block)
                        or read.get('jsonrpc') != '2.0' or type(read.get('id')) is not int or 'error' in read):
                    raise SourceError('Invalid source RPC evidence.')
                decoded.append(_decode_address(read['result'], allow_zero=True))
            if decoded:
                if len(set(decoded)) != 1 or entry and entry['status'] in ('verified', 'zero_source') and (
                        entry['source_address'] != decoded[0]
                        or entry['status'] != ('verified' if int(decoded[0], 16) else 'zero_source')):
                    raise SourceError('Configured source disagrees with raw RPC evidence.')
                result[asset].update(status='verified' if int(decoded[0], 16) else 'zero_source',
                                     source_address=decoded[0], rpc_evidence=deepcopy(reads[-1]),
                                     reason='Configured address only; feed health/freshness unverified.'
                                     if int(decoded[0], 16) else 'No direct configured source; fallback unresolved.')
            elif entry and entry['status'] in ('verified', 'zero_source'):
                raise SourceError('Configured mapping lacks RPC evidence.')
        except (SourceError, KeyError, TypeError, ValueError, AttributeError):
            result[asset] = {'status': 'inconsistent', 'source_address': None, 'rpc_evidence': None,
                             'reason': 'Mapping evidence is incomplete, malformed or contradictory; not verified.'}
    return result
