# Historical Aave example — NOT LIVE

In **REAL PORTFOLIO ANALYSIS**, select **Load verified historical example — NOT LIVE**.
This explicitly selects retrospective analysis, loads local evidence, validates its
digest and completeness, and immediately shows a WETH −30% scenario. No network
request is made. Inspect dependencies, change the shock, and enter your own
transaction notional/probability assumptions for conditional economics. Oracle
outages remain unquantified. No current actionable STAY/EXIT signal is available.
The separate live-import form continues to use the configured public RPC.

## Capture v1

- Public example account: `0x24a416e5c00c7b47a5f8a300984579b53919330b` (not an ownership claim).
- Ethereum mainnet block: **26,165,176**.
- Block hash: `0x7df6f9897ddb70d3f3ddc310190bddaa0a2d2873754f1bb74c0a227cec773193`.
- Block timestamp: `2026-10-10T22:58:47+00:00` (Unix `1791673127`).
- Account retrieval: `2026-10-10T22:58:58.135243+00:00`.
- RPC: `https://ethereum-rpc.publicnode.com`, via the existing bounded read-only
  `src.wallet.fetch_wallet_position` importer. Original contract registry URL/hash,
  source-discovery observation time and transport diagnostics are inside the capture.
- 67 initialized reserves enumerated; 185 mandatory RPC reads, including both
  block headers, account/eMode/configuration, all reserve IDs/user balances,
  active token metadata/code/balance cross-checks and pinned Aave oracle prices.
- All 3 active configured source addresses verified with supplementary pinned
  reads and two additional block-header confirmations:
  WETH `0x5424384b256154046e9667ddfaaa5e550145215e`,
  USDT `0x260326c220e469358846b187ee53328303efe19c`,
  GHO `0xd110cac5d8682a3b045d5524a9903e031d70fccd`.
- File size: **364,678 bytes**. SHA-256:
  `e3ebae4fbb7394c41825fae29b7c09d76b304bd647dba5e6c22f0572405f9b42`.

## Integrity, reproducibility and limitations

The loader pins this digest in code and rejects corrupted bytes before parsing.
The fixture-specific Git attributes prevent automatic line-ending conversion
from changing captured bytes between Windows and Linux checkouts.
It then reuses existing graph, source-mapping and stress/HF evidence validators,
and checks captured display metadata against its raw contract evidence. This is
an integrity check of a trusted repository capture, **not independent proof of
blockchain authenticity**. Public RPC responses are not Merkle/storage proofs or
multi-provider consensus. Configured source addresses do not prove feed health.

The JSON is the original complete importer output: timestamps, block headers,
balances, raw reads and its original current-at-capture `historical: false` flag
are unchanged. Replay is a separate, forced historical UI mode; the flag never
makes the example live. Freshness checks remain unchanged for ordinary imports.
The original market snapshot is not involved in loading or calculating replay.

To reproduce the on-chain reads using an archive-capable public RPC and the
project interpreter (no keys, signing or transactions):

```python
from src.wallet import fetch_wallet_position
from src.wallet_dependencies import build_wallet_graph
from src.wallet_risk import analyze_wallet_stress

observation = fetch_wallet_position(
    '0x24a416e5c00c7b47a5f8a300984579b53919330b',
    rpc_url='https://ethereum-rpc.publicnode.com',
    block_number=26165176,
)
graph = build_wallet_graph(observation)
result = analyze_wallet_stress(
    observation,
    '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2',
    -0.30,
    historical_replay=True,
)
assert result['coverage']
```

Archive access can fail; it is unnecessary for the bundled replay. A re-fetch
has a new retrieval time/transport evidence and is not byte-identical. The
published registry can also change after capture. Keep **v1 immutable**: any new
capture must receive a new filename/version and independently reviewed digest,
provenance and validation tests. Do not regenerate v1 in tests or at startup.

At capture, supplied WETH was 61.666552715484060428, USDT variable debt
107438.20408, GHO variable debt 0.097347458472486516, eMode 0. Baseline HF
was approximately 1.194934; WETH −30% gives HF approximately 0.836454 and a
marked-equity change of −$46,357.80. This is a hypothetical stress mark,
not realized damage, a current incident, a liquidation prediction or an
executable exit quote. Repayment, liquidity, routes and fees remain unverified.
