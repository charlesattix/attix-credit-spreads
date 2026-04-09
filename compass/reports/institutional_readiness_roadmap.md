# Institutional Readiness Roadmap

**Scope:** regulatory, operational, and infrastructure requirements for
scaling the 8-stream options portfolio from proprietary paper-trading
into an institutional-grade product accepting external capital from
$100M through $1B+.
**Date:** 2026-04-09
**Status:** research and planning document — **not legal advice**

> **Critical disclaimer.** Every number, rule citation, and threshold
> in this document is drawn from US securities regulation and
> industry practice as of early 2026. Regulations change. Before
> taking any step described here, engage a qualified securities
> attorney, a fund-formation specialist, and a compliance consultant.
> This document is a planning guide for management conversations, not
> a substitute for professional counsel.

---

## 1. Executive summary

Moving from a proprietary paper strategy to an institutional product
crosses four gating thresholds that change what is legally required:

| Threshold | Regulatory step | Operational step | Tech step |
|---|---|---|---|
| $0 – $25M | Exempt Reporting Adviser (ERA) state registration possible | Self-managed books | Consumer-grade cloud OK |
| $25M – $100M | **State RIA registration** required in most states | Fund administrator recommended | DR plan required |
| **$100M+** | **SEC RIA registration** mandatory | Big-4 / top-tier fund auditor, independent CCO | SOC 2 Type II strongly recommended |
| $1.5B+ | **Form PF Section 2 large-adviser reporting** | Institutional ODD survives tier-1 allocators | SOC 2 Type II mandatory for most LPs |

The single biggest discontinuity is the **$100M AUM trigger**.
Below that the adviser is regulated by state authorities and
reporting is light. At or above $100M the adviser must register
with the SEC on Form ADV, publish Part 2 brochures, submit to
SEC examination authority, and comply with the Advisers Act of
1940 in its entirety (Rule 204-2 books-and-records, Rule 206(4)-7
compliance program, Rule 206(4)-2 custody rule, etc.).

The second biggest is the **$1.5B private-fund threshold** that
activates quarterly Form PF Section 2 reporting for large hedge fund
advisers.

---

## 2. Regulatory framework

### 2.1 SEC / state investment-adviser registration

The Investment Advisers Act of 1940 and the Dodd-Frank amendments
define three tiers of adviser registration based on regulatory AUM:

| Tier | AUM range | Regulator | Primary filing |
|---|---|---|---|
| **Exempt Reporting Adviser** | < $150M AUM *and* only advises private funds | SEC (notice only) | Form ADV Parts 1A & 1B (shortened) |
| **State-registered adviser** | $25M – $100M (varies by state) | State securities commissions | State Form ADV |
| **SEC-registered adviser (RIA)** | ≥ $100M (or crossing in either direction per the $90M/$110M switching window) | SEC | Form ADV Parts 1A, 2A, 2B |

**Key actions at each tier:**

- **ERA (simplest, private fund only):** file Form ADV Part 1A
  Sections 1, 3, 6, 7, 10, 11, 12. Update within 30 days of material
  change. No books-and-records rule and no custody rule burden.
- **State RIA:** file state Form ADV, pay state filing fees (~$150
  – $500 per state), comply with state-specific books-and-records.
  Must register a qualified IAR (investment-adviser representative).
- **SEC RIA (the main target):**
  - File Form ADV Parts 1A, 2A, 2B within 90 days of crossing
    $100M
  - Designate a Chief Compliance Officer (Rule 206(4)-7)
  - Implement written compliance policies and procedures and test
    annually
  - Comply with the custody rule (Rule 206(4)-2) — typically met by
    using a qualified custodian and subject to annual surprise
    custody audit by an independent PCAOB-registered accountant
  - Comply with Rule 204-2 books-and-records (5-year retention for
    most records, first 2 years on-site or cloud-accessible)
  - Prepare for routine SEC examination (typical cycle 2–5 years
    for new registrants)

### 2.2 FINRA considerations

FINRA regulates broker-dealers, not investment advisers. A pure
investment adviser running a private fund through a third-party
prime broker does NOT need FINRA registration. Registration becomes
relevant if:

- The adviser also markets securities (could trigger broker-dealer
  registration or Series-7 / Series-66 requirements for sales
  personnel)
- The adviser uses its own execution to route customer orders
- The adviser charges transaction-based compensation from the fund

