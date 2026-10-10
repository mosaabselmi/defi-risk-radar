"""Deterministic optional source discovery; no live data or snapshot writes."""
import copy

import networkx as nx
import pytest
import requests

from src.wallet import _RPC, WALLET_SELECTORS
from src.wallet_oracles import discover_oracle_sources, validated_source_mappings
from src.wallet_dependencies import analyze_wallet_incident, build_wallet_graph
from src.wallet_risk import analyze_wallet_stress
from src.wallet_viz import plot_wallet_graph, wallet_layout
from tests.test_wallet import WalletRPC, fetch, addr, abi, Response


class SourceRPC(WalletRPC):
    def __init__(self):
        super().__init__()
        self.sources = [addr(500), addr(501), addr(502)]
        self.source_failures = {}
        self.source_calls = []
        self.optional_reorg = False

    def reply(self, request):
        if request['method'] == 'eth_call' and request['params'][0]['data'].startswith(WALLET_SELECTORS['getSourceOfAsset(address)']):
            self.source_calls.append(copy.deepcopy(request))
            asset = addr(int(request['params'][0]['data'][10:], 16))
            index = self.reserves.index(asset)
            failure = self.source_failures.get(index)
            if isinstance(failure, Exception):
                raise failure
            if isinstance(failure, dict):
                return {'jsonrpc': '2.0', 'id': request['id'], 'error': failure}
            return {'jsonrpc': '2.0', 'id': request['id'],
                    'result': failure if failure is not None else abi(int(self.sources[index], 16))}
        result = super().reply(request)
        if self.optional_reorg and request['method'] == 'eth_getBlockByNumber' and self.headers >= 4:
            result['result']['hash'] = '0x' + 'ff'*32
        return result


def test_all_active_assets_mapping_provenance_and_unchanged_financials():
    baseline = fetch(WalletRPC())
    rpc = SourceRPC()
    observed = fetch(rpc)
    mappings = validated_source_mappings(observed)
    assert observed['complete'] and len(mappings) == 3
    assert all(m['status'] == 'verified' for m in mappings.values())
    assert [m['source_address'] for m in mappings.values()] == rpc.sources
    assert len(rpc.source_calls) == 3
    for asset, entry in mappings.items():
        assert entry['chain_id'] == 1 and entry['asset'] == asset
        assert entry['oracle'] == observed['oracle']
        assert entry['method'] == 'getSourceOfAsset(address)'
        assert entry['block_number'] == observed['block']['number']
        assert entry['block_hash'] == observed['block']['hash']
        assert entry['rpc_evidence']['params'][1] == hex(observed['block']['number'])
        assert entry['rpc_evidence']['params'][0]['data'] == entry['calldata']
    assert observed['oracle_sources']['feed_health_verified'] is False
    assert len(observed['oracle_sources']['header_evidence']) == 2
    assert len([e for e in observed['rpc_evidence'] if e['method']=='eth_getBlockByNumber']) == 2
    for key in ('account','collateral_usd','debt_usd','health_factor','complete'):
        assert observed[key] == baseline[key]
    assert analyze_wallet_stress(observed, rpc.reserves[0], -.3)['stressed'] == analyze_wallet_stress(baseline,rpc.reserves[0],-.3)['stressed']


def test_shared_contract_converges_and_propagates_to_both_exposures():
    rpc = SourceRPC()
    rpc.sources[1] = rpc.sources[0]
    observed = fetch(rpc)
    graph = build_wallet_graph(observed)
    node = graph.graph['source_nodes'][rpc.reserves[0]]
    assert node == graph.graph['source_nodes'][rpc.reserves[1]]
    assert graph.nodes[node]['contract_address'] == rpc.sources[0]
    assert graph.nodes[node]['shared_assets'] == rpc.reserves[:2]
    for asset in rpc.reserves[:2]:
        edge = graph['valuation:'+asset][node]
        assert edge['evidence_level'] == 'verified' and edge['chain_id'] == 1
    assert nx.is_directed_acyclic_graph(graph)
    incident = analyze_wallet_incident(observed,rpc.reserves[0],'oracle_source_outage')
    assert incident['focus'] == node and incident['financial'] is None
    assert all('exposure:'+a in incident['affected'] for a in rpc.reserves[:2])
    assert 'exposure:'+rpc.reserves[2] not in incident['affected']
    for theme in ('light','dark'):
        assert plot_wallet_graph(graph, node, incident['affected'], theme).layout.height
    positions, sizes = wallet_layout(graph)
    assert set(positions) == set(graph)


