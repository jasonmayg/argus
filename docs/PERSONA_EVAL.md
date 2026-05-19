# Persona Weight Evaluation — Operations Guide

> Engineer-facing guide for `scripts/persona_eval.py`. The design rationale
> lives in `/path/to/Downloads/Argus_Phase2_7_PersonaEval_Design.md`; this
> document covers *how to run it* and *how to read the output*.

## 1. What it does

Evaluates whether the persona `risk_weights` in `personas/defaults.yaml`
actually produce meaningfully-different alert prioritizations across
the four personas. Combines two methods:

| Method | Question | Output |
|---|---|---|
| **A. Differentiation** | Are the 4 personas' top-20 alert lists actually different? | Jaccard matrix + outlier flags |
| **B. LLM judge** | Does each persona's top-20 truly match its job-to-be-done? | Per-category weight adjustment suggestions, with confidence |

The script writes a markdown report + JSON dump under `data/persona_eval/`,
and a row to the `persona_eval_runs` SQLite table for trend analysis.

**Nothing in `personas/defaults.yaml` is changed automatically.** All
adjustments are suggestions — landing them requires a PR + human review.

## 2. Running it

```bash
# Default: 30-day window, all 4 personas, both methods
python scripts/persona_eval.py

# Wider window when the Guardian snapshot is sparse
python scripts/persona_eval.py --days 90

# Specific calendar range
python scripts/persona_eval.py --since 2026-04-01 --until 2026-04-30

# Only one persona
python scripts/persona_eval.py --persona fx_manager

# Method A only (no LLM cost, fast)
python scripts/persona_eval.py --no-llm

# Print report to stdout, don't write files or DB
python scripts/persona_eval.py --dry-run
```

Exit codes:
| Code | Meaning |
|---|---|
| 0 | Success — report written |
| 1 | Insufficient data (`< 50` alerts in window) |
| 2 | Method B failed for every persona |
| 3 | Unrecoverable error (DB / import / etc.) |

## 3. Reading the report

### Method A — Jaccard matrix

```
                cash_manager  fx_manager  risk_manager  treasury_ops
cash_manager       1.00         0.62        0.58          0.71
fx_manager         0.62         1.00        0.51          0.43
risk_manager       0.58         0.51        1.00          0.49
treasury_ops       0.71         0.43        0.49          1.00
```

Healthy zone is **0.30 – 0.65** off-diagonal. Interpretation:

| Jaccard | Verdict |
|---|---|
| `>= 0.80` | Weights too similar — two personas see ~same alerts. Flag fires automatically. |
| `0.65 – 0.80` | Reasonable overlap; common signal (e.g. macro liquidity events should hit multiple personas) |
| `0.30 – 0.65` | **Healthy** — clear differentiation but shared base risk awareness |
| `0.20 – 0.30` | Worth a look |
| `< 0.20` | Suspiciously disjoint — one persona may be over-extreme. Flag fires. |

The thresholds (`JACCARD_HIGH=0.80`, `JACCARD_LOW=0.20`) are tuned in
`core/persona_eval/differentiation.py`. After 3-5 production runs, revisit
based on observed distribution.

### Method B — LLM judge

Per persona, the report shows:

* **Consensus adjustments** — both LLM runs agreed on direction.
  Confidence is the *minimum* of the two runs' confidence labels.
* **Disagreements** — runs differed on direction, OR only one run
  proposed an adjustment. Goes into the "Manual review queue".

The `Auto-apply candidates` section in the Recommendations block at the
end of the report lists **high-confidence consensus** suggestions only.
Even these still require a PR — the section name promises "candidates",
not "auto-applied".

### Why "auto-apply" is still gated on human review

Persona weight changes are business strategy: they shift how every
alert prioritization and Phase 3 agent reasoning works. The LLM judge
has its own bias (anchoring on category names, training-data priors
about what a "treasury manager" cares about). Two-run consensus is a
necessary check, not a sufficient one.

## 4. When to run it

| Trigger | Frequency |
|---|---|
| Before adding a new persona | **Required** — see §5 |
| After changing `personas/defaults.yaml` | Within 1 week |
| Routine health check | Weekly cron |
| Before kicking off Phase 3 IntelligenceAgent rollout | Once |

A weekly cron is enough. The script costs ~$0.40/run if both methods run,
and `< 1 minute` of wall time.

## 5. SOP: adding a new persona

1. Draft the new persona block in `personas/defaults.yaml` (weights based
   on best engineering judgment of the role's priorities).
2. Run `python scripts/persona_eval.py --persona <new_persona>` — generates
   a report just for that persona.
3. Run `python scripts/persona_eval.py` (full eval) — captures how the
   new persona's view *differs* from the existing four. The Jaccard
   pairs involving the new persona are the signal.
4. If any pair-with-existing-persona has `jaccard >= 0.80`, the new
   persona is redundant; reshape weights and goto 2.
5. Commit the persona + the report path in PR description.

## 6. Reading historical trends

```sql
SELECT
    DATE(run_at)         AS day,
    avg_jaccard,
    max_jaccard,
    alerts_count,
    llm_cost_usd
FROM persona_eval_runs
ORDER BY run_at DESC
LIMIT 10;
```

Watch for:

* `avg_jaccard` drifting **up** over time → personas converging (probably
  because all weights are getting bumped uniformly via tuning churn).
* `avg_jaccard` drifting **down** → personas diverging. Could be healthy
  differentiation, or could be that one persona has been over-tuned and
  is now an outlier.
* `alerts_count` chronically `< 50` → Guardian snapshot is sparse;
  consider increasing `--days` default, or check whether Guardian POC
  ingestion is healthy.

## 7. Known caveats

* **Method A is sensitive to top-N**. With N=20 and only 11 alerts in
  the window (current Guardian snapshot state), every persona sees the
  same 11 → Jaccard=1.0 trivially. The `MIN_ALERTS_FOR_EVAL=50` floor
  prevents reporting in this degenerate case.
* **Method B's LLM judge has training-data bias.** It may anchor on
  category names (`"political_risk" sounds important"`) rather than
  actual operational impact. Use the consensus mechanism + manual
  review; never auto-apply.
* **The category-mapping table in `_compute_effective_score`** is the
  ground truth for how alerts get scored. If a new alert category type
  appears in the Guardian feed and isn't in `CAT_MAP`, it defaults to
  weight=1.0 silently. Worth periodically scanning for un-mapped categories.

## 8. Adding categories / new metrics

If you extend `risk_weights` in `personas/defaults.yaml` with a new
category, no code change is needed — the eval picks it up automatically.
The LLM judge prompt also adapts: it sees the current weights block
inline, so a new category appears in the LLM's view too.

If you want to add a third evaluation method (e.g. user-behavior implicit
feedback per §11 of the design doc), the pattern is:

* New file under `core/persona_eval/`
* New section in `report.py::render_markdown`
* Optional new column in `persona_eval_runs` table
* Don't change `data_loader.py`'s public API — it's the substrate.