Standard institutional practice: route execution through an
unaffiliated prime broker, keep the adviser squarely inside RIA
territory, and avoid FINRA altogether.

### 2.3 Private-fund exemptions

To accept outside capital into a pooled vehicle you need a fund
structure that relies on a Section 3 exemption from the Investment
Company Act of 1940:

| Exemption | Investor limit | Investor type | Typical use |
|---|---|---|---|
| **Section 3(c)(1)** | ≤ 100 beneficial owners | accredited investors (Reg D 506(b) or 506(c)) | Small initial fund |
| **Section 3(c)(7)** | ≤ ~2,000 beneficial owners | "qualified purchasers" ($5M+ investments for individuals, $25M+ for entities) | Scale target for institutional |

**Practical recommendation:** start with a **3(c)(1) master/feeder**
structure, transition to **3(c)(7)** when the LP count approaches
75–80 to leave a safety margin. Tax-inefficient investors (US
tax-exempts, foreign) typically allocate through an offshore feeder
(Cayman Islands LP or limited company).

### 2.4 Form PF reporting

Form PF is a confidential filing to the SEC used for systemic-risk
monitoring. The reporting thresholds for hedge fund advisers:

| Adviser AUM (hedge fund) | Reporting requirement |
|---|---|
| < $150M | Exempt from Form PF |
| $150M – $1.5B | Section 1 only (annual) |
| ≥ $1.5B | **Section 2** (quarterly, extensive) |

Section 2 filings are substantial: monthly performance, portfolio
composition, VaR and DV01, counterparty exposures, liquidity
profile, leverage, margin, and stress-test results. Large advisers
typically use a dedicated reg-reporting platform (e.g. Confluence,
SS&C GlobeOp, Advise Technologies) rather than hand-filing.

### 2.5 Options-specific considerations

- The underlying strategy is **listed US options only**, which means
  **no CFTC or NFA registration** required (no futures, no swaps).
  This materially simplifies the registration footprint versus a
  futures-heavy strategy.
- If the strategy expands into **SX5E, DAX, or other non-US
  listed options** in Phase C, the adviser may need **AIFMD
  marketing permissions** to accept European LPs and may face
  incremental reporting to local regulators.
- **Section 1256** tax treatment for most listed index options
  (60/40 long/short) is a marketing advantage — note it in the
  fund's PPM.

---

## 3. Prime broker requirements

### 3.1 Tier landscape and minimums

| Tier | Examples | Typical minimum AUM | Options expertise |
|---|---|---|---|
| **Tier-0 prop desk** | IBKR Pro, Tastytrade, TradeStation | No minimum | Strong on retail option flow, no institutional services |
| **Mid-tier electronic** | Wedbush, Cowen / TD Cowen, Clear Street, Marex (formerly ED&F Man) | $10M – $50M | Good for small hedge funds; options-aware but limited synthetic financing |
| **Tier-1 prime brokers** | Goldman Sachs, Morgan Stanley, JP Morgan, Barclays, BNP Paribas, BofA | **$100M–$250M** | Full-service: synthetic financing, cap intro, ODD support |
| **Options specialists** | Susquehanna Private Equity Partners, Wolverine, SMBC Nikko | $50M – $150M | Deep options books but fewer LP references |

At $100M AUM, the entry door to Tier-1 primes cracks open. Most
allocate a mid-tier prime (Clear Street or Wedbush) for the first
$50–100M, then add a Tier-1 prime in parallel once above $150M to
access deeper financing and cap intro.

### 3.2 What prime brokers ask for

Standard onboarding requires:

- Legal entity documents (LP agreement, PPM, side letters if any)
- Adviser's Form ADV Parts 1A and 2A
- Audited financials (if fund has them) or opening cap table
- Operational due diligence questionnaire (see §5)
- Counterparty credit review (for primes extending margin)
- KYC/AML on the GP, the GP's principals, and each LP at or above
  the 5% beneficial-ownership threshold
- Wolfsberg AML certification in some cases
- A written order-execution and best-execution policy
- Evidence of trade-allocation procedures (pro-rata across fund
  vehicles) — critical for advisers running multiple funds

**Timeline:** 4–12 weeks from first meeting to funded account at
a Tier-1 prime. Start the conversation 3–6 months before the target
go-live.

### 3.3 Recommended approach for this portfolio

