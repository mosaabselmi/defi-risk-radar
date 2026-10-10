# DeFi Risk Radar — Belem Capital starter

An explainable **starter**, not a finished DeFi investment-grade risk system. Developed for exploratory learning around the EPFL BSA Belem Capital DeFi Risk Dependency Challenge.

## What it does now

- Attractive, interactive, single-page Streamlit dashboard.
- Builds and traces an **illustrative Aave V3 dependency graph** with edges oriented `dependent -> dependency`.
- Optionally retrieves **real published Aave contract addresses** from the official public address-book Solidity source and **spot-price observations** from DefiLlama's free price API.
- Uses a **synthetic WETH collateral / USDC debt position**, an **assumed liquidation threshold**, and **user-selected** price shocks/probabilities/conditional losses/exit-cost proxies.
- Computes a simplified health factor and transparent scenario-based stay/exit comparison.
- Shows source provenance and makes offline vs sourced mode explicit.
- Includes unit tests for graph traversal, parsing and arithmetic.

## What it deliberately does NOT yet do

- It does **not** read an actual wallet, execute transactions or validate the Aave registry on-chain.
- Its published registry entries do **not** guarantee current live contract state.
- It does **not** fetch the real liquidation threshold from an Aave market or model eMode/isolation/liquidation execution.
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

The application begins **in illustrative mode**. It will never silently label demo numbers as real market data.

## Next hackathon milestones

1. Confirm event rule on pre-existing code and AI tool use; disclose preparation work when required.
2. Use direct on-chain/market APIs to verify dynamic oracle source configuration and reserve liquidation parameters for one Aave V3 market.
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
