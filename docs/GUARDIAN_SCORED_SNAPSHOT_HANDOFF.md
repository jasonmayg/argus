# Guardian Scored Snapshot — Handoff to AI_Treasury_Project

**Status:** draft, awaiting AI_Treasury_Project confirmation
**Owner (Argus side):** TBD
**Created:** 2026-05-19

## Background

Argus's dashboard, `/api/actions`, and `persona_eval` all consume "Guardian
alerts" — country-tagged, risk-categorized, scored items. The data flow
moved to GCS earlier in 2026: AI_Treasury_Project writes per-day raw CSVs
under `gs://your-gcs-bucket/raw/dt=YYYY-MM-DD/`. Today's partition
holds 65 CSVs covering 13 countries of economic series + 7 central banks of
press releases.

What the migration **didn't** ship is the bridge from raw CSVs to scored,
classified alerts:

* `core/guardian_loader._load_guardian_snapshot_from_gcs()` reads the raw
  CSVs and returns 2093 alerts — but **every row has `score: 0` and
  `category_label: "Unclassified"`** because the loader is doing flat
  passthrough, not running risk-keyword classification.
* `generate_actions.py` and `core/persona_eval/data_loader.py` still
  read from `guardian_poc/data/alerts_latest.json`, which is the old
  Guardian POC's output (`python guardian_poc/src/guardian.py` runs
  scoring locally). That file hasn't been refreshed since 2026-05-14;
  nobody is running the POC anymore.

Net effect: persona_eval Jaccard = 1.00 across all 4 personas. Persona
weights multiply against `category_weight` from the alert. With every
alert at `Unclassified` and `score=0`, persona weights have nothing to
discriminate against.

## What we're asking AI_Treasury_Project for

Write a daily **scored snapshot JSON** to GCS alongside the raw CSVs.
Argus reads it and the missing risk classification + scoring lands
for free.

### Object location

```
gs://your-gcs-bucket/alerts_latest.json
```

This is the first path `core/guardian_loader._load_guardian_snapshot_from_gcs()`
checks (see `latest_candidates` at `core/guardian_loader.py:626`). Just
dropping the file in that location wires every Argus consumer in.

Optional: also write a versioned copy at
`gs://your-gcs-bucket/snapshots/alerts_YYYYMMDD_HHMMSS.json`
following the regex `alerts_\d{8}_\d{6}\.json$` — the loader picks the
lexicographically-largest matching blob as a fallback, which is useful
for history and rollback.

### Cadence

Daily, after the raw CSV partition for `dt=YYYY-MM-DD` finishes writing.
Failure mode of "no fresh file today" is acceptable — the loader picks
yesterday's snapshot. Failure mode of "corrupt JSON" is not — Argus then
falls back to `_read_alerts_from_gcs_raw_csvs` which produces the
unclassified mess described above.

### JSON shape

Required top-level keys (mirrors what
`guardian_poc/data/alerts_latest.json` currently has):

```json
{
  "generated_at_utc":   "2026-05-19T18:00:00+00:00",
  "generated_at_local": "2026-05-19T11:00:00-07:00",
  "timezone":           "America/Los_Angeles",
  "persona":            "treasury_ops",
  "threshold":          40,
  "data_mode":          "gcs_scored",
  "event_count":        191,
  "alert_count":        191,
  "alerts": [ <alert>, <alert>, ... ]
}
```

Each `<alert>` must populate **at minimum**:

| Field                | Type    | Notes                                                                 |
|----------------------|---------|-----------------------------------------------------------------------|
| `alert_id`           | string  | Stable id. Use the source URL, or `{source}#{hash(title+date)}`.      |
| `title`              | string  | News headline / event summary.                                        |
| `url`                | string  | Source URL when available.                                            |
| `source`             | string  | Adapter / source key (e.g. `ecb`, `boe`, `fed`, `gdelt`, `usgs`).     |
| `country`            | string  | Detected country (`"USA"`, `"China"`, `"UK"`, ..., `"Unknown"`).      |
| `category_key`       | string  | One of: `market_risk`, `liquidity_risk`, `credit_risk`, `operational_risk`, `geopolitical_risk`, `disaster_risk`, `regulatory_risk`, `cyber_risk`. |
| `category_label`     | string  | Human label matching `category_key` (e.g. `"Market Risk"`).           |
| `score`              | float   | 0–100, the effective alert score after severity × recency × country × persona weights. |
| `severity`           | float   | 0–1, source-level severity floor (see scoring section below).         |
| `recency`            | float   | 0–1, exponential decay by `published_at`.                             |
| `published_at`       | string  | ISO 8601, source publish time.                                        |

Optional but recommended:
* `recommendation` / `recommendation_source` — short action hint (rule-based or LLM).
* `rationale` — 1-2 sentence "why this matters".

### Reference scoring implementation

The canonical scoring algorithm lives in `guardian_poc/src/guardian.py`:

* `GuardianEngine.score_events()` (line 797) — top-level scoring pass
* `_detect_countries()` / `_detect_categories()` — keyword detection against
  `guardian_poc/config/countries.yaml` and `risk_keywords.yaml`