Given that the strategy is listed options only, weekly cadence, no
exotic derivatives, and ~$50M paper-traded:

1. **Phase A ($50M–$200M):** keep Alpaca paper and add an IBKR Pro
   institutional account as the first live broker. IBKR Pro has no
   minimum, supports all the strategy's instruments, and has a
   reasonable institutional onboarding process.
2. **Phase B ($200M–$500M):** add a mid-tier prime such as Clear
   Street or Wedbush. These firms compete hard for emerging managers
   and their onboarding is quicker than Tier-1 primes. They will
   provide basic synthetic-financing and cap-intro services.
3. **Phase C ($500M+):** begin Tier-1 prime conversations (Goldman
   Sachs / Morgan Stanley / JP Morgan). Expect 6 months from first
   meeting to funded account. Keep the mid-tier relationship as a
   redundancy and failover.

---

## 4. Audit, custody, and compliance infrastructure

### 4.1 Annual fund audit

Institutional LPs will not allocate without an annual audited NAV.
Standards:

- **Auditor:** PCAOB-registered, preferably one of the "Big 4"
  (PwC, Deloitte, EY, KPMG) or a tier-2 fund-audit specialist
  (Withum, Anchin, Citrin Cooperman, EisnerAmper, Grant Thornton).
- **Standard:** US GAAP with ASC 946 Investment Companies treatment.
- **Timeline:** audited financials delivered within 120 days of
  fiscal year end (Rule 206(4)-2 exception for pooled vehicles that
  deliver audited financials replaces the surprise-custody exam).
- **Cost:** typical range $40K–$100K for a first-year audit on a
  ~$100M single-strategy fund. Scales sub-linearly with AUM.

### 4.2 Fund administrator

Required at institutional scale even though not strictly mandated by
the Advisers Act:

- **Services:** daily / weekly NAV calculation, investor-level
  accounting, capital-call and distribution processing, GAAP-ready
  trial balance, Form K-1 prep, investor statements, AML/KYC.
- **Providers:** SS&C GlobeOp, Citco, NAV Consulting, Apex Group,
  MG Stover, Opus Fund Services, Trident Fund Services.
- **Cost:** typically 3–8 bps of AUM per year, with $60K–$120K
  minimums.

### 4.3 Chief Compliance Officer (Rule 206(4)-7)

Once SEC-registered, the adviser MUST designate a CCO with:

- Authority to enforce written compliance policies
- Independent access to senior management
- Responsibility for annual compliance review

Two common models:

- **In-house CCO** — a named employee of the adviser. Expensive
  (fully-loaded cost $150K–$300K/year) but signals institutional
  commitment and scales to $1B+ without friction.
- **Outsourced CCO** — a compliance consultant named as CCO in
  Form ADV. Cheaper ($40K–$80K/year from firms like ACA Group,
  Foreside, Cipperman) but tier-1 LPs often view it as a red flag
  above $250M AUM and require an in-house successor.

**Recommendation:** outsourced CCO from $100M–$250M, in-house CCO
from $250M upward.

### 4.4 Custody rule (Rule 206(4)-2)

For a pooled investment vehicle, the adviser is deemed to have
custody of client assets. Compliance options:

1. **Deliver audited financial statements to all investors within
   120 days of fiscal year end** — the standard exemption. Requires
   a PCAOB-registered independent auditor. This is the normal path.
2. **Surprise annual custody audit** — alternative if audited
   financials are not delivered on time. More expensive and more
   disruptive. Avoid this path.

### 4.5 Books and records (Rule 204-2)

Five-year retention, first two years in an "easily accessible
place":

- Trade tickets, broker confirmations, order memoranda
- Portfolio holdings and valuations at each NAV date
- Written policies and procedures
- Marketing materials and performance presentations (10-year
  retention for marketing)
- Emails and instant messages pertaining to business conducted
  (requires an email-archive vendor: Smarsh, Global Relay, MessageOps)
- Proxy voting records

**Cost:** email archive $1K–$3K/month at small scale. Books and
records platform (e.g. Eze Eclipse, Advent Geneva, Bloomberg AIM)
$50K–$200K/year depending on fund count and complexity.

### 4.6 Marketing and performance (GIPS)

**Global Investment Performance Standards (GIPS 2020)** are the
institutional standard for performance presentation. GIPS compliance
is not legally required but is a de facto condition for most
institutional allocators (pensions, foundations, fund-of-funds).

