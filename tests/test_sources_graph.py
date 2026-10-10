import pytest
from src.sources import parse_address_book, parse_defillama_prices, SourceError
from src.dependencies import build_dependency_graph, affected_by_failure


def test_parsing_solidity_registry_declarations():
    keys = ['POOL', 'ORACLE', 'WETH_UNDERLYING', 'USDC_UNDERLYING',
            'WETH_ORACLE', 'USDC_ORACLE']
    addr = '0x' + '1' * 40
    text = '\n'.join(f'address internal constant {key} = {addr};' for key in keys)
    assert parse_address_book(text)['WETH_ORACLE'] == addr


def test_missing_registry_field_is_error():
    with pytest.raises(SourceError):
        parse_address_book('address internal constant POOL = 0x' + '1'*40 + ';')


def test_price_parsing_case_insensitive():
    addresses = {'WETH_UNDERLYING': '0x'+'A'*40, 'USDC_UNDERLYING': '0x'+'B'*40}
    coins = {'ethereum:0x'+'a'*40: {'price': 2500},
             'ethereum:0x'+'b'*40: {'price': 1.0}}
    assert parse_defillama_prices({'coins': coins}, addresses)['WETH']['usd'] == 2500


def test_dependents_for_feed():
    g = build_dependency_graph({'mode': 'illustrative', 'addresses': {}})
    affected = affected_by_failure(g, 'WETH Feed')
    assert 'Position' in affected
    assert 'Aave Price Oracle' in affected
    assert 'Aave Pool' in affected
    assert 'WETH' not in affected
    assert affected_by_failure(g, 'does not exist') == []


def test_oracle_failure_reaches_pool_and_position():
    """Correct dependency direction: pool depends on oracle, oracle on feeds."""
    g = build_dependency_graph({'mode': 'illustrative', 'addresses': {}})
    impacted = affected_by_failure(g, 'WETH Feed')
    assert 'Aave Price Oracle' in impacted
    assert 'Aave Pool' in impacted
    assert 'Position' in impacted
    assert 'WETH' not in impacted  # Token existence is not dependent on price feed.
    assert g.has_edge('Aave Price Oracle', 'WETH Feed')
    assert not g.has_edge('WETH Feed', 'Aave Price Oracle')


def test_published_registry_never_marks_graph_edges_as_onchain_verified():
    g = build_dependency_graph({'mode': 'sourced', 'addresses': {'POOL': '0x' + 'a'*40}})
    assert not any(meta['verified_onchain'] for _, _, meta in g.edges(data=True))