* `_base_severity()` (now lowered to `0.3` for `ecb` after PR #47) —
  per-source severity floor
* `_recency_score()` — recency decay
* Score formula: `score = 100 * severity * recency * country_weight * persona_weight`
  (see `guardian.py:828`)

Three good options for AI_Treasury_Project:

1. **Port the scoring code into the upstream pipeline** — copy
   `score_events()` + helpers, run after CSV generation, write the JSON.
   Lowest coupling.
2. **Vendor `guardian_poc/src/guardian.py` as a library** —
   `from guardian_poc.src.guardian import GuardianEngine` and call
   `engine.score_events(events)` in-process.
3. **Subprocess `python guardian_poc/src/guardian.py`** — produces a
   snapshot file we then upload to GCS.

Whichever option, **the result must include PR #47's noise filters**:
* `_base_severity("ecb")` is 0.3 (down from 0.6)
* `_adapter_noaa_nws` filters to `("Severe", "Extreme")` by default

### Persona

The Argus team uses `treasury_ops` as the snapshot's reference persona
(it's the broadest weight profile). `persona` field in the JSON should
be `"treasury_ops"` unless we coordinate a different convention.

### Source whitelist

`core/guardian_loader.GUARDIAN_SOURCE_WHITELIST` constrains what
sources actually surface in the dashboard. AI_Treasury_Project can
emit more sources — the whitelist filters at read time. Current
whitelist:

```
gdelt, reliefweb, world_bank, noaa_nws, nasa_eonet, usgs, fred,
us_treasury_auction, bank_of_canada, ofac_sls, ecb
```

Anything outside this list is silently dropped downstream. If
AI_Treasury_Project starts producing `boe` / `boj` / `fed` / `fca`
/ `treasury` alerts (the new sources in today's GCS partition), we'll
extend the whitelist on the Argus side once the snapshot lands.

## What Argus will do once the snapshot lands

No code change is required for `core/guardian_loader.py` — it already
prefers `alerts_latest.json` over the raw CSV fallback.

Open code changes on the Argus side:

1. `generate_actions.py:41` — `GUARDIAN_PATH = Path("guardian_poc/data/alerts_latest.json")`
   needs to also go through `core/guardian_loader` (or a thin wrapper) so
   `/api/actions` sees the GCS snapshot, not the stale local POC file.
2. `core/persona_eval/data_loader.py:36` — same migration:
   `GUARDIAN_SNAPSHOTS_DIR = Path("guardian_poc/data/snapshots")` →
   read via guardian_loader, falling back to the local dir only when
   GCS is unreachable.

Tracking these as a follow-up Argus PR once the GCS snapshot is producing.

## Validation plan

Once AI_Treasury_Project ships the first snapshot:

1. Argus team pulls the snapshot, validates schema (script tbd, mirrors
   the field table above).
2. Re-runs `python scripts/persona_eval.py --days 30 --no-llm --dry-run`
   on the new snapshot path.
3. Acceptance criterion: persona pairwise Jaccard < 1.00, ideally
   < 0.8 for at least one pair. (Current state is 1.00 across all
   pairs.)
4. Spot-check: 10 random alerts have non-empty `category_key`, non-zero
   `score`, and a recognizable `country`.

## Open questions for AI_Treasury_Project

1. Which option above (port / vendor / subprocess) does the team prefer?
2. Cadence: tied to the raw CSV write, or a separate schedule?
3. Error handling: should a partial scoring run still produce a snapshot
   (with whatever it scored) or fail closed?
4. Are there sources in raw CSVs that don't map cleanly to a Guardian
   category? (e.g. `news_google_macro_radar.csv` is currently empty —
   42 bytes — so it may not matter, but flagging for review.)

---

## Appendix — what the GCS bucket actually contains today

For reference when reviewing this spec.

```
gs://your-gcs-bucket/
├── raw/
│   ├── dt=2026-02-10/   (oldest partition)
│   ├── ...
│   └── dt=2026-05-19/   (today, 65 CSVs)
│       ├── US_*.csv, eu_*.csv, jp_*.csv, ...
│       ├── news_fed_press.csv (8788 bytes)
│       ├── news_boe_press.csv (21974 bytes)
│       ├── news_boj_press.csv (11169 bytes)
│       ├── news_ecb_press.csv (4629 bytes)
│       ├── news_fca_press.csv (51280 bytes)
│       ├── news_treasury_press.csv (2368 bytes)
│       ├── news_boc_press.csv (3992 bytes)
│       ├── gdelt_conflict_light.csv (12062 bytes)
│       └── usgs_earthquakes_global.csv (794 bytes)
└── summary/  (legacy, only one entry from 2026-02-11)
```

99 daily partitions, last gap-free.

Today's loaded alerts via `_load_guardian_snapshot_from_gcs()`:
* 2093 total
* 1902 `source=gcs_raw` with empty title (econ series CSV rows)
* 191 named-source items: BoE 50, BoJ 47, FCA 20, Fed 20, Treasury 17, ECB 15, USGS 12, BoC 10
* All `category_label="Unclassified"`, all `score=0`