Key obligations:

- Present net-of-fees returns in all marketing
- Include composite returns (GIPS requires composites for all
  fee-paying discretionary portfolios)
- Provide at least five years of GIPS-compliant history (or since
  inception, whichever is less)
- Verification by a qualified GIPS verifier recommended but optional

**Cost:** first-year GIPS verification typically $25K–$60K.

---

## 5. Operational due diligence (ODD) checklist

Institutional allocators run ODD separately from investment due
diligence. A "good" ODD team will spend 30–60 days on-site, review
hundreds of documents, and interview most of the operations staff.
Any single failure category is usually veto power over the entire
allocation regardless of investment merit.

### 5.1 Standard ODD categories (AIMA / SBAI framework)

| Category | Representative questions | Our current state | Gap |
|---|---|---|---|
| **Firm overview** | Legal entity structure, ownership, headcount, domicile, tenure of principals | Needs formalising | Legal formation + team bios |
| **Personnel and background checks** | Background screens (FINRA BrokerCheck, state regulator, criminal, credit) on all GPs and key employees | Not done | Engage BackgroundChecks.com or HireRight |
| **Track record and substantiation** | Independent verification of reported returns; can the audited NAVs be tied to trade logs? | Walk-forward validated (EXP-2280) but not audited | Need one year of audited paper track record |
| **Investment process** | Written investment policy, portfolio construction, risk limits, rebalance cadence | MASTERPLAN + EXP-2410 config | Needs to be formalised into a 10–20 page IP document |
| **Risk management** | Limits, monitoring, breach escalation, stress tests, independence of risk function | EXP-2220, 2750, 2820, 2920 | Independent risk officer at $250M+ |
| **Compliance** | CCO designation, compliance manual, testing, code of ethics, personal-trading policy | None yet | Build in Phase 1 |
| **Operations** | Trade settlement, reconciliation, NAV calculation, error policy | Ad-hoc | Needs fund administrator |
| **Counterparty risk** | Prime broker list, concentration, margin agreements, failover plan | IBKR + Alpaca | Formalise at Phase B |
| **Valuation policy** | Who prices, how, independence of pricing function, how are stale/illiquid positions handled | Daily close (liquid options) | Written valuation policy needed |
| **Cash management** | Where is idle cash held, segregation, sweep rules | N/A (paper) | Formalise at fund launch |
| **Technology** | DR, BCP, cybersecurity, access controls | EXP-2520 monitoring + EXP-2920 spec | Needs SOC 2 by Phase C |
| **Service providers** | Named auditor, administrator, legal counsel, IT vendor, insurance broker | Not engaged | Engage in Phase 1 |
| **Insurance** | E&O, D&O, fidelity bond, cyber liability | Not carried | Quote at Phase 1 |
| **Regulatory history** | Any disciplinary actions, litigation, investigations against the GP or principals | Clean (new entity) | Maintain |
| **Side letters and conflicts** | Most-favoured-nation terms, fee concessions, personal trading | None | Track once fund launches |

### 5.2 Common red flags

ODD teams typically **veto the allocation** when they find any of:

- Backtest-only track record (paper trading is tolerated, backtest-
  only is not)
- CCO is the CIO or the founder (independence concern)
- Adviser is its own administrator or auditor
- Pricing is controlled by the portfolio manager
- Any material discrepancy between Form ADV and what the GP says in
  meetings
- Books and records kept in spreadsheets or personal email
- No written BCP/DR plan or one not tested in last 12 months
- Key-person risk without a documented succession plan

### 5.3 Standard ODD document request list

Allocators typically request 40–80 documents up front. The big
ones:

1. Form ADV Parts 1 and 2
2. PPM / Offering memorandum
3. LPA / operating agreement
4. Compliance manual and code of ethics
5. BCP/DR plan with last test date
6. Cybersecurity policy
7. Valuation policy
8. Brokerage agreements
9. Audit opinion (most recent year)
10. SSAE 18 / SOC 2 report for key vendors
11. Insurance declarations pages
12. Trading blotter sample (last 30 days)
13. NAV reconciliation sample
14. Compliance testing results (last annual review)
15. Org chart and key-person bios

---

## 6. Technology infrastructure

### 6.1 Disaster recovery and business continuity

