# The AI Treasury Project: An Upstream Data-Ingestion Layer for Treasury Risk Intelligence

### Companion report to the Argus Risk Intelligence Platform

*Prepared as a worked example for a forthcoming volume on digital finance.*

---

> **Note to readers / co-authors.** This is the **upstream** report. It documents the data-collection and storage layer that feeds the downstream **Argus** application (documented separately). Argus consumes this layer's output read-only; the two systems are deliberately decoupled and deploy independently. This report is intentionally concise — its role is to establish *what data exists, where it comes from, and how it reaches Argus* — and leaves room for the data co-author to expand the per-source and operational detail.

---

## Abstract

Any risk-intelligence system is only as good as the data beneath it. The **AI Treasury Project** is the ingestion layer for the Argus platform: a configuration-driven pipeline that fetches heterogeneous public financial, macroeconomic, geopolitical, and physical-risk data from **82 configured sources**, normalises each to a tabular format, and writes immutable, date-partitioned snapshots to cloud storage. This report describes the design philosophy (a single declarative source registry driving pluggable fetchers), the source taxonomy, the storage contract that links it to Argus, and the deployment model. The separation of ingestion (here) from intelligence (Argus) is the architectural decision that makes both halves independently evolvable and the downstream analysis reproducible.

---

## 1. Purpose and Scope

### 1.1 Role in the two-system architecture

Treasury risk intelligence requires two distinct capabilities: **collecting** a broad, reliable, and current view of the world, and **interpreting** that view into prioritised, role-specific risk. Conflating them in one codebase couples two concerns with very different rates of change — data sources are added and repaired frequently, whereas the risk model and AI layer evolve on a slower, more deliberate cadence. The AI Treasury Project owns the first capability; Argus owns the second.

```
  AI Treasury Project (this report)            Argus (companion report)
  ─────────────────────────────────            ────────────────────────
  82 sources → fetchers → normalise            read GCS → classify →
       │                                        score (rules/LLM/ML) →
       ▼                                        Probability × Impact matrix →
  Google Cloud Storage                          persona personalisation →
  raw/dt=YYYY-MM-DD/<source>.csv  ───read──▶    decision support
       (immutable daily snapshots)
```

### 1.2 The storage contract

The interface between the two systems is **not an API but a storage convention**: the upstream layer writes CSV files to a Google Cloud Storage bucket under a date-partitioned prefix, `raw/dt=YYYY-MM-DD/<source_id>.csv`. This contract has three deliberate properties:

- **Immutability and reproducibility.** Each day's raw data is a frozen snapshot. Any downstream analysis can be re-run against a historical partition and will see exactly the inputs of that day.
- **Loose coupling.** The upstream layer can add sources, change fetcher internals, or redeploy without any code change in Argus, provided the file convention holds.
- **Auditability.** The raw, un-interpreted data is preserved exactly as fetched, so that any downstream score can be traced back to its source rows.

### 1.3 Scope of this report

This report covers source coverage, the ingestion architecture, the storage layout, and deployment. It does **not** cover risk scoring, the Probability × Impact matrix, personas, or the AI layer — all of which belong to Argus and are documented in the companion report.

---

## 2. Source Coverage

The pipeline is driven by a single declarative registry, `src/config/sources.yaml`, which currently defines **82 sources**. Each entry specifies an `id`, a `class` (e.g. *economic*, *political*, *disaster*, *news*), a `country`, a `type` (which selects the fetcher), an `enabled` flag, and a `params` block. The registry is the single source of truth: adding a data source is, in the common case, a YAML edit rather than a code change.

### 2.1 Source taxonomy

The 82 sources break down by fetcher type as follows:

