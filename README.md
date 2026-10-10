# DeFi Risk Radar — Belem Capital starter

An explainable **starter**, not a finished DeFi investment-grade risk system. Developed for exploratory learning around the EPFL BSA Belem Capital DeFi Risk Dependency Challenge.

## What it does now

- Attractive, interactive, single-page Streamlit dashboard.
- Builds and traces an **illustrative Aave V3 dependency graph** with edges oriented `dependent -> dependency`.
- Optionally retrieves **real published Aave contract addresses** from the official public address-book Solidity source and **spot-price observations** from DefiLlama's free price API.
- Reads **WETH and USDC base reserve configuration** from the Aave V3 Ethereum data provider at one Ethereum block, validating chain ID, deployed code, market relationships, ABI responses and block consistency.
- Uses a **synthetic WETH collateral / USDC debt position**, the **sourced WETH base liquidation threshold when available** (otherwise a manual assumption), and **user-selected** price shocks/probabilities/conditional losses/exit-cost proxies.
- Computes a simplified health factor and transparent scenario-based stay/exit comparison.
- Shows source provenance and makes offline vs sourced mode explicit.
- Includes unit tests for graph traversal, parsing and arithmetic.

## What it deliberately does NOT yet do

- It does **not** read an actual wallet, execute transactions or validate the Aave registry on-chain.
- Its published registry entries do **not** guarantee current live contract state.
- It does **not** model account-specific eMode/isolation/liquidation execution. The reserve getter does not include caps or pause status.
- It does **not** derive probabilities or liquidation losses from historical incident evidence.
- Its exit cost is a user-assumed proxy; it does **not** query swap quotes, borrowing repayment fees or dynamic market slippage.
- An oracle outage is **not** automatically a price crash. The price-shock control is a separate hypothetical market stress.
- No automated trading recommendation should be made from this version.

## Windows PowerShell setup

```powershell
cd defi-risk-radar-starter
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pytest -q
python -m streamlit run app.py
```

If script activation is blocked by PowerShell policy, run without activating:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Click **Fetch public source data** in the sidebar to try loading real contract identities and spot prices. Requires internet access. A source error will be shown if data cannot be fetched or validated.

### Ethereum reserve reads

The adapter uses the free public `https://ethereum-rpc.publicnode.com` endpoint by default. Set `ETH_RPC_URL` in the terminal before starting Streamlit to use another Ethereum mainnet HTTP(S) RPC:

```powershell
$env:ETH_RPC_URL = 'https://ethereum-rpc.publicnode.com'
.\.venv\Scripts\python.exe -m streamlit run app.py
```

No additional dependencies or wallet are required. Public endpoints can rate-limit or become unavailable; a failed refresh preserves the selected snapshot.

The address book supplies the Ethereum addresses provider and underlying token identities. The adapter resolves `getPool()` and `getPoolDataProvider()` at the captured block, checks them against the registry, checks deployed code and the data provider's `ADDRESSES_PROVIDER()` / `POOL()` relationships, then calls `getReserveConfigurationData(address)` for both assets. All contract reads use the same block number; a final header check rejects a changed block hash.

Retrieved fields: decimals, LTV, liquidation threshold, liquidation bonus, reserve factor, collateral enabled, borrowing enabled, deprecated stable borrowing enabled, active and frozen. Percentages are stored in basis points. For example, threshold `8300` becomes `0.83` in the model; liquidation bonus `10500` means a 5% bonus, not 105% extra.

Saved snapshots include block number/hash/timestamp, chain ID, market contract addresses, RPC host, retrieval time, raw call evidence and a SHA-256 fingerprint of the fetched registry text. Full RPC URLs are not saved, avoiding credential-bearing paths or queries. Saved records are dated observations: loading does not perform new on-chain verification. Older snapshots without reserve configuration remain supported and use the manual threshold. Invalid or partial new reserve evidence is rejected.

Reserve observations do not verify oracle-source edges or the dependency graph. DefiLlama prices remain market observations, so the displayed health factor is still an approximation rather than Aave's actual account health factor.

Interface signatures and return order: [Aave IPoolAddressesProvider](https://github.com/aave-dao/aave-v3-origin/blob/main/src/contracts/interfaces/IPoolAddressesProvider.sol), [Aave IPoolDataProvider](https://github.com/aave-dao/aave-v3-origin/blob/main/src/contracts/interfaces/IPoolDataProvider.sol), and [Aave view contracts](https://www.aave.com/docs/aave-v3/smart-contracts/view-contracts). Function selectors were checked against Ethereum `web3_sha3` Keccak hashes. [PublicNode endpoint](https://ethereum.publicnode.com/).

The application begins **in illustrative mode**. It will never silently label demo numbers as real market data.

## Next hackathon milestones

1. Confirm event rule on pre-existing code and AI tool use; disclose preparation work when required.
2. Verify dynamic oracle source configuration on-chain; base WETH/USDC reserve parameters are now read at a recorded block.
3. Improve dependency discovery beyond static registry mapping and show a reproducible evidence trail per edge.
4. Model a specific event's propagation into health factor, liquidation exposure, severity and exit route.
5. Estimate executable exit costs using liquidity quote data, with failure handling and risk warnings.
6. Add scenario calibration/sensitivity + careful uncertainty intervals.
7. Validate claims with Belem mentors and prepare a recorded offline demo.

## Sources

- [Aave Address Book](https://github.com/aave-dao/aave-address-book)
- [Aave V3 documentation](https://www.aave.com/docs/aave-v3/markets/data)
- [DefiLlama coins API](https://api-docs.defillama.com/)
- [BSA EPFL 2026 event](https://hackathon.bsaepfl.com/)

## Repository policy

Never commit private keys or `.env` files. Do not present the demo-mode scenarios as observed risks. Commit small verified changes after tests pass. Before submitting prewritten code, confirm the organizer's policy.

## Updated interface (v2)

The Streamlit theme in `.streamlit/config.toml` now uses a consistent teal accent and dark navy palette. The dashboard includes:

- Directional dependency arrows and a corrected *conceptual* propagation direction for oracle references.
- Clear visual distinction between published identities, modeled relationships and hypothetical financial assumptions.
- A compact decision panel with neutral treatment of unsupported recommendations.
- A health-factor sensitivity chart and three tabs for stress diagnostics, evidence and model limitations.
- Correct adverse (red) coloration for falling health factor.
- Saved public-source snapshots named `data/latest_source_snapshot.json` (previous versions used a different filename).

**Please run the steps in [MANUAL_TEST_PLAN.md](MANUAL_TEST_PLAN.md).** The mathematical and graph unit tests cover only part of the system; the browser rendering and live network request still require a run on your laptop.