Institutional LPs expect an RPO (recovery point objective) of ≤
4 hours and an RTO (recovery time objective) of ≤ 4 hours for the
core trading system, and ≤ 24 hours for reporting systems.

**Requirements:**

- Hot/warm standby environment in a geographically separate region
  (not same data centre, not same cloud AZ)
- Automated failover tested at least annually (some LPs want
  semi-annually, documented with timestamps)
- Data replication with point-in-time recovery for at least 7 days
- Documented runbook for every critical failure scenario

**Current state:** EXP-2520 + EXP-2920 provide a Mac Studio–hosted
daemon with Telegram alerts, 5-minute polling, and basic state
files. This is an excellent paper-trading foundation and completely
insufficient for institutional DR. The Mac Studio is a single
point of failure.

**Gap to close:**

- Move the engine off the Mac Studio to at least a cloud VM with
  automated snapshots (AWS EC2, GCP Compute, Azure VM) at Phase A
  end
- Add a second region warm standby at Phase B
- Add a full hot-hot configuration with automatic cutover at Phase C
- Document every failure scenario in a BCP runbook

### 6.2 Redundancy and high availability

| Component | Paper tier (today) | Phase A target | Phase C target |
|---|---|---|---|
| Compute | Single Mac Studio | Cloud VM + snapshot | Active/active across regions |
| Network | Residential ISP | Cloud provider SLA | Multi-homed BGP |
| Database | SQLite single file | Managed PG / MySQL | Managed PG + replica + WAL archive |
| Broker API | Alpaca paper | Alpaca paper + IBKR Pro | 2+ primes with automated cutover |
| Market data | Yahoo + IronVault local | Polygon Advanced + Yahoo fallback | OPRA direct + 2 backup feeds |
| Monitoring | Telegram + local log | Telegram + PagerDuty + Datadog | Grafana + PagerDuty + 24/7 NOC vendor |
| Secrets | env vars on Mac | Vault or AWS Secrets Manager | HSM-backed secret store |

### 6.3 Monitoring and observability

The EXP-2920 monitoring spec already defines the 7-dimension matrix
and the 4 MASTERPLAN abort triggers. Institutional readiness extends
this with:

- **Infrastructure-level monitoring:** CPU, memory, disk, network,
  process liveness (Datadog, New Relic, or Prometheus + Grafana)
- **Application-level SLOs:** order-submission latency p50/p95/p99,
  fill deviation, state-file write latency, market-data freshness
- **On-call rotation:** PagerDuty or Opsgenie with documented
  escalation paths
- **Incident management:** written post-mortems for every abort
  trigger event and every SLO breach
- **Regulator-ready audit trail:** immutable append-only log of
  every order, fill, cancel, reject, breaker event, and config
  change (17a-4 compliance)

### 6.4 Cybersecurity

SEC Regulation S-P and the 2024 cyber risk management rules require
a written information security program. Practical components:

- **SOC 2 Type II** report from a qualified auditor covering
  security, availability, and confidentiality. **Strongly recommended
  at $100M, mandatory for tier-1 LPs at $500M+.** First-year cost
  $40K–$80K, annual refresh $25K–$50K.
- **Penetration testing:** annual external pen test by a qualified
  firm. $15K–$40K.
- **Employee training:** phishing simulations, security-awareness
  training, written acceptable-use policy.
- **Access control:** least-privilege, MFA on every system, role-
  based access to Alpaca / IBKR production credentials.
- **Endpoint security:** managed detection and response (CrowdStrike,
  SentinelOne) on every device with production access.
- **Vulnerability management:** patched within 30 days for high
  severity, 90 days for medium.
- **Incident response plan:** documented, tested at least annually,
  aligned with NIST CSF 2.0.

### 6.5 SOC 2 specifically

SOC 2 Type II is the de-facto cybersecurity certification for fund
managers. The five Trust Services Criteria are Security,
Availability, Processing Integrity, Confidentiality, and Privacy.
Most fund managers scope to Security + Availability + Confidentiality.

Typical timeline:

- **Month 0–3:** engage auditor (Schellman, A-LIGN, Coalfire, BARR
  Advisory). Scope the report.
- **Month 3–6:** implement controls (access control, change
  management, encryption, logging, vendor management). Most
  expensive step — usually requires a security consultant at
  ~$25K–$60K.
- **Month 6–12:** observation period (minimum 6 months for Type II).
- **Month 12–14:** audit fieldwork, report delivery.

