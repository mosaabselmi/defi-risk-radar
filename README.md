# DeFi Risk Radar — Belem Capital starter

An explainable **starter**, not a finished DeFi investment-grade risk system. Developed for exploratory learning around the EPFL BSA Belem Capital DeFi Risk Dependency Challenge.

## What it does now

- Attractive, interactive, single-page Streamlit dashboard.
- Builds and traces an Aave V3 dependency graph with edges oriented `dependent -> dependency`. Oracle-to-feed edges use discovered on-chain source mappings when available; other edges remain modeled.
- Optionally retrieves **real published Aave contract addresses** from the official public address-book Solidity source and **spot-price observations** from DefiLlama's free price API.
- Reads **WETH and USDC base reserve configuration** from the Aave V3 Ethereum data provider at one Ethereum block, validating chain ID, deployed code, market relationships, ABI responses and block consistency.
- Uses a **synthetic WETH collateral / USDC debt position**, the **sourced WETH base liquidation threshold when available** (otherwise a manual assumption), and four explicit economic scenarios: WETH/USDC market shocks and WETH/USDC oracle-source outages.
- Computes marked equity, scenario-specific P&L, modeled health factor and a transparent **STAY / EXIT / MONITOR** comparison with assumed execution costs.
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

At the same block, the adapter resolves the provider's `getPriceOracle()`, checks the oracle against the published market address, verifies deployed code and the oracle's `ADDRESSES_PROVIDER()` relationship, and calls `getSourceOfAsset(address)` for WETH and USDC. Nonzero source addresses must have deployed code. The nested `reserve_configuration.oracle_sources` record stores the oracle/source addresses, raw call evidence and observed code; block, RPC and retrieval provenance are inherited from the enclosing record.

Only the two configured oracle-to-feed edges are generated from these mappings and marked `verified_onchain=True`. They retain the dashboard's WETH Feed / USDC Feed labels while their addresses come from on-chain discovery, even if they differ from registry references. Every other relationship remains modeled. A zero source address is recorded as absence of a direct source and creates no verified feed edge. Legacy snapshots without oracle evidence retain their modeled edges. Malformed or inconsistent evidence is rejected before graph construction or saved-snapshot loading.

Discovery does not identify source adapters' internal dependencies, proxy implementations, fallback routes, actual oracle price answers or freshness. Reachability can combine verified and modeled edges and does not establish realized financial loss. Public RPC responses and local snapshots are trusted observations rather than independently authenticated state proofs. DefiLlama prices remain market observations, so the displayed health factor is still an approximation rather than Aave's actual account health factor.

Interface signatures and return order: [Aave IPoolAddressesProvider](https://github.com/aave-dao/aave-v3-origin/blob/main/src/contracts/interfaces/IPoolAddressesProvider.sol), [Aave IPoolDataProvider](https://github.com/aave-dao/aave-v3-origin/blob/main/src/contracts/interfaces/IPoolDataProvider.sol), [Aave IAaveOracle](https://github.com/aave-dao/aave-v3-origin/blob/main/src/contracts/interfaces/IAaveOracle.sol), and [Aave view contracts](https://www.aave.com/docs/aave-v3/smart-contracts/view-contracts). Function selectors were checked against Ethereum `web3_sha3` Keccak hashes, including `getPriceOracle()` (`0xfca513a8`) and `getSourceOfAsset(address)` (`0x92bf2be0`). [PublicNode endpoint](https://ethereum.publicnode.com/).

The application begins **in illustrative mode**. It will never silently label demo numbers as real market data.

### Incident-specific economic impact (Block 03)

Economic scenario selection is separate from graph inspection. A WETH market shock affects collateral price only; a USDC market shock affects debt price only and permits either sign. Marked equity is `WETH units × WETH/USD − USDC debt units × USDC/USD`. A falling USDC price reduces this borrower's USD debt and improves equity. Market P&L is stressed equity minus base equity. Downside and favorable outcomes are reported separately, with no negative expected loss.

Oracle-source outages preserve reference prices and show affected graph paths. Dollar impact, expected downside and expected favorable impact remain **unquantified** and the result is **MONITOR**. A reference health factor is not an estimate of outage damage. The sensitivity plot remains a labeled hypothetical market curve, with no selected market shock marker for outages.

For market scenarios, expected downside is the **user-assumed 24h scenario probability** multiplied by conditional downside **once**. This probability is not an empirically measured incident frequency. Execution cost is `assumed aggregate traded notional × (spread + slippage + trading fee bps) / 10,000 + aggregate gas USD`. The default notional assumes gross collateral sale plus debt purchase; edit it for the intended trading legs. Repayment principal is already reflected in net equity and is not a trading fee. No executable quote, live slippage, funding verification or transaction is provided. Full collateral withdrawal requires debt repayment or another unwind first; liquidity and transaction ordering remain unverified.

HF below 1 indicates **modeled liquidation eligibility**, using the sourced WETH base threshold and observed market prices. Actual Aave eligibility uses protocol oracle prices and account settings. The sourced WETH liquidation bonus is displayed separately. Optional penalty estimates require an explicitly assumed **fraction of stressed debt liquidated**. Only the incremental collateral bonus cost is added, with debt repayment capped by stressed collateral availability including the bonus. Principal repayment offsets debt reduction and is not added again as damage. The fraction is not Aave's actual close factor and no realized liquidation is guaranteed. Stressed equity excludes the separately displayed penalty; total conditional downside includes it only when explicitly estimated.

**EXIT** means expected modeled downside exceeds assumed execution cost with adequate inputs; **STAY** means it does not. A price-only lower bound can justify EXIT even when incremental liquidation damage is unknown, but an unquantified liquidation penalty prevents an unsupported STAY. **MONITOR** also applies to missing/unreliable data, non-positive base equity, an already liquidation-eligible base position, zero exposure or an unjustified zero traded notional. Decisions remain conditional research comparisons, not executable trade instructions.

The dashboard's explicit conservative data policy requires snapshot/price/reserve observations no older than **1 hour**, spot-price confidence at least **0.9**, and no timestamps more than **60 seconds** in the future. These thresholds are policy assumptions, not calibrated risk estimates. Demo and legacy records still show arithmetic and graphs but return MONITOR when sourced price/reserve evidence is absent. Invalid/nonfinite inputs and prices at or below **$0.00000001** are unsupported; an exact −100% shock is rejected instead of fabricating a tiny price. Existing snapshots and verified graph evidence are unchanged.

Protocol interpretation: [Aave health factor and liquidations](https://aave.com/help/borrowing/liquidations), [Aave collateral withdrawals](https://aave.com/help/supplying/withdraw-tokens).

## Next hackathon milestones

1. Confirm event rule on pre-existing code and AI tool use; disclose preparation work when required.
2. Extend oracle discovery to adapter internals and fallback routes; direct WETH/USDC source mappings and base reserve parameters are now read at a recorded block.
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