| Fetcher type | Count | What it provides |
|:-------------|------:|:-----------------|
| `fred_series` | 56 | FRED economic time series — policy rates, reference rates (SOFR, EFFR), sovereign yields, FX spot, CPI, monetary aggregates, across many economies |
| `rss` | 10 | News RSS feeds |
| `banxico_series` | 4 | Banco de México series |
| `boc_valet_series` | 3 | Bank of Canada (Valet API) series |
| `gdelt_doc` | 1 | GDELT geopolitical event/news documents |
| `acled_events` | 1 | ACLED armed-conflict / unrest events |
| `usgs_earthquakes` | 1 | USGS earthquake feed |
| `reliefweb_reports` | 1 | ReliefWeb humanitarian/disaster reports |
| `imf_compact` | 1 | IMF macro data (SDMX compact) |
| `bis_sdmx_compact` / `bis_dataflows` | 2 | BIS macro data and dataflow discovery |
| `treasury_auctions` | 1 | US Treasury auction results |
| `treasury_press_releases_html` | 1 | US Treasury press releases |

### 2.2 Coverage philosophy

The source mix is intentionally **cross-domain**, because treasury risk is cross-domain. Economic series (the bulk, via FRED) capture the monetary and market environment; news and GDELT/ACLED capture geopolitical and event risk; USGS and ReliefWeb capture physical-disaster risk that can disrupt counterparties and supply chains; IMF/BIS provide cross-border macro context. This breadth is what lets the downstream system populate categories as diverse as *liquidity*, *political*, and *disaster* risk on the same matrix.

### 2.3 The FRED-mirror strategy

A majority of economic sources are FRED series. FRED is used as a **single, reliable mirror** for indicators that originate from many different central banks and statistical agencies (ECB, BoE, BoJ, RBA, BoK, RBI, and others). The trade-off is explicit and documented: FRED mirroring buys uniform access and reliability at the cost of some update latency relative to each authority's native feed, and native fetchers (e.g. Banxico, Bank of Canada) are used where they add value. A known limitation is that a few indicators (e.g. certain 10-year yields for some emerging markets) are not available on FRED and await native fetchers.

### 2.4 Coverage gap **[CO-AUTHOR — expand]**

A recognised issue is the gap between *configured* sources and *consistently-producing* sources: not every enabled source emits usable data on every run, and that gap can flatten downstream risk differentiation. Diagnosing and closing this gap (per-source reliability, resilience to upstream outages) is an ongoing data-quality priority and is a natural place for the data co-author to expand with concrete numbers.

---

## 3. Ingestion Architecture

### 3.1 Configuration-driven fetching

The pipeline follows a **registry + pluggable fetchers** pattern. The orchestrator (`daily_fetch_v2.py`) loads `sources.yaml`, and for each enabled source dispatches on its `type` to the appropriate fetcher. Fetchers are organised by domain under `src/fetchers/`:

| Module | Responsibility |
|:-------|:---------------|
| `economic_us.py` | US Treasury auctions, FRED series |
| `economic_ca.py` | Bank of Canada (Valet) series |
| `economic_mx.py` | Banco de México series |
| `economic_imf.py` / `economic_bis.py` | IMF / BIS macro data and dataflow listing |
| `political_conflict.py` | GDELT documents (and conflict events) |
| `physical_disaster.py` | ReliefWeb reports, USGS earthquakes |
| `news_rss.py` / `news_treasury.py` | RSS feeds, US Treasury press releases |

Each fetcher returns a normalised tabular result (a dataframe), which the orchestrator uploads as CSV via the storage helper (`src/storage/gcs.py`). This design means the *control flow is uniform* — every source, regardless of its native API, follows fetch → normalise → upload — while the *source-specific quirks* are isolated inside one fetcher.

### 3.2 Normalisation

Heterogeneous upstream formats (REST JSON, SDMX, HTML, RSS, CSV) are normalised to a common tabular shape before storage. This is what allows the downstream system to treat all sources uniformly: a FRED yield series and a GDELT event feed arrive in Argus as comparable date-indexed CSVs.

### 3.3 Backfill and date ranges

The orchestrator accepts a date range, supporting both the routine single-day daily run and historical backfill over an interval. Combined with the immutable date-partitioned storage layout, this makes it straightforward to reconstruct or repair any historical partition.

### 3.4 Resilience **[CO-AUTHOR — expand]**

