# DeFi Risk Radar — manual test checklist (Windows)

Do this *after* `python -m pytest -q` succeeds. The model is a research demonstration, not a calibrated trading tool. For screenshot verification, test at 100% browser zoom first.

## Launch

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Expected: browser opens at `http://localhost:8501`; page title is "DeFi Risk Radar"; no red Python exceptions. Sidebar and main area are both readable. Resize browser to ~1280px width and check labels don't get clipped.

## UI / model baseline

Keep illustrative mode: 4 WETH, 6,000 USDC debt, $2,500/WETH, $1/USDC, assumed threshold 80%, WETH market shock −20%, assumed 24h probability 10%. Execution assumptions: $16,000 aggregate traded notional, spread 10 bps, slippage 20 bps, fee 5 bps, gas $10.

- [ ] Base collateral $10,000, debt $6,000, equity $4,000; stressed equity $2,000 and market P&L −$2,000.
- [ ] Base HF 1.333…, stressed HF 1.0667…; falling HF delta is red.
- [ ] Price-only expected downside $200; execution components $16 + $32 + $8 + $10 = $66.
- [ ] Demo result is MONITOR because inputs are illustrative. No live data is implied.
- [ ] Graph arrows remain dependent → dependency, with source evidence preserved.

## Scenario behavior

- [ ] Load a fresh sourced snapshot with adequate price confidence and reserve evidence. The same decision arithmetic may return EXIT when expected downside exceeds assumed cost; stale snapshots return MONITOR with reasons.
- [ ] Choose a −30% WETH market shock. With the recent $2,494/WETH, ~$1/USDC and 83% threshold, base HF is ~1.38, stressed HF ~0.97 and price loss ~$2,993 before penalties.
- [ ] Confirm liquidation eligibility is separate from the STAY/EXIT comparison. The sourced bonus is shown separately; penalty stays unquantified unless its checkbox and debt-fraction assumption are entered.
- [ ] Enable a 50% assumed debt liquidation fraction with a sourced 5% bonus. Bonus penalty is stressed debt × 50% × 5%, capped by collateral availability. Repayment principal is not counted as another loss.
- [ ] Choose USDC market shock −10%. WETH value stays unchanged; debt USD falls, equity and HF improve. Downside is zero and favorable impact is positive.
- [ ] Change USDC shock to +10%. WETH stays unchanged; debt USD rises and equity/HF decline.
- [ ] Change graph inspection without changing scenario. Financial values and rationale stay the same.
- [ ] Select each oracle-source outage. Reference prices/HF remain unchanged, dollar loss is unquantified, MONITOR is returned, and relevant affected paths appear. No market-price shock control or market crash is implied.
- [ ] Set probability to 0% in a market scenario with no unquantified liquidation exposure. Expected downside becomes zero; with adequate inputs, comparison returns STAY. Probability scales conditional downside once and does not scale exit cost.
- [ ] Double the assumed traded notional. Spread, slippage and fee costs double; gas is unchanged. Cost does not use equity.
- [ ] Set debt to zero: HF infinity, no liquidation eligibility; USDC price shocks create no debt P&L.
- [ ] Set collateral to zero, or both balances to zero: no crash; unsupported/unfunded position returns MONITOR.
- [ ] Non-positive base equity and already liquidation-eligible base positions return MONITOR with execution uncertainty.
- [ ] Load stale/legacy snapshots: dated evidence and arithmetic remain available, but missing/stale decision inputs return MONITOR.
- [ ] Check every tab for tracebacks. Invalid/nonfinite prices and assumptions must not create confident recommendations.

## Evidence / live data

- [ ] Open **Sources & evidence** tab in demo mode. All relations are marked modeled. After a sourced refresh, only the oracle-to-feed mappings should show on-chain block evidence; all other relationships remain modeled.
- [ ] Click **Fetch public data** with a working internet connection. Verify price and address records are displayed, retrieval date is shown, and a `data/latest_source_snapshot.json` file is created (network availability permitting).
- [ ] Inspect saved JSON and confirm prices are positive, contract addresses have 40 hexadecimal digits and timestamps are present. Confirm both assets appear in `reserve_configuration.reserves`, every raw call uses the same block tag, and block number/hash/timestamp are recorded.
- [ ] Verify the Sources tab displays both reserve configurations. Compare the raw responses at the recorded block with the configured Ethereum RPC, not with unpinned latest values.
- [ ] Verify both configured source addresses and the verified Aave oracle appear in the Sources tab. The oracle-source call tags must match the enclosing reserve block. Confirm source addresses come from `getSourceOfAsset(address)`, not just the registry feed references.
- [ ] Confirm graph direction is `Aave Price Oracle -> WETH Feed` / `USDC Feed`. Each configured feed disruption reaches the oracle, pool and position, while the token node is not affected by feed failure.
- [ ] Load a reserve-only legacy snapshot lacking `oracle_sources`; the graph should retain modeled source edges. A malformed new oracle record must be rejected without replacing the selected snapshot.
- [ ] A zero source observation should have no oracle-to-feed edge for that asset and no claimed direct source; fallback discovery remains outside this version.
- [ ] Confirm the sidebar displays the sourced WETH base threshold instead of the assumed-threshold slider. Health factor uses WETH's threshold divided by 10,000; USDC's collateral threshold does not enter this WETH-collateral model.
- [ ] Confirm liquidation bonus is displayed as the extra percentage: raw `10500` means 5%. Reserve flags are observations, not proof that an account can borrow or liquidate.
- [ ] Click **Load saved source snapshot**. It loads without an internet connection, and the banner still identifies the data as a dated *public-source snapshot*, not live on-chain verification.
- [ ] Click **Use illustrative demo**. It restores the demo banner, demo prices and unverified state.
- [ ] Load an older saved registry/price snapshot without `reserve_configuration`; it should retain the manual threshold and clearly state that reserve evidence is absent.
- [ ] Set `ETH_RPC_URL` to a wrong-chain or unreachable endpoint in the launching terminal and restart. Refresh should fail without replacing the prior snapshot. Restore a working Ethereum endpoint afterward.
- [ ] A malformed or partial saved reserve record should show an error and preserve the prior selected snapshot.
- [ ] Disconnect internet and click **Fetch public data**. The app should show an error without crashing or silently replacing earlier loaded source data.

## Final presentation

- [ ] Test project launch from a new VS Code terminal.
- [ ] Run `python -m pytest -q` again after changes.
- [ ] Reload the browser and navigate every tab; watch the terminal for tracebacks.
- [ ] Take screenshots at 100% zoom and a full browser width. Check all graph labels and arrows.
- [ ] Record a ~90-second offline demo **only after** the live-mode flow is validated.

## Interpretation notes

- Published registry addresses alone are not proof of configured oracle sources. Only direct source mappings with recorded on-chain evidence are marked verified; remaining graph relationships and propagation assumptions remain modeled.
- DefiLlama spot prices are not Aave's oracle answer.
- WETH's base liquidation threshold comes from the recorded Ethereum block when reserve configuration is present; demo and legacy snapshots use a manual assumption. eMode and actual account settings are not modeled. Loss probability remains hypothetical.
- The STAY/EXIT scenario model is not a real investment recommendation.