def test_zero_source_is_evidenced_but_fallback_remains_unknown():
    rpc = SourceRPC()
    rpc.sources[0] = addr(0)
    observed = fetch(rpc)
    mapping = validated_source_mappings(observed)[rpc.reserves[0]]
    assert mapping['status'] == 'zero_source' and mapping['source_address'] == addr(0)
    graph = build_wallet_graph(observed)
    source = graph.graph['source_nodes'][rpc.reserves[0]]
    assert graph.nodes[source]['contract_address'] is None
    assert graph['valuation:'+rpc.reserves[0]][source]['evidence_level'] == 'unknown'
    assert observed['complete']


@pytest.mark.parametrize('failure', [
    {'code':-32000,'message':'execution reverted'}, requests.Timeout('secret URL must not appear'),
    '0x', '0x01', abi(2**160), abi(1,2), '0x'+'zz'*32,
])
def test_optional_failures_preserve_wallet_and_other_verified_sources(failure):
    rpc = SourceRPC()
    rpc.source_failures[1] = failure
    observed = fetch(rpc)
    mappings = validated_source_mappings(observed)
    assert mappings[rpc.reserves[1]]['status'] in ('unavailable','inconsistent')
    assert mappings[rpc.reserves[1]]['source_address'] is None
    assert all(mappings[a]['status']=='verified' for a in (rpc.reserves[0],rpc.reserves[2]))
    linked = analyze_wallet_incident(observed,rpc.reserves[0],shock=-.3)
    assert observed['complete'] and linked['financial']['coverage']
    assert 'secret URL' not in str(observed)
    assert len(rpc.source_calls) <= 4


@pytest.mark.parametrize('change', [
    lambda o:o['oracle_sources']['entries'][0].update(source_address=addr(900)),
    lambda o:o['oracle_sources']['entries'][0].update(block_hash='0x'+'ff'*32),
    lambda o:o['oracle_sources']['entries'][0]['rpc_evidence']['params'].__setitem__(1,'latest'),
    lambda o:o['oracle_sources']['entries'][0]['rpc_evidence']['params'][0].update(to=addr(99)),
    lambda o:o['oracle_sources']['entries'][0].update(calldata='0x'),
    lambda o:o['oracle_sources']['entries'].append(copy.deepcopy(o['oracle_sources']['entries'][0])),
    lambda o:o['oracle_sources'].update(chain_id=10),
    lambda o:o['oracle_sources'].update(chain_id=True),
    lambda o:o['oracle_sources']['entries'][0].update(chain_id=True),
    lambda o:o['oracle_sources'].update(oracle=addr(900)),
    lambda o:o['oracle_sources'].update(feed_health_verified=True),
    lambda o:o['oracle_sources']['header_evidence'][-1]['result'].update(hash='0x'+'ff'*32),
])
def test_tampered_or_contradictory_sources_never_verify_or_change_account_coverage(change):
    rpc = SourceRPC()
    observed = fetch(rpc)
    change(observed)
    mappings = validated_source_mappings(observed)
    assert mappings[rpc.reserves[0]]['status'] == 'inconsistent'
    assert mappings[rpc.reserves[0]]['source_address'] is None
    linked = analyze_wallet_incident(observed,rpc.reserves[0],shock=-.3)
    assert linked['graph'] is not None and linked['financial']['coverage']