Robustness to individual source failures (timeouts, schema drift, upstream outages) is essential, since one flaky source must not abort the whole daily run. The orchestration includes per-source isolation and timeouts; the resilience behaviour and its history (including a notable fix that restored sources which had silently dropped out) are a good place for the data co-author to expand.

---

## 4. Storage Layout

Output is written to Google Cloud Storage under a defaults block in the registry (bucket and `raw_prefix`), producing the partition layout:

```
gs://<bucket>/
└── raw/
    ├── dt=2026-06-18/
    │   ├── us_sofr.csv
    │   ├── US_10y_treasury.csv
    │   ├── eu_policy_rate.csv
    │   ├── gdelt_doc.csv
    │   ├── usgs_earthquakes.csv
    │   └── … (one CSV per producing source)
    ├── dt=2026-06-19/
    └── dt=2026-06-20/
```

The `dt=` partition convention is a standard data-lake idiom: it makes "give me everything as of day *D*" a simple prefix listing, supports incremental processing, and keeps history immutable. Argus reads these partitions directly and is responsible for all interpretation downstream.

---

## 5. Deployment and Scheduling

The ingestion layer is containerised (a `Dockerfile` is provided) for deployment on Google Cloud, where it runs on a schedule to produce the daily partition. Because the contract with Argus is the storage layout rather than a live service, the ingestion job can run, fail, retry, and redeploy entirely independently of the downstream application — the downstream system simply reads whatever partitions exist. Deployment specifics (Cloud Run / scheduler configuration, service-account and bucket permissions, and the GCP coordination required for changes) are owned by the data co-author and are summarised here only as architecture.

---

## 6. Relationship to Argus and the Digital-Finance Theme

For a digital-finance audience, the upstream layer illustrates two transferable points. First, **the boundary between data and intelligence should be a stable, immutable contract** — here, date-partitioned object storage — so that the fast-changing data world and the carefully-governed analytics world can evolve at their own paces without breaking each other. Second, **breadth and provenance matter as much as algorithms**: the downstream Probability × Impact matrix can only place an event on the *disaster* or *political* axis if a USGS or GDELT feed put that event in the data lake in the first place. The intelligence is downstream, but its ceiling is set upstream.

---

## 7. Limitations and Future Work

- **Source reliability.** Closing the gap between configured and consistently-producing sources (§2.4) is the highest-value upstream improvement, since it directly affects downstream differentiation.
- **Native vs. mirrored feeds.** Replacing latency-prone FRED mirrors with native central-bank APIs where freshness matters, and adding native fetchers for indicators FRED does not carry.
- **Schema-drift handling.** Hardening fetchers against upstream format changes and surfacing fetch-health metrics.
- **Coverage expansion.** Additional regions and asset classes as the downstream personas demand them.

---

## 8. Conclusion

The AI Treasury Project is the unglamorous but foundational half of the Argus system: a configuration-driven, multi-domain ingestion pipeline that turns 82 heterogeneous public sources into clean, immutable, date-partitioned snapshots in cloud storage. By exposing its output through a stable storage contract rather than a coupled service, it lets the downstream risk-intelligence platform read reproducible inputs and lets both systems evolve independently. Its breadth across economic, geopolitical, and physical-risk domains is precisely what gives the downstream Probability × Impact matrix something meaningful to score.

---

## Appendix — Source Registry Schema (illustrative)

```yaml
defaults:
  bucket: <gcs-bucket>
  raw_prefix: raw
  timeout_sec: 30

sources:
  - id: us_sofr            # unique source id → output filename
    class: economic        # domain: economic | political | disaster | news
    country: US
    type: fred_series      # selects the fetcher
    enabled: true
    params:
      series_id: SOFR
      limit: 30
```

*Adding a source is, in the common case, a single YAML entry — no code change — which is the property that lets the source registry grow quickly while the fetcher code stays small and stable.*

---

*End of AI Treasury Project report. Sections marked **[CO-AUTHOR — expand]** (§2.4, §3.4) and the deployment specifics in §5 are intended for expansion by the data-collection co-author.*