**Total cost for first year: ~$80K–$150K** including consulting,
tooling, and audit fees. Annual refresh ~$30K–$60K.

---

## 7. Phased readiness roadmap

Aligns with the AUM scaling phases in
`compass/research/aum_scaling_roadmap.md`.

### Phase A — $50M → $200M (prop / early external capital)

| Step | Owner | Timeline | Cost |
|---|---|---|---|
| Engage securities counsel | GP | Month 0 | $10K–$30K retainer |
| Form LP + GP entities (DE LP + DE LLC GP) | Counsel | Month 0–1 | $15K–$25K setup |
| Draft PPM + LPA under Reg D 506(c) | Counsel | Month 1–2 | $25K–$60K |
| File Form ADV as ERA (if first capital is private funds only) | Counsel | Month 2 | included |
| Engage fund administrator | GP | Month 2 | $60K/yr floor |
| Engage auditor for first-year audit | GP | Month 2 | $40K–$60K |
| Engage outsourced CCO | GP | Month 3 | $40K–$80K/yr |
| Move engine from Mac Studio to cloud VM | Ops | Month 3 | $200/mo |
| Procure E&O + D&O + cyber insurance | GP | Month 4 | $30K–$60K/yr premium |
| Documented BCP / DR with quarterly tabletop tests | Ops | Month 4 | internal time |
| First-year SOC 2 scoping and gap assessment | Consultant | Month 6 | $25K–$40K |

**Phase A one-time cost:** ~$200K–$350K. **Annual run rate:**
~$200K–$350K (fund admin, auditor, CCO, insurance, software,
cloud). Economic breakeven at 2% management fee: **~$15M committed
capital**.

### Phase B — $200M → $500M (institutional foundation)

| Step | Owner | Timeline | Cost |
|---|---|---|---|
| **Convert from ERA to SEC RIA** (triggered at $100M) | CCO + counsel | ~Month 6 | $10K–$25K |
| Full Form ADV Parts 1A, 2A, 2B with annual update | CCO | ongoing | $5K–$15K/yr |
| Written compliance manual + code of ethics + annual testing | CCO | Month 7 | included in CCO fee |
| Begin SOC 2 Type II observation period | Ops + auditor | Month 8–14 | $40K–$80K |
| Onboard a mid-tier prime (Clear Street or Wedbush) | GP | Month 9 | included |
| GIPS composite construction and verification | Performance consultant | Month 10 | $25K–$40K |
| Independent risk officer (fractional) | External | Month 11 | $50K–$100K/yr |
| Expand monitoring: Datadog/Grafana + PagerDuty | Ops | Month 12 | $30K/yr |
| First institutional ODD meeting — practice round | GP + CCO | Month 14 | internal time |

**Phase B incremental annual cost:** ~$150K–$250K on top of Phase A.
**Economic breakeven:** ~$50M committed capital at Phase B size.

### Phase C — $500M → $1B+ (tier-1 institutional)

| Step | Owner | Timeline | Cost |
|---|---|---|---|
| Engage tier-1 prime broker (GS / MS / JPM) | GP | Month 18 | included |
| In-house CCO hire (replaces outsourced) | HR + CCO | Month 20 | $200K–$300K/yr |
| In-house COO hire | HR | Month 22 | $250K–$400K/yr |
| Form PF Section 2 quarterly reporting (triggered at $1.5B) | CCO + reg-reporting vendor | Month 24 | $30K–$60K/yr |
| AIFMD marketing permissions for EU LPs (optional) | Counsel | Month 24 | $40K–$80K |
| 24/7 NOC vendor for infrastructure | Ops | Month 24 | $100K–$200K/yr |
| Hot-hot multi-region cloud failover | Ops | Month 24 | $50K–$100K/yr infra |
| Second annual SOC 2 Type II | Auditor | Month 26 | $40K–$60K |
| Tier-1 ODD reviews (multiple allocators) | GP + CCO + COO | Month 24+ | internal time dominant |

**Phase C incremental annual cost:** ~$600K–$1.2M on top of Phase B.
At $1B AUM and 1% management fee that is 6–12% of gross revenue —
comfortable.

---

## 8. Cost summary and breakeven

