# DeFi Risk Radar — manual test checklist (Windows)

Do this *after* `python -m pytest -q` succeeds. The model is a research demonstration, not a calibrated trading tool. For screenshot verification, test at 100% browser zoom first.

## Launch

```powershell
python -m pip install -r requirements.txt
python -m pytest -q
python -m streamlit run app.py
```

Expected: browser opens at `http://localhost:8501`; page title is "DeFi Risk Radar"; no red Python exceptions. Sidebar and main area are both readable. Resize browser to ~1280px width and check labels don't get clipped.

## UI / model baseline

Keep illustrative mode and defaults: 4 WETH, 6,000 USDC debt, $2,500/WETH and $1/USDC, liquidation threshold 80%, -20% shock, event probability 10%, conditional loss 35%, exit cost 2% of equity, $10 fixed fee.

- [ ] Base collateral **$10,000**, debt **$6,000**.
- [ ] Base health factor **1.333…**, stressed health factor **1.0667…**.
- [ ] Stressed health-factor **negative delta is red**, not green.
- [ ] Selected failure WETH Feed reaches **Aave Price Oracle, Aave Pool and Position** (not WETH token).
- [ ] Arrowheads on dependency graph point **dependent → dependency**.
- [ ] Expected loss **$140**, exit cost **$90**, recommendation **EXIT**, break-even event probability **~6.4%**.
- [ ] Data mode explicitly says **illustrative** and modeled relationships are not labeled as live on-chain verified.

## Scenario behavior

- [ ] Set event probability to **0%**. Expected scenario loss becomes **$0**, and recommendation is **STAY** (positive exit cost).
- [ ] Restore to **10%**, raise assumed exit-cost percentage to **20%**. Recommendation becomes **STAY**.
- [ ] Set WETH market shock to **-30%**. Stressed health factor falls below **1** and the liquidation eligibility warning is **red**.
- [ ] Set WETH market shock to **0%**. Base and stressed health factors are equal.
- [ ] Set debt to **0 USDC**. Both health factors show infinity, diagnostics explain that no borrowing liquidation is modeled, and health curve handles the case without crashing.
- [ ] Restore debt to 6000 and reduce WETH collateral to **1 WETH**. Equity is negative, so the app says **NOT EVALUATED**, not a confident STAY recommendation.
- [ ] Try different failed components and verify the trace varies. The severity and price-shock sliders must still be labeled independent assumptions, not incident-derived forecasts.
- [ ] At 100% conditional loss and high cost, check break-even does not show impossible values as ordinary reachable probabilities.

## Evidence / live data

- [ ] Open **Sources & evidence** tab. It shows relations marked **modeled**, not "verified on chain".
- [ ] Click **Fetch public data** with a working internet connection. Verify price and address records are displayed, retrieval date is shown, and a `data/latest_source_snapshot.json` file is created (network availability permitting).
- [ ] Inspect saved JSON and confirm prices are positive, contract addresses have 40 hexadecimal digits and timestamps are present. Confirm both assets appear in `reserve_configuration.reserves`, every raw call uses the same block tag, and block number/hash/timestamp are recorded.
- [ ] Verify the Sources tab displays both reserve configurations. Compare the raw responses at the recorded block with the configured Ethereum RPC, not with unpinned latest values.
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

- Published Aave registry addresses are *not* proof of current oracle configuration; modeled dependency edges remain unverified.
- DefiLlama spot prices are not Aave's oracle answer.
- WETH's base liquidation threshold comes from the recorded Ethereum block when reserve configuration is present; demo and legacy snapshots use a manual assumption. eMode and actual account settings are not modeled. Loss probability remains hypothetical.
- The STAY/EXIT scenario model is not a real investment recommendation.
