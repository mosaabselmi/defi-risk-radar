# DeFi Risk Radar

**Evidence-backed DeFi dependency analysis, financial stress testing, and conditional STAY vs EXIT decisions.**

Built for the **Belem Capital DeFi Risk Dependency Challenge** at the **BSA EPFL Hackathon 2026**.

**[Live application](https://defi-risk-radar.streamlit.app/)** · **[GitHub repository](https://github.com/mosaabselmi/defi-risk-radar)**

## Why it exists

A DeFi position is more than its visible token balances. It depends on lending contracts, reserve configurations, price oracles, underlying assets, liquidity, and the settlement chain. When something goes wrong, the useful questions are: **what does this position depend on, how could a failure affect its financial health, and would a hypothetical exit be worth the cost?**

DeFi Risk Radar connects on-chain evidence, a position-specific dependency graph, transparent stress scenarios, and a conditional economic comparison in one read-only workflow.

## What the prototype does

- **Import real Aave V3 positions on Ethereum:** inspect wallet balances, collateral/debt roles, reserve parameters, oracle configuration, and health-factor information from on-chain sources.
- **Discover and inspect dependencies:** explore an evidence-linked graph of the position's relevant assets, reserve contracts, lending components, oracle sources, and Ethereum infrastructure. Relationships distinguish observed/configured evidence from modeled or unresolved dependencies.
- **Surface position-specific risk scenarios:** identify potential market-price, oracle, protocol, liquidity, and infrastructure failures. Not every failure mode has a quantitative loss model.
- **Stress collateral and borrowed-asset prices:** run explicitly hypothetical asset-price changes and recompute marked assets, liabilities, equity, health factor, and modeled liquidation eligibility when supported.
- **Compare STAY vs EXIT conditionally:** vary assumed incident probability, avoided downside, notional, slippage, and costs to inspect the hypothetical economic trade-off and break-even probability. **No executable exit quote is provided.**
- **Inspect evidence and limitations:** distinguish historical snapshots, modeled outcomes, user assumptions, and unsupported claims.

## Reproducible historical demonstration

The included demonstration analyzes an actual **historical** Aave V3 Ethereum borrower position at **block 26,165,176**, not a live risk alert.

| Hypothetical scenario | Modeled health factor | Modeled consequence |
| --- | ---: | --- |
| Observed baseline | 1.1949 | Snapshot position state |
| WETH collateral price **−30%** | 0.8365 | About **$46,357.80 decrease in marked equity**; HF below 1 indicates liquidation eligibility under the model |
| USDT borrowed-asset price **+10%** | 1.0863 | About **$10,733.35 increase in marked debt**, reducing marked equity by the same amount |
| USDT borrowed-asset price **−10%** | 1.3277 | About **$10,733.35 decrease in marked debt**; not an adverse-loss or EXIT-triggering event in the model |

These are **counterfactual calculations**, not observed incidents, realized liquidations, or forecasts. The original position snapshot and its evidence are preserved for reproducibility.

An example conditional decision compares a **10% user-assumed 24-hour probability** of the WETH stress with **100% assumed downside avoidance** and **$315 hypothetical exit costs**, yielding **$4,635.78 expected modeled STAY downside** versus **$315 hypothetical EXIT cost**. This illustrates the calculation, **not a real probability estimate or verified executable trade**.

## Try it

1. Visit **[the live app](https://defi-risk-radar.streamlit.app/)**.
2. Choose **Try Verified Historical Demo** on Home.
3. In **Overview**, review the wallet and automatically identified risk mechanisms.
4. Open **Dependencies** to inspect the position-specific graph and supporting evidence.
5. In **Scenarios**, test WETH collateral and USDT/GHO debt-price changes.
6. In **Decision**, enter explicit hypothetical assumptions to compare STAY and EXIT.
7. Use **Evidence** to review provenance and boundaries.

The historical example is designed to be reproducible without requiring live RPC access. Real-wallet imports require working Ethereum RPC access.

## Run locally

**Requirements:** Python 3.12 recommended; dependencies are listed in `requirements.txt`.

```bash
python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Open the local URL printed by Streamlit (usually `http://localhost:8501`).

Run the tests:

```bash
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
```

On macOS/Linux, activate `.venv` and use `python` instead of the Windows interpreter path.

## Scope and safeguards

- **Supported production-like importer:** Aave V3 on Ethereum; other protocols and chains are not automatically covered.
- **Read-only:** no wallet signing, trading, automatic repayment, or liquidation execution.
- **Historical demo is not live monitoring:** the pinned block does not establish current wallet or protocol conditions.
- **Failure mechanisms have different levels of support:** price shocks can be quantitatively modeled; many oracle, smart-contract, liquidity, and chain failures are described but **not assigned fabricated dollar-loss estimates**.
- **No market-derived incident probability:** STAY/EXIT probabilities and avoidance assumptions come from user inputs.
- **No verified full-position exit:** slippage, gas, trade notional, repayment funding, and execution feasibility are not confirmed by live route quotes.
- **Liquidation eligibility is not liquidation execution:** HF below 1 does not prove that a liquidation occurred or quantify collateral seized.

This is a hackathon research prototype, not financial advice.

## Tech stack

Python, Streamlit, NetworkX, Plotly, Ethereum JSON-RPC / Aave V3 on-chain data, and pytest.

## Challenge alignment

The Belem Capital challenge assesses **dependency discovery (30%)**, **defensible impact estimation (30%)**, **decision usefulness (25%)**, and a **working demonstration using real data (15%)**. DeFi Risk Radar focuses on a traceable end-to-end example, making its evidence and uncertainties explicit rather than claiming unsupported incident coverage.