| Tier | One-time | Annual run rate | Breakeven AUM @ 1% mgmt fee | @ 2% mgmt fee |
|---|---|---|---|---|
| Phase A (ERA) | $200–350K | $200–350K | $30M | $15M |
| Phase B (SEC RIA + mid-prime) | $80–150K | $350–600K | $45M | $22M |
| Phase C (Tier-1 + in-house C-suite) | $100–200K | $1.0–1.8M | $120M | $60M |
| **Cumulative at $1B AUM** | **~$500K** | **~$1.9M** | — | — |

At $1B AUM and 1% management fee (plus 20% performance at target
Sharpe), total compliance + operations + infrastructure overhead is
approximately **20 bps of AUM**, which is standard for a
single-strategy institutional fund.

---

## 9. Critical milestones and sign-off gates

| Gate | Required before | Owner |
|---|---|---|
| First audited track record (1 year) | first LP allocation above family/friends | Auditor + CCO |
| Form ADV filed (SEC or state) | accepting any fee-paying client | Counsel + CCO |
| PPM and LPA executed | first capital call | Counsel |
| BCP tested within last 12 months | any ODD meeting | COO / Ops |
| SOC 2 Type II report available | $250M+ LP conversations | Ops + Auditor |
| Independent CCO in place | SEC registration | CCO or search firm |
| Two funded broker relationships | any live trading above $50M | GP |
| Insurance policies in force | fund launch | Insurance broker |

---

## 10. Immediate next steps (pre-Phase A)

1. **Engage a fund-formation attorney this quarter.** Everything
   downstream depends on legal entity structure decisions that are
   expensive to unwind. Budget $10K for a scoping conversation.
2. **Get background checks on all named GPs** (FINRA BrokerCheck,
   state regulator, criminal, credit) — takes 2 weeks, $500/person,
   and is a hard prerequisite for any institutional conversation.
3. **Move the paper-trading engine off the Mac Studio to a cloud
   VM** — the single-box topology is the number-one technology red
   flag for any ODD review. Can be done in a week for $200/month.
4. **Start the SOC 2 gap assessment** — not the full audit, just
   the scoping conversation with one of Schellman, A-LIGN, or
   Coalfire. Knowing the gap determines how long Phase A actually
   takes.
5. **Write the first draft of the operations manual** — valuation
   policy, trade allocation, error policy, cash management. One
   document, 20 pages. Allocators read this in the first week of
   ODD; having a professional version saves months later.

---

## 11. Honest caveats and limitations

- **This is a planning document, not legal advice.** Every
  regulatory threshold, filing requirement, and cost estimate in
  this document is drawn from general knowledge of US securities
  practice as of early 2026. Rules change, and individual facts
  matter. Engage qualified counsel before acting.
- **Cost ranges are industry medians, not quotes.** Actual vendor
  quotes vary by fund complexity, location, and negotiating
  leverage. Budget with ±30% sensitivity.
- **State-specific rules are omitted.** If the adviser or principals
  reside in California, New York, Texas, or Massachusetts there
  will be additional state blue-sky and adviser-registration
  quirks not covered here.
- **International LPs bring additional complexity.** AIFMD, MiFID II,
  FATCA, CRS — each layer adds filing requirements not covered in
  this document.
- **The SOC 2 recommendation is a practical floor, not a legal
  requirement.** It is the de-facto certification that unlocks
  tier-1 institutional relationships, but it is not mandated by
  the Advisers Act. A smaller fund with a single mid-tier prime
  may operate without SOC 2 indefinitely.

---

## 12. References and further reading

- Investment Advisers Act of 1940 — §203, §204, §206
- SEC Rule 204-2 (books and records)
- SEC Rule 206(4)-2 (custody rule)
- SEC Rule 206(4)-7 (compliance programs)
- SEC Form ADV General Instructions
- SEC Form PF General Instructions
- Investment Company Act of 1940 — §3(c)(1), §3(c)(7)
- Reg D 506(b), 506(c) (general solicitation)
- AIMA Illustrative Questionnaire for Due Diligence of Fund Managers
  (current edition)
- SBAI (Standards Board for Alternative Investments) Standards
- GIPS 2020 Standards
- NIST Cybersecurity Framework 2.0
- SEC Cybersecurity Risk Management rules (2024)
- CFTC / NFA rules — not applicable to this strategy but review if
  the strategy expands into futures or swaps

---

*Last updated 2026-04-09. This document should be refreshed
annually or whenever any of the cited thresholds change.*