def test_conflicting_legacy_read_is_not_promoted():
    from tests.test_wallet_dependencies import add_source
    rpc = SourceRPC()
    observed = fetch(rpc)
    add_source(observed,source=addr(900))
    assert validated_source_mappings(observed)[rpc.reserves[0]]['status'] == 'inconsistent'


def test_supplementary_block_change_discards_verification_only():
    rpc = SourceRPC()
    rpc.optional_reorg = True
    observed = fetch(rpc)
    assert observed['complete']
    assert all(m['status']!='verified' for m in validated_source_mappings(observed).values())
    assert analyze_wallet_stress(observed,rpc.reserves[0],-.3)['coverage']


def test_historical_source_evidence_never_refreshes_or_promotes_account():
    from tests.test_wallet_risk import archived_observation
    observed = archived_observation()
    original = copy.deepcopy(observed)
    rpc = SourceRPC()
    rpc.block['timestamp'] = hex(observed['block']['timestamp'])
    client = _RPC(rpc,'https://rpc.example',max_seconds=30,max_requests=10,timeout_seconds=5,max_retries=2)
    observed['oracle_sources'] = discover_oracle_sources(client, observed)
    assert all(m['status']=='verified' for m in validated_source_mappings(observed).values())
    assert observed['retrieved_at'] == original['retrieved_at'] and observed['block'] == original['block']
    assert not analyze_wallet_stress(observed,rpc.reserves[0],-.3)['coverage']
    replay = analyze_wallet_stress(observed,rpc.reserves[0],-.3,historical_replay=True)
    assert replay['coverage'] and not replay['current_actionable']


def test_empty_wallet_does_not_make_supplementary_reads():
    rpc = SourceRPC()
    rpc.supplied = rpc.debts = [0,0,0]
    rpc.flags = 0
    observed = fetch(rpc)
    assert observed['empty'] and observed['complete'] and not rpc.source_calls
    assert observed['oracle_sources']['entries'] == []


def test_source_request_budget_is_enforced_without_unbounded_fallback():
    observed = fetch(WalletRPC())
    rpc = SourceRPC()
    client = _RPC(rpc,'https://rpc.example',max_seconds=30,max_requests=2,timeout_seconds=5,max_retries=0)
    report = discover_oracle_sources(client, observed)
    assert report['transport']['http_requests'] == 2
    assert len(rpc.source_calls) == 1
    assert all(e['status']=='unavailable' for e in report['entries'])
    observed['oracle_sources'] = report
    assert all(m['status']=='unavailable' for m in validated_source_mappings(observed).values())


def test_supplementary_initial_header_timeout_remains_unavailable_not_invalid_wallet():
    class HeaderFailure(SourceRPC):
        def reply(self, request):
            if request['method']=='eth_getBlockByNumber' and self.headers >= 2:
                raise requests.Timeout('must not echo provider secrets')
            return super().reply(request)
    rpc = HeaderFailure()
    observed = fetch(rpc)
    assert observed['complete'] and not rpc.source_calls
    assert all(m['status']=='unavailable' for m in validated_source_mappings(observed).values())
    assert analyze_wallet_incident(observed,rpc.reserves[0],shock=-.3)['financial']['coverage']


def test_supplementary_ui_shows_full_source_addresses_and_verification_scope():
    from streamlit.testing.v1 import AppTest
    observed = fetch(SourceRPC())
    app = AppTest.from_string('import streamlit as st\nfrom src.wallet_ui import render_wallet_stress\nrender_wallet_stress(st.session_state.observation)')
    app.session_state.observation = observed
    app.run()
    assert not app.exception
    assert any('3/3 verified' in caption.value for caption in app.caption)
    table = next(frame.value for frame in app.dataframe if 'Configured source' in frame.value.columns)
    assert table['Configured source'].tolist() == [addr(500),addr(501),addr(502)]
    assert all('health/freshness unverified' in value for value in table['Scope'])
    app.selectbox(key='wallet_incident').set_value('oracle_source_outage').run()
    assert not app.exception and not app.metric
    assert any('Financial impact: NOT QUANTIFIED' in value.value for value in app.info)
