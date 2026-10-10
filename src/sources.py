"""Public, no-key source adapters with explicit validation and timestamps.

The Aave Address Book is a published *registry*, not proof of current on-chain state.
DefiLlama gives market-price observations, not native oracle answers.
"""
import re
from datetime import datetime, timezone
from typing import Any

import requests

ADDRESS_BOOK_URL = (
    'https://raw.githubusercontent.com/aave-dao/aave-address-book/main/src/'
    'AaveV3Ethereum.sol'
)
PRICE_API = 'https://coins.llama.fi/prices/current/'
REQUIRED = (
    'POOL', 'ORACLE', 'WETH_UNDERLYING', 'USDC_UNDERLYING',
    'WETH_ORACLE', 'USDC_ORACLE'
)
ADDRESS_PATTERN = re.compile(
    r'\b([A-Za-z][A-Za-z0-9_]*)\s*=\s*'
    r'(?:[A-Za-z_][A-Za-z0-9_]*\s*\(\s*)?'
    r'(0x[0-9a-fA-F]{40})\b', re.MULTILINE
)


class SourceError(RuntimeError):
    """A remote source failed or returned invalid data."""


def parse_address_book(solidity_source: str) -> dict[str, str]:
    addresses = dict(ADDRESS_PATTERN.findall(solidity_source))
    missing = set(REQUIRED) - addresses.keys()
    if missing:
        raise SourceError(f'Aave registry missing fields: {sorted(missing)}')
    return {name: addresses[name] for name in REQUIRED}


def parse_defillama_prices(payload: dict[str, Any], addresses: dict[str, str]) -> dict[str, Any]:
    coins = payload.get('coins', {})
    parsed = {}
    for asset in ('WETH', 'USDC'):
        lookup = f"ethereum:{addresses[asset + '_UNDERLYING']}".lower()
        normalized = {k.lower(): v for k, v in coins.items()}
        item = normalized.get(lookup)
        if not isinstance(item, dict) or not isinstance(item.get('price'), (int, float)):
            raise SourceError(f'Missing valid {asset} spot-price observation')
        if item['price'] <= 0:
            raise SourceError(f'Nonpositive {asset} price')
        parsed[asset] = {
            'usd': float(item['price']),
            'timestamp': item.get('timestamp'),
            'confidence': item.get('confidence'),
        }
    return parsed


def fetch_market_snapshot(session=None) -> dict[str, Any]:
    client = session or requests
    try:
        registry_res = client.get(ADDRESS_BOOK_URL, timeout=12)
        registry_res.raise_for_status()
        addresses = parse_address_book(registry_res.text)
        coin_ids = ','.join(
            f"ethereum:{addresses[asset + '_UNDERLYING']}"
            for asset in ('WETH', 'USDC')
        )
        price_url = PRICE_API + coin_ids
        prices_res = client.get(price_url, timeout=12)
        prices_res.raise_for_status()
        prices = parse_defillama_prices(prices_res.json(), addresses)
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        raise SourceError(f'Live market-data retrieval failed: {exc}') from exc
    return {
        'mode': 'sourced',
        'retrieved_at': datetime.now(timezone.utc).isoformat(),
        'network': 'Ethereum mainnet',
        'protocol': 'Aave V3',
        'registry_source': ADDRESS_BOOK_URL,
        'price_source': price_url,
        'data_limitations': (
            'Addresses come from a published registry, not direct on-chain calls. '
            'Prices come from DefiLlama, not the Aave protocol oracle. '
            'User holdings and liquidation threshold remain illustrative.'
        ),
        'addresses': addresses,
        'prices': prices,
    }


DEMO_SNAPSHOT = {
    'mode': 'illustrative',
    'retrieved_at': None,
    'network': 'Ethereum mainnet (illustrative)',
    'protocol': 'Aave V3 (illustrative topology)',
    'registry_source': None,
    'price_source': None,
    'data_limitations': 'Offline demo only: no verified live records or current market prices.',
    'addresses': {},
    'prices': {'WETH': {'usd': 2500.0}, 'USDC': {'usd': 1.0}},
}
