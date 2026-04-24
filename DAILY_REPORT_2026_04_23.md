<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>PilotAI Daily Report — 2026-04-23</title>
<style>
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 1100px; margin: 0 auto; padding: 24px 32px; background: #fff; color: #1a1a1a; line-height: 1.6; }
h1 { font-size: 1.8em; border-bottom: 3px solid #1a1a1a; padding-bottom: 12px; margin-top: 0; }
h2 { font-size: 1.35em; color: #333; border-bottom: 1px solid #ddd; padding-bottom: 6px; margin-top: 36px; }
h3 { font-size: 1.1em; color: #555; margin-top: 24px; }
table { width: 100%; border-collapse: collapse; margin: 12px 0 20px 0; font-size: 0.92em; }
th, td { padding: 8px 12px; text-align: left; border-bottom: 1px solid #e0e0e0; }
th { background: #f5f5f5; font-weight: 600; color: #333; }
tr:hover { background: #fafafa; }
.pass { color: #16a34a; font-weight: 600; }
.fail { color: #dc2626; font-weight: 600; }
.warn { color: #d97706; font-weight: 600; }
.killed { background: #fef2f2; }
.metric-box { display: inline-block; background: #f8f9fa; border: 1px solid #e0e0e0; border-radius: 8px; padding: 14px 20px; margin: 6px 8px 6px 0; text-align: center; min-width: 130px; }
.metric-box .label { font-size: 0.78em; color: #666; text-transform: uppercase; letter-spacing: 0.5px; }
.metric-box .value { font-size: 1.5em; font-weight: 700; color: #1a1a1a; }
.metric-box .value.red { color: #dc2626; }
.metric-box .value.green { color: #16a34a; }
.callout { background: #fffbeb; border-left: 4px solid #d97706; padding: 14px 18px; margin: 16px 0; border-radius: 0 6px 6px 0; }
.callout-green { background: #f0fdf4; border-left-color: #16a34a; }
.callout-red { background: #fef2f2; border-left-color: #dc2626; }
.callout strong { color: inherit; }
.tag { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.8em; font-weight: 600; }
.tag-kill { background: #fecaca; color: #991b1b; }
.tag-pass { background: #bbf7d0; color: #166534; }
.tag-fix { background: #bfdbfe; color: #1e40af; }
.section-num { color: #999; font-weight: 400; }
code { background: #f3f4f6; padding: 1px 5px; border-radius: 3px; font-size: 0.9em; }
.footer { margin-top: 48px; padding-top: 16px; border-top: 1px solid #ddd; color: #999; font-size: 0.85em; }
</style>
</head>
<body>

<h1>PilotAI Daily Report &mdash; 2026-04-23</h1>
<p><strong>Operator:</strong> Maximus (AI Trading Strategist) &nbsp;|&nbsp; <strong>Owner:</strong> Carlos Cruz &nbsp;|&nbsp; <strong>Phase:</strong> 8 (Paper Trading Deployment) &nbsp;|&nbsp; <strong>MASTERPLAN:</strong> v12</p>

<!-- ============================================================ -->
<h2><span class="section-num">1.</span> Executive Summary</h2>

<p>Today's session combined <strong>alpha discovery research</strong> (5 new experiments) with a <strong>comprehensive code quality audit and remediation</strong>. The central finding is strategic: <em>equity-based strategies do not clear our quality bar</em>. The variance risk premium (VRP) harvested by options strategies is 50&ndash;100&times; larger per trade than any equity factor edge we tested. AUM expansion requires Polygon ($199/mo) to unlock IWM/DIA/EEM options &mdash; not equity factor strategies.</p>

<div style="display: flex; flex-wrap: wrap; margin: 16px 0;">
  <div class="metric-box"><div class="label">Experiments Run</div><div class="value">5</div></div>
  <div class="metric-box"><div class="label">Experiments Killed</div><div class="value red">5</div></div>
  <div class="metric-box"><div class="label">Tests Fixed</div><div class="value green">18 &rarr; 0</div></div>
  <div class="metric-box"><div class="label">Rule Zero Violations Fixed</div><div class="value green">3 / 3</div></div>
  <div class="metric-box"><div class="label">Test Suite</div><div class="value green">12,607</div></div>
  <div class="metric-box"><div class="label">Coverage</div><div class="value">58.6%</div></div>
</div>

<div class="callout">
  <strong>Key strategic conclusion:</strong> Put credit spreads collect 200&ndash;400 bps of edge per trade (variance risk premium). The overnight premium delivers 3.9 bps/day &mdash; destroyed by 4 bps of round-trip execution cost. Sector momentum's best walk-forward Sharpe is 0.57 &mdash; less than SPY buy-and-hold. <strong>The VRP edge from options is 50&ndash;100&times; larger per trade than any equity factor strategy we tested.</strong>
</div>

<!-- ============================================================ -->
<h2><span class="section-num">2.</span> Experiment Scorecard</h2>

<table>
<tr>
  <th>Exp</th>
  <th>Name</th>
  <th>Hypothesis</th>
  <th>Key Metric</th>
  <th>Result</th>
  <th>Kill Reason</th>
</tr>
<tr class="killed">
  <td><strong>EXP-2910</strong></td>
  <td>TLT Put Credit Spreads</td>
  <td>Bond VRP is persistent and uncorrelated (&#961; = 0.06) with equity VRP</td>
  <td>Trade Sharpe: <span class="fail">0.76</span> (need &ge;1.0)<br>Trades/yr: <span class="fail">9.2</span> (need &ge;20)</td>
  <td><span class="tag tag-kill">KILLED</span></td>
  <td>Insufficient trade frequency (IronVault has only monthly expirations). TLT premium too thin ($0.045 median credit). 9-stream net Sharpe 4.94 &lt; 6.0.</td>
</tr>
<tr class="killed">
  <td><strong>EXP-2920</strong></td>
  <td>TLT IV-RV Arb (MOVE Index)</td>
  <td>MOVE-VIX &#961; = 0.13 &rarr; bond vol is a genuinely different factor; 5 approaches tested</td>
  <td>Best Sharpe: <span class="fail">0.26</span> (long/short z=1)<br>All 5 approaches &lt; 1.0</td>
  <td><span class="tag tag-kill">KILLED</span></td>
  <td>Bond VRP is real but not harvestable during structural rate regime (2020&ndash;2026). MOVE-filtered spreads <em>reduced</em> Sharpe. TLT declined 40% over period &mdash; directional headwind overwhelms vol signals.</td>
</tr>
<tr class="killed">
  <td><strong>EXP-2930</strong></td>
  <td>SOXX/XLK Feasibility</td>
  <td>Semiconductor (SOXX) and tech (XLK) ETFs could add capacity via put credit spreads</td>
  <td>SOXX &#961;(QQQ): <span class="fail">0.888</span><br>XLK &#961;(QQQ): <span class="fail">0.970</span></td>
  <td><span class="tag tag-kill">KILLED</span></td>
  <td>Both are QQQ clones. SOXX &#961; = 0.888 (threshold: &lt;0.70). XLK &#961; = 0.970 plus only 13.9 trades/yr. Neither warrants full walk-forward.</td>
</tr>
<tr class="killed">
  <td><strong>EXP-2940</strong></td>
  <td>Overnight Return Premium</td>
  <td>Buy SPY+QQQ at close, sell at open &mdash; academic anomaly (Lou, Polk &amp; Skouras 2019)</td>
  <td>Gross Sharpe: <span class="warn">0.959</span><br>Net (2 bps slip): <span class="fail">-0.02</span></td>
  <td><span class="tag tag-kill">KILLED</span></td>
  <td><strong>Slippage-fatal.</strong> Edge is 3.9 bps/day; round-trip execution costs are 4 bps. Strategy has negative expected returns after realistic MOO slippage. Also &#961; = 0.577 with exp1220 and -23% max DD.</td>
</tr>
<tr class="killed">
  <td><strong>EXP-2950</strong></td>
  <td>Sector Momentum Rotation</td>
  <td>11 SPDR sector ETFs, monthly rebalance, 3/6/12m momentum; 13 variants tested</td>
  <td>Best WF Sharpe: <span class="fail">0.57</span> (long_top3_3m)<br>All L/S variants negative</td>
  <td><span class="tag tag-kill">KILLED</span></td>
  <td>Sector momentum has decayed below quality bar. Best variant (0.57) is worse than SPY buy-and-hold (0.59). All cross-sectional long-short variants have <em>negative</em> Sharpe. 9-stream integration degraded portfolio by -0.25.</td>
</tr>
</table>

<div class="callout-red callout">
  <strong>5 for 5 killed.</strong> Zero viable new alpha streams discovered today. This is an honest result &mdash; the kill discipline is working. The quality bar (Sharpe &ge; 1.0, trades/yr &ge; 20, &#961; &lt; 0.5, net positive after costs) correctly filtered all five candidates.
</div>

<!-- ============================================================ -->
<h2><span class="section-num">3.</span> Code Quality Improvements</h2>

<h3>Before / After Comparison</h3>

<table>
<tr><th>Metric</th><th>Before (AM)</th><th>After (PM)</th><th>Delta</th></tr>
<tr><td>Tests Passing</td><td>12,589</td><td class="pass">12,607</td><td class="pass">+18 (all failures fixed)</td></tr>
<tr><td>Tests Failing</td><td class="fail">18</td><td class="pass">0</td><td class="pass">-18</td></tr>
<tr><td>Rule Zero Violations</td><td class="fail">3 confirmed</td><td class="pass">0</td><td class="pass">All fixed</td></tr>
<tr><td>Test Coverage</td><td>58.12%</td><td>58.60%</td><td>+0.48pp</td></tr>
<tr><td>Rule Zero Grade</td><td class="fail">C</td><td class="pass">A</td><td class="pass">+2 grades</td></tr>
</table>

<h3>Production Code Fixes (4 files)</h3>
<table>
<tr><th>File</th><th>Fix</th><th>Impact</th></tr>
<tr><td><code>shared/database.py</code></td><td>Metadata preservation on partial <code>upsert_trade</code> updates</td><td>IC strikes and straddle fields no longer silently wiped on status transitions</td></tr>
<tr><td><code>execution/position_monitor.py</code></td><td>Handle None strikes in SL/PT formula check</td><td>Formula-only stop-loss now fires correctly for positions missing strike data</td></tr>
<tr><td><code>compass/ensemble_model_health.py</code></td><td>Replaced synthetic KS-test samples with <code>scipy.stats.kstest</code> CDF</td><td>Drift detection uses real statistics, no synthetic data</td></tr>
<tr><td><code>compass/adaptive_1dte.py</code></td><td>Quarantined <code>build_exp1220_daily()</code> &mdash; was generating fabricated returns</td><td>Function raises <code>NotImplementedError</code> with Rule Zero warning</td></tr>
</table>

<h3>Rule Zero Violations Resolved</h3>
<table>
<tr><th>File</th><th>Violation</th><th>Action</th></tr>
<tr><td><code>experiments/EXP-1020-max/backtest.py</code></td><td><code>rng.normal()</code> for synthetic intraday moves</td><td><span class="tag tag-kill">QUARANTINED</span> (renamed to .QUARANTINED_RULE_ZERO)</td></tr>
<tr><td><code>compass/adaptive_1dte.py</code></td><td><code>np.random.normal()</code> to synthesize daily returns from yearly targets</td><td><span class="tag tag-fix">FIXED</span> &rarr; NotImplementedError</td></tr>
<tr><td><code>compass/ensemble_model_health.py</code></td><td><code>np.random.RandomState(42).normal()</code> for drift testing</td><td><span class="tag tag-fix">FIXED</span> &rarr; scipy.stats.kstest</td></tr>
</table>

<!-- ============================================================ -->
<h2><span class="section-num">4.</span> Strategic Conclusions &mdash; AUM Scaling</h2>

<h3>4.1 The Options VRP Edge Is Irreplaceable</h3>

<table>
<tr><th>Strategy Type</th><th>Edge Per Trade</th><th>Best Sharpe Found</th><th>Survives Execution Costs?</th></tr>
<tr><td><strong>Put credit spreads</strong> (our core)</td><td><strong>200&ndash;400 bps</strong> (20&ndash;40% of width)</td><td>3.85 (SPY, EXP-1220)</td><td class="pass">YES &mdash; edge &gt;&gt; costs</td></tr>
<tr><td>Overnight premium</td><td>3.9 bps/day</td><td>0.96 gross &rarr; -0.02 net</td><td class="fail">NO &mdash; edge &lt; costs</td></tr>
<tr><td>Sector momentum</td><td>~2 bps/day (implied)</td><td>0.57 WF</td><td class="fail">NO &mdash; below benchmark</td></tr>
<tr><td>TLT IV-RV arb</td><td>~1 bps/day</td><td>0.26</td><td class="fail">NO &mdash; TLT headwind</td></tr>
<tr><td>TLT put credit spreads</td><td>$0.045 median credit</td><td>0.76</td><td class="warn">Marginal &mdash; too thin</td></tr>
</table>

<div class="callout-green callout">
  <strong>The path to $1B AUM is through more options underliers, not equity factor strategies.</strong> The Polygon subscription ($199/mo) unlocks IWM ($2.1B/day options ADV), DIA, and EEM. IWM alone could add $500M of capacity. This is the single highest-ROI investment for the portfolio.
</div>

<h3>4.2 What We Learned About Each Candidate Asset Class</h3>

<table>
<tr><th>Asset Class</th><th>Finding</th><th>Recommendation</th></tr>
<tr><td><strong>Bonds (TLT)</strong></td><td>Correlation is excellent (&#961; = 0.06) but bond VRP is not harvestable during structural rate regime changes. MOVE index has zero predictive lead over VIX.</td><td>Abandon TLT as alpha source. Revisit after Polygon (weekly expirations would fix trade frequency).</td></tr>
<tr><td><strong>Semiconductors (SOXX)</strong></td><td>&#961; = 0.888 with QQQ &mdash; it's a QQQ clone. IronVault data has 3-year gap (2020&ndash;2023).</td><td>Killed. No further work.</td></tr>
<tr><td><strong>Tech (XLK)</strong></td><td>&#961; = 0.970 with QQQ &mdash; literally a QQQ subset. 13.9 trades/yr, $0.45 avg credit.</td><td>Killed. No further work.</td></tr>
<tr><td><strong>Overnight equity</strong></td><td>Academic anomaly is real (Sharpe 0.96 gross) but slippage-fatal. Edge (3.9 bps) &lt; execution cost (4 bps). 28% of WF folds negative.</td><td>Killed. Tue/Wed substructure noted for future micro-overlay research.</td></tr>
<tr><td><strong>Sector momentum</strong></td><td>Alpha has decayed below benchmark. Best variant (0.57 WF Sharpe) &lt; SPY buy-and-hold (0.59). All L/S variants negative.</td><td>Killed. Do not add more equity factor strategies.</td></tr>
</table>

<!-- ============================================================ -->
<h2><span class="section-num">5.</span> Recommended Next Steps (Prioritized)</h2>

<table>
<tr><th>Priority</th><th>Action</th><th>Rationale</th><th>Blocker</th></tr>
<tr><td><strong>P0</strong></td><td>Provision Alpaca paper API keys</td><td>Phase 8 paper trading has been ready since Apr 8. This is the #1 blocker.</td><td>Carlos to provision</td></tr>
<tr><td><strong>P0</strong></td><td>Provision Polygon Options subscription ($199/mo)</td><td>Unlocks IWM/DIA/EEM options. IWM alone adds ~$500M capacity. <em>This is the path to AUM scaling</em>, not equity factor strategies.</td><td>Carlos to approve</td></tr>
<tr><td><strong>P1</strong></td><td>Run IWM put credit spread backtest (EXP-2960)</td><td>76K puts/day, $2.1B/d ADV. Highest-capacity candidate after SPY/QQQ. Requires Polygon data.</td><td>Polygon subscription</td></tr>
<tr><td><strong>P1</strong></td><td>Run DIA put credit spread backtest (EXP-2970)</td><td>7K puts/day. Dow Jones exposure adds diversification from tech-heavy SPY/QQQ.</td><td>Polygon subscription</td></tr>
<tr><td><strong>P2</strong></td><td>Reorganize compass/ directory</td><td>435 files in flat structure. Separate ~20 production modules from ~270 archived research files.</td><td>None</td></tr>
<tr><td><strong>P2</strong></td><td>Raise reconciler test coverage to 70%+</td><td>Currently 46%. Critical module for live trading position sync.</td><td>None</td></tr>
<tr><td><strong>P3</strong></td><td>Revisit TLT after Polygon</td><td>Weekly expirations would fix trade frequency (9.2 &rarr; ~26/yr). &#961; = 0.06 makes it the best diversifier.</td><td>Polygon subscription</td></tr>
</table>

<!-- ============================================================ -->
<h2><span class="section-num">6.</span> Updated Experiment Registry</h2>

<h3>Wave 12: AUM Capacity Research (Apr 21&ndash;23)</h3>

<table>
<tr><th>Exp ID</th><th>Name</th><th>Status</th><th>Key Finding</th></tr>
<tr><td>EXP-2910</td><td>TLT Put Credit Spreads</td><td><span class="tag tag-kill">KILLED</span></td><td>Sharpe 0.76, 9.2 trades/yr. Premium too thin.</td></tr>
<tr><td>EXP-2920</td><td>TLT IV-RV Arb (MOVE Index)</td><td><span class="tag tag-kill">KILLED</span></td><td>5 approaches tested, best Sharpe 0.26. Bond VRP not harvestable in rate regime.</td></tr>
<tr><td>EXP-2930</td><td>SOXX/XLK Feasibility</td><td><span class="tag tag-kill">KILLED</span></td><td>Both QQQ clones (&#961; = 0.888 / 0.970).</td></tr>
<tr><td>EXP-2940</td><td>Overnight Return Premium</td><td><span class="tag tag-kill">KILLED</span></td><td>Slippage-fatal. 3.9 bps edge vs 4 bps cost.</td></tr>
<tr><td>EXP-2950</td><td>Sector Momentum Rotation</td><td><span class="tag tag-kill">KILLED</span></td><td>WF Sharpe 0.57 &lt; SPY buy-and-hold 0.59.</td></tr>
</table>

<h3>Cumulative Experiment Count</h3>

<div style="display: flex; flex-wrap: wrap; margin: 16px 0;">
  <div class="metric-box"><div class="label">Total Experiments</div><div class="value">~100</div></div>
  <div class="metric-box"><div class="label">Waves Completed</div><div class="value">12</div></div>
  <div class="metric-box"><div class="label">Killed (Honest)</div><div class="value">~35</div></div>
  <div class="metric-box"><div class="label">Production Streams</div><div class="value">8</div></div>
  <div class="metric-box"><div class="label">Net Sharpe (v8a)</div><div class="value">6.39</div></div>
  <div class="metric-box"><div class="label">AUM Cap</div><div class="value">~$50M</div></div>
</div>

<!-- ============================================================ -->
<h2><span class="section-num">7.</span> Portfolio Status (Unchanged)</h2>

<p>The v8a 8-stream portfolio remains the production configuration. No new streams were added today (all 5 candidates killed). North Star targets:</p>

<table>
<tr><th>Target</th><th>Goal</th><th>v8a NET</th><th>Status</th></tr>
<tr><td>Sharpe (pooled)</td><td>&ge; 6.0</td><td><strong>6.39</strong></td><td class="pass">MET</td></tr>
<tr><td>CAGR</td><td>&ge; 100%</td><td><strong>118%</strong></td><td class="pass">MET</td></tr>
<tr><td>Max DD</td><td>&le; 12%</td><td><strong>5.12%</strong></td><td class="pass">MET</td></tr>
<tr><td>AUM capacity</td><td>&ge; $1B</td><td><strong>~$50M</strong></td><td class="fail">NOT MET</td></tr>
</table>

<div class="callout">
  <strong>AUM remains the sole structural gap.</strong> Today's research confirms that the path to $1B is through Polygon-enabled options on IWM/DIA/EEM &mdash; not through equity factor strategies. The $199/mo Polygon subscription is the single highest-ROI investment for the portfolio.
</div>

<div class="footer">
  <p>Generated: 2026-04-23 &nbsp;|&nbsp; Operator: Maximus &nbsp;|&nbsp; All data sources are real. No synthetic data. Rule Zero held. &nbsp;|&nbsp; <em>The market is the only judge.</em></p>
</div>

</body>
</html>
