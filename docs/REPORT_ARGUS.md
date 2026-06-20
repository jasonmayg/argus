# Argus: A Persona-Driven Risk-Intelligence Platform for Treasury Operations

### A Case Study in Applied Digital Finance — The Probability × Impact Risk Matrix Operationalised with Large Language Models

**Project report — Argus Risk Intelligence Platform**

*Prepared as a worked example for a forthcoming volume on digital finance (blockchain, artificial intelligence, and their application to economics and finance).*

---

> **Note to readers / co-authors.** This document is the report for the **downstream Argus application** — the system that turns raw market and event data into persona-specific, scored treasury risk. The **upstream data-collection layer** (the *AI Treasury Project*) is described in a companion report. Two sections of this report — **§9 Data Collection** and **§10 Model Development** — are intentionally written as concise summaries with hooks for the teammates who own those areas to expand. They are marked **[CO-AUTHOR SECTION]**.

---

## Abstract

Treasury operations sit at the intersection of macroeconomics, market microstructure, and operational risk. A treasurer must decide, continuously and under time pressure, which of hundreds of daily signals — a central-bank rate decision, a sovereign-yield move, a regulatory filing, an earthquake near a supplier hub — actually threatens the firm's liquidity, funding, and portfolio. Conventional dashboards report *data*; they do not report *risk*, and they do not adapt to *who is asking*.

Argus is a risk-intelligence platform that addresses this gap. Its conceptual core is a **5 × 5 Probability × Impact risk matrix**: every monitored event is placed on two axes — its likelihood of occurring (or persisting) over a forward window, and the severity of its consequences for treasury operations — and the matrix cell determines a Low / Medium / High risk level. The novelty is not the matrix itself, which is a classical risk-management instrument, but its **operationalisation**: Argus assigns the two coordinates automatically, fusing a deterministic keyword/threshold layer, a large-language-model (LLM) scoring and reasoning layer, and a supervised machine-learning layer, and it then **re-weights** every event through a *persona* — a parameterised model of a specific treasury role and its regional exposures. The same event is therefore "High" for one user and "Low" for another, by design.

This report describes the theoretical framework, the system architecture, the event-scoring methodology, the persona personalisation model, and the AI layer (LLM routing, retrieval-augmented generation, and a set of constrained autonomous agents). It situates the work within the digital-finance literature, reports on evaluation, and discusses the economic interpretation of an automated, personalised risk-matrix engine. The system is implemented in Python/Flask and is in production use.

**Keywords:** digital finance, treasury risk management, probability–impact matrix, large language models, retrieval-augmented generation, risk personalisation.

---

## 1. Introduction

### 1.1 The treasury risk problem

The corporate (or institutional) treasury function is responsible for liquidity, funding, cash positioning, foreign-exchange (FX) exposure, counterparty credit, and the operational continuity of payment and settlement. Each of these is sensitive to a wide and heterogeneous information environment: monetary-policy rates, money-market reference rates (SOFR, EFFR, SONIA), sovereign yield curves, FX spot rates, inflation prints, regulatory changes, geopolitical events, and even physical disasters that can disrupt counterparties or supply chains.

The defining difficulty is not the *absence* of data — high-quality public data is abundant — but the **cognitive cost of converting data into a prioritised risk picture**. A treasurer confronted with a CPI release, a 10-year yield move, a central-bank communication, and a sanctions headline on the same morning must answer three questions for each: *How likely is this to matter? How badly would it hurt us? And does it matter to me, given my mandate and my geographic footprint?* Doing this manually, consistently, and at the cadence the market demands is infeasible.

### 1.2 The digital-finance context

Digital finance — the application of computational, data-intensive, and increasingly AI-driven methods to financial problems — offers a path. Two specific capabilities are now mature enough to deploy in production. First, **large language models** can read unstructured text (news, filings, central-bank statements) and emit structured judgments with human-readable justification, a task that previously required analysts. Second, **vector retrieval** lets a system ground those judgments in its own historical record rather than in the model's static training data. Argus is an attempt to assemble these capabilities into a disciplined, auditable risk engine rather than a chatbot bolted onto a dashboard.

Crucially, Argus is *not* "an LLM that answers questions about markets." The LLM is one scoring component inside a structured pipeline whose backbone is an explicit, inspectable risk model — the Probability × Impact matrix. This design choice reflects a thesis central to this report: in finance, **the value of AI is maximised when it is embedded in, and disciplined by, an economically meaningful framework**, not when it replaces that framework.

### 1.3 Contributions

This report makes the following contributions, framed as a case study suitable for a digital-finance curriculum:

1. It presents an **operationalisation of the classical Probability × Impact risk matrix** in which both coordinates are assigned automatically to live financial events (§3, §5).
2. It describes a **layered scoring methodology** that fuses deterministic rules, LLM judgment, and a supervised ML model, with graceful degradation between layers (§5).
3. It introduces a **persona model** that personalises risk to a treasury role and its regional exposures, turning a single objective signal into role-specific decision support (§6).
4. It documents a **constrained AI architecture** — an LLM router, retrieval-augmented generation over the system's own corpus, and bounded autonomous agents with explicit cost guards (§7).
5. It reflects on the **economic interpretation and limitations** of automating risk-matrix assignment (§12, §13).

### 1.4 Scope and division of labour

This report covers the Argus downstream application. The upstream *AI Treasury Project* — which fetches, normalises, and stores the raw data Argus consumes — is documented separately; §9 summarises it only insofar as it is needed to understand Argus's inputs. The supervised ML scoring model is summarised in §10 and owned, in depth, by a co-author.

---

## 2. Background and Related Work

### 2.1 The risk matrix in risk management

The two-dimensional risk matrix — plotting likelihood against consequence — is one of the most widely used instruments in enterprise and operational risk management. It appears, in 3 × 3, 4 × 4, and 5 × 5 forms, across safety engineering, project management, and financial-risk frameworks, and is embedded in standards such as ISO 31000 (risk management) and COSO ERM. Its appeal is pedagogical and communicative: it compresses a multidimensional judgment into a single coloured cell that a non-specialist can act on. Its well-documented weaknesses — coarse discretisation, sensitivity to axis definitions, and the risk of treating ordinal cells as if they were cardinal — are precisely the issues that an automated, data-grounded implementation must confront, and we return to them in §13.

### 2.2 Treasury and market-risk monitoring

Classical treasury risk management distinguishes market risk, credit/counterparty risk, liquidity risk, and operational risk, often supplemented by regulatory, political, and reputational risk. Quantitative measures (Value-at-Risk, liquidity coverage ratios, stress tests) address the *measurement* of exposure on positions the firm already holds. Argus addresses a complementary, upstream problem: **early detection and prioritisation of exogenous events** that may later move those measures. It is a monitoring and triage system, not a pricing or capital-calculation engine.

### 2.3 LLMs and retrieval-augmented generation in finance

Recent work applies LLMs to financial text classification, sentiment, and summarisation. The principal failure modes are well known: hallucination (confident but unsupported claims), staleness (the model's knowledge ends at its training cut-off), and non-determinism (the same prompt yielding different scores). **Retrieval-augmented generation (RAG)** mitigates staleness and grounding by retrieving relevant documents from a trusted corpus and conditioning the model on them. Argus uses RAG over its *own* historical news, alerts, regulatory changes, and prior recommendations, so that its language outputs cite the system's record rather than the model's memory (§7.2). The non-determinism and hallucination risks are addressed structurally: the LLM never has the last word on the risk level — it proposes coordinates that a deterministic matrix then resolves.

### 2.4 Positioning

Argus's distinguishing design decisions, relative to a generic "AI market assistant," are: (i) an **explicit, inspectable risk model** at the core; (ii) **personalisation** as a first-class concept rather than a prompt afterthought; and (iii) **defence-in-depth scoring** so that no single component (and in particular no single LLM call) is a single point of failure.

---

## 3. The Probability × Impact Matrix: Theoretical Framework

This section presents the conceptual core of the system. The framework is the organising theory of the platform; everything in §4–§8 exists to populate it correctly and to act on its output.

### 3.1 The two axes

Argus places every monitored event on two ordinal axes, each discretised into five levels.

**Probability (likelihood) axis** — the likelihood that the event occurs, or that an already-observed condition persists/worsens, over a forward window (operationally, the next ~90 days):

| Level | Label | Interpretation |
|------:|:------|:---------------|
| 1 | Unlikely | Remote; no current indicators |
| 2 | Seldom | Possible but not indicated by current data |
| 3 | Occasional | Plausible; some supporting signal |
| 4 | Likely | Probable; multiple converging signals |
| 5 | Frequent | Near-certain or already unfolding |

**Impact (consequence) axis** — the severity of the event's consequences for treasury operations, liquidity, and the portfolio:

| Level | Label | Interpretation |
|------:|:------|:---------------|
| 1 | Negligible | No material treasury consequence |
| 2 | Minor | Manageable within normal operations |
| 3 | Moderate | Requires attention; some repositioning |
| 4 | Hazardous | Material threat to liquidity/funding/portfolio |
| 5 | Catastrophic | Existential or systemic consequence |

The choice of a **forward window** for the probability axis is deliberate and economically meaningful: treasury decisions are about *positioning ahead of* events, so the relevant quantity is not whether something happened but whether it is likely to bind over the planning horizon.

### 3.2 The matrix and its resolution rule

The two axes form a 5 × 5 grid. Each cell is assigned a final **risk level** — Low, Medium, or High — according to the resolution matrix implemented in `risk_matrix.py`:

```
Impact →        1·Negligible  2·Minor   3·Moderate  4·Hazardous  5·Catastrophic
Probability ↓
5 · Frequent      Medium        High      High        High         High
4 · Likely        Medium        Medium    High        High         High
3 · Occasional    Low           Medium    Medium      High         High
2 · Seldom        Low           Low       Medium      Medium       High
1 · Unlikely      Low           Low       Low         Medium       Medium
```

Three properties of this resolution rule are worth making explicit, because they encode risk-management judgments:

1. **Impact is weighted more heavily than probability in the tails.** A Catastrophic (I = 5) event reaches High at moderate probability (P ≥ 3), whereas a Frequent (P = 5) but Negligible (I = 1) event never rises above Medium. This asymmetry reflects the treasury principle that low-probability/high-severity events deserve disproportionate attention — the logic of tail-risk hedging.
2. **The Low region is conservative.** Only the genuinely benign corner (low P *and* low I) is Low, so the system is biased toward surfacing rather than suppressing.
3. **The output is ordinal, not cardinal.** The matrix deliberately does not multiply P × I into a 1–25 score, which would falsely imply that the axes are cardinal and equally spaced. A monotone but non-multiplicative cell assignment avoids the well-documented "range compression" pathology of risk matrices.

### 3.3 Why a matrix rather than a single score

A purely scalar risk score (0–100) is attractive for sorting but discards the *reason* a thing is risky. Two events can share a score of 70 while being qualitatively different — one near-certain but moderate, the other unlikely but catastrophic — and they demand different responses (monitor vs. hedge). By preserving both coordinates, Argus keeps the decision-relevant structure visible: the user sees not just *how* risky but *why*, which directly serves the platform's design principle of *actionable, self-evident* feedback. Internally, the matrix level is also mapped to an ordinal rank (Low = 0, Medium = 1, High = 2) for sorting and comparison, but the two coordinates are always retained alongside it.

### 3.4 From framework to engine

The theoretical contribution of Argus is the claim that **both matrix coordinates can be assigned automatically, at scale, to live and heterogeneous financial events, with acceptable accuracy and full auditability**. §5 describes how. The remainder of the framework's value — that the *same* coordinates can be personalised per user — is developed in §6.

---

## 4. System Architecture

### 4.1 High-level data flow

Argus is the downstream half of a two-repository system. Data flows in one direction:

```
┌─────────────────────────────┐         ┌──────────────────────────────────────────┐
│  AI Treasury Project         │  write  │  Google Cloud Storage (GCS)              │
│  (upstream ingestion)        │ ──────▶ │  bucket: raw/dt=YYYY-MM-DD/<source>.csv  │
│  82 configured sources       │         └──────────────────────────────────────────┘
│  FRED · GDELT · USGS · BIS … │                          │ read-only
└─────────────────────────────┘                          ▼
                                  ┌────────────────────────────────────────────────┐
                                  │  ARGUS (this report)                           │
                                  │  ┌──────────────┐  ┌───────────────────────┐   │
                                  │  │ Ingest/normal│  │ Scoring pipeline (§5)  │   │
                                  │  │ -ise GCS data│─▶│ rules → LLM → ML       │   │
                                  │  └──────────────┘  └──────────┬────────────┘   │
                                  │                               ▼                │
                                  │  ┌───────────────┐  ┌───────────────────────┐  │
                                  │  │ Persona engine│◀─│ Probability × Impact   │  │
                                  │  │ (§6)          │  │ matrix (§3)            │  │
                                  │  └──────┬────────┘  └───────────────────────┘  │
                                  │         ▼                                      │
                                  │  ┌─────────────────────────────────────────┐  │
                                  │  │ AI layer: LLM router · RAG · agents (§7)│  │
                                  │  └──────────────────┬──────────────────────┘  │
                                  │                     ▼                         │
                                  │  Decision support: dashboard · geomap ·      │
                                  │  intelligence briefs · action cards (§8)     │
                                  └────────────────────────────────────────────────┘
```

The strict separation of concerns — ingestion upstream, intelligence downstream, GCS as the contract between them — is an architectural decision with two benefits. It lets the data layer and the analytics layer evolve and deploy independently, and it makes Argus's inputs reproducible: any day's analysis can be re-run against the immutable, date-partitioned raw snapshot in GCS.

### 4.2 Technology stack

Argus is implemented in **Python 3** with a **Flask** web layer served by **gunicorn** behind **nginx** on a Google Cloud VM. The persistence layer is **SQLite** as the authoritative store (news, classifications, risk analyses, regulatory changes, telemetry) plus a **ChromaDB** vector store as a retrieval replica (§7.2). The front end is a single-page application (HTML/CSS/standard JavaScript) with a risk-monitor view, an interactive geomap, a decision-support view, and a four-tab administrative console. LLM access is provider-agnostic through an internal router (§7.1).

### 4.3 Module map

The repository organises responsibilities as follows (selected modules):

| Concern | Module(s) |
|:--------|:----------|
| Probability × Impact matrix | `risk_matrix.py` |
| Keyword/threshold classification | `risk_classifier.py`, `risk_classification.json`, `thresholds.json` |
| Event ingestion & scoring orchestration | `core/guardian_loader.py`, `daily_monitor.py`, `agents_system.py` |
| Persona model | `personas/defaults.yaml`, `personas/persona_loader.py`, `personas/profiles/` |
| LLM access | `core/llm_router.py`, `core/llm_providers.py`, `core/llm_telemetry.py` |
| Retrieval (RAG) | `core/vector_store.py`, `core/embedder.py`, `core/rag_context.py` |
| Autonomous agents | `core/agent_runner.py`, `core/agents/`, `core/tools/` |
| Action generation | `generate_actions.py`, `routes/actions_routes.py` |
| Web API | `routes/*.py` (risk monitor, alerts, intelligence, admin, …) |

### 4.4 Design principles

The platform follows four interaction-design principles that shape the architecture as much as the UI: **design for goals, not features** (the user wants prioritised risk, not raw indicators); **don't make users think** (the matrix cell and its colour are self-evident); **the system bears the complexity** (deduce probability and impact rather than asking the user); and **progressive disclosure** (a hover shows the headline, a click reveals the reasoning and evidence). These principles explain, for example, why scoring is automatic (§5) and why every action recommendation carries a justification (§8).

---

## 5. Event Scoring Methodology

This section answers the central technical question: **how does an event acquire its Probability and Impact coordinates?** Argus uses a *layered* approach with three scoring sources and a deterministic resolution, so that the matrix is populated robustly even when individual layers are unavailable.

### 5.1 The layered pipeline

```
            Raw event (indicator move, news item, alert, regulatory change)
                                   │
            ┌──────────────────────┼───────────────────────────────┐
            ▼                      ▼                               ▼
   Layer 1: Rules/keywords   Layer 2: LLM scoring         Layer 3: ML model
   (deterministic)           (Claude via router)          (supervised, §10)
   risk_classifier.py        proposes P, I + reasoning     downside-risk prob.
   thresholds.json           grounded by RAG (§7.2)        guardian_poc/
            │                      │                               │
            └──────────────────────┼───────────────────────────────┘
                                   ▼
                       Probability (1–5), Impact (1–5)
                                   │
                                   ▼
                     Probability × Impact matrix (§3)  →  Low / Medium / High
                                   │
                                   ▼
                     Persona re-weighting (§6)  →  user-specific score & alert
```

### 5.2 Layer 1 — deterministic rules and keyword classification

The first layer is fully deterministic and requires no model. It serves both as a fast first pass and as the **fallback** that guarantees the system degrades gracefully when LLM or ML layers are unavailable.

**Indicator thresholds.** For numeric market data (rates, FX, CPI, yields, auction sizes), `thresholds.json` encodes a library of rules. Each rule names an indicator, a comparison `type` (`absolute`, period-over-period `change`, or `yoy`), an operator and threshold, a severity, a risk category — and, critically, **default `probability` and `impact` integers**. For example, a rule for US CPI rising more than 0.3% month-over-month carries `probability: 3, impact: 3`; a rapid-acceleration variant carries higher coordinates. When the upstream data crosses a threshold, the rule fires and contributes its (P, I) directly.

**Keyword classification.** For unstructured text, `risk_classifier.py` matches a curated keyword map (defined in `risk_classification.json`) against the headline and body. The number of distinct matched keywords yields a 0–100 `risk_score` (capped), and the matched keywords resolve a three-tier taxonomy (core category → intermediate category → outer sources). A short list of high-priority keywords (e.g. *crisis, default, bankruptcy, fraud, cyber attack, scandal*) escalates an item regardless of count.

**Heuristic mapping to the axes.** When only a scalar `risk_score` is available, `risk_matrix.py` provides `score_to_probability()` and `score_to_impact()`, which map the 0–100 score to the 1–5 axes using slightly different breakpoints — the impact mapping is intentionally more sensitive at the low end, encoding the principle that treasury *impact* can be material even when *probability* is moderate.

### 5.3 Layer 2 — LLM scoring and reasoning

The second layer is where the system reads like an analyst. An LLM (Anthropic's Claude family, accessed through the router described in §7.1, **not** a hard-coded API call) is prompted to assess an event and to propose its Probability and Impact coordinates *together with a written justification*. The LLM call is grounded by retrieval (§7.2): the prompt includes the most relevant prior news, alerts, and regulatory changes from Argus's own corpus, so the model reasons from the system's record rather than from its static training data.

Two disciplines constrain this layer:

- **The LLM proposes; the matrix disposes.** The model returns *coordinates*, not a final risk level. The deterministic matrix of §3 converts those coordinates into Low/Medium/High. This means a hallucinated or miscalibrated coordinate can be wrong, but the *mapping* from coordinates to level is always consistent and inspectable.
- **Every call is metered.** All LLM usage is written to a telemetry table (`core/llm_telemetry.py`), recording task, latency, tokens, and cost, so that the contribution and expense of the LLM layer are auditable (§7.4).

### 5.4 Layer 3 — supervised machine learning **[CO-AUTHOR SECTION — see §10]**

The third layer is a trained model that estimates a forward "downside-risk" probability for a monitored entity from quantitative features (prices, balances, cash flows, and engineered signals). In the current implementation this is a logistic-regression model under `guardian_poc/`, whose output `probability` feeds the Probability axis for the entities it covers. The modelling methodology, feature engineering, training data, and validation are owned by a co-author and summarised in §10.

### 5.5 Resolution and conflict handling

The three layers do not always agree, and reconciling them is itself a design decision. The operative rules are: (i) deterministic threshold rules always fire when their condition is met (they encode hard, auditable policy); (ii) where an LLM or ML estimate is available for an item, it refines the coordinates that the heuristic mapping would otherwise assign; and (iii) in the absence of higher layers, the heuristic mapping from Layer 1 guarantees a valid (P, I) pair. The result is *defence in depth*: the matrix is always populated, the cheapest and most deterministic layer is always available, and the more expensive layers add judgment where they can.

### 5.6 Worked example

Consider a news item: *"Major bank announces liquidity stress; regulator opens inquiry."*
- **Layer 1** matches *liquidity*, *stress*, *inquiry*, and the high-priority cue *crisis*-adjacent vocabulary, yielding a high `risk_score` and the categories *liquidity_risk* and *regulatory_risk*.
- **Layer 2** (LLM, RAG-grounded) retrieves two prior, similar episodes from the corpus, judges recurrence *Likely* (P = 4) and treasury consequence *Hazardous* (I = 4), and writes a one-paragraph justification citing the retrieved precedents.
- **Matrix** resolves (P = 4, I = 4) → **High**.
- **Persona** (§6): for a *cash_manager* with elevated `liquidity_risk` weighting, the event clears the alert threshold and surfaces near the top; for a persona indifferent to liquidity it may fall below threshold.

The user sees a red "High" cell, the two coordinates, the contributing categories, and the LLM's justification with citations — *why*, not merely *how much*.

---

## 6. Persona-Driven Risk Personalisation

A single objective risk level is not enough, because risk is *relative to a mandate*. A cash manager, an FX manager, and a credit officer reading the same headline care about different facets of it. Argus encodes this directly.

### 6.1 The persona model

A **persona** is a parameterised model of a treasury role, defined in `personas/defaults.yaml` (the read-only AI-generated baseline) and optionally overridden by user-saved JSON profiles in `personas/profiles/`. A persona has three components:

1. **Risk-category weights.** A multiplier (typically 0.3–1.7) for each of thirteen risk categories: *market, credit, liquidity, operational, political, regulatory, reputation, systematic, disaster, supply, demand, process, business*. A weight ≥ 1.4 marks a core focus; ≤ 0.4 effectively filters the category out. For example, the `cash_manager` persona weights `liquidity_risk` at 1.6 and `regulatory_risk` at 1.4 but `reputation_risk` and `political_risk` at 0.5.
2. **Region overrides.** Shallow per-region diffs applied on top of the global weights. The same `cash_manager` elevates `political_risk`, `operational_risk`, and `credit_risk` for *Mexico* and `regulatory_risk`/`liquidity_risk` for *China*, reflecting genuine geographic exposure asymmetries. Only keys that differ from the global weight need to be listed; the effective weight is the global value overridden by any matching region key.
3. **Alert threshold.** A persona-specific cut-off (e.g. 40) below which a weighted event is monitored but not alerted, controlling the signal-to-noise trade-off per role.

### 6.2 How personalisation modifies the matrix outcome

The base event carries an objective risk_score and matrix coordinates (§5). The persona applies its category weight (and any region override) to produce a **persona-effective score**. This re-weighting can move an event across the alert threshold and changes its rank in the user's view, while the *objective* coordinates remain available for audit. The economic reading is precise: the persona is a **utility/relevance function over risk categories and regions**, and Argus computes expected relevance rather than asserting a single universal risk.

### 6.3 Override semantics

Profiles are merged over the YAML baseline **in memory** at load time (a deep merge / `.update()` of the JSON profile onto the defaults), per the project's strict rule that the baseline `defaults.yaml` is never overwritten by code. This keeps the AI-generated baseline immutable and auditable while allowing unlimited user customisation — an important property for a system intended to be trusted with risk decisions.

### 6.4 Persona evaluation

Because personalisation is a first-class feature, its quality is measured. A persona-evaluation harness (`core/persona_eval/`, `scripts/persona_eval.py`, surfaced in the admin console) checks that distinct personas actually produce distinct, sensible rankings — for instance, detecting a degenerate case in which all personas return identical alert sets (a Jaccard similarity of 1.0 indicating that personalisation is not biting). This evaluation discipline is what separates a *configurable* system from a *meaningfully personalised* one.

---

## 7. The AI / LLM Intelligence Layer

Argus's AI layer is deliberately structured so that intelligence is *additive and bounded*, never a single point of failure and never unbounded in cost.

### 7.1 The LLM router

All model access goes through `core/llm_router.py`, which exposes a single `call_llm(task=…)` entry point and an ordered list of provider/model attempts per task. This abstraction delivers four things: **provider independence** (the system is not wired to one vendor's SDK); **task-based routing** (different tasks — brief generation, chat, action generation — can use different models or fall back across providers); **graceful failover** (if the first attempt fails, the next is tried); and **uniform telemetry** (every call, whoever serves it, is logged identically). A documented operational constraint — one provider account having zero quota for a given model family — is handled at the router level rather than scattered through call sites.

### 7.2 Retrieval-augmented generation

To ground language outputs in the system's own record, Argus maintains a **ChromaDB** vector store (`core/vector_store.py`) alongside the authoritative SQLite database. An embedding provider (`core/embedder.py`, with a local sentence-transformers default and a hosted fallback) encodes documents into vectors; the store keeps separate collections for **news**, **alerts**, **regulatory** changes, and **historical actions**, each with rich metadata (date, region, category, score, matrix level). Retrieval is *hybrid*: a vector-similarity query combined with metadata pre-filters (e.g. region = UK, date ≥ last 7 days, score ≥ 30).

RAG is wired into three decision surfaces:
- **Intelligence brief** retrieves the news most relevant to a persona's focus, so the brief cites real, recent items rather than a generic summary.
- **Chat** retrieves news and alerts relevant to the user's question, so an answer to "what happened in China over the last month?" is grounded in the database, with citations.
- **Action generation** retrieves the most similar *past* recommendations, supplying few-shot precedent so that similar situations yield consistent advice.

The SQLite store remains the source of truth; ChromaDB is a best-effort retrieval replica kept in sync by write-hooks, with an audit script that alarms if the two diverge.

### 7.3 Bounded autonomous agents

For complex requests, Argus can escalate from single-shot RAG to a small set of **constrained autonomous agents** built on the provider's native tool-use loop (`core/agent_runner.py`, `core/agents/`, `core/tools/`):
- an **Intelligence agent** that answers multi-step analytical queries and returns markdown with citations;
- a **Response/chat agent** that handles complex conversational queries (simple ones stay on the cheaper RAG path);
- an **Actions agent** that assembles recommendations using tools such as `search_news`, `query_econ_indicator`, `get_fx_rate`, `get_persona_weights`, `get_cash_positions`, `get_historical_actions`, and `score_action_coherence`.

These agents are **bounded by explicit guards** configured per agent: a maximum number of turns, a maximum total token budget, and a hard per-call cost ceiling, enforced by the runner on every turn. Observed cost in production is a few cents per call, well under the ceiling. The agents are intentionally *not* given persistent memory, browser control, the ability to write code, or multi-agent free conversation — the boundaries are part of the safety design, not an incomplete implementation.

### 7.4 Observability and cost governance

Every model and retrieval interaction is metered. Telemetry tables record LLM calls (`llm_calls`), retrieval queries (`rag_queries`), and agent executions (`agent_executions`), joinable by a shared request id so that one can ask, for a given user action, *how many tokens it cost and how much retrieval saved*. A four-tab administrative SPA surfaces RAG, agent, LLM-usage, and pipeline health to operators. This instrumentation is what allows the AI layer to be defended as *auditable* rather than *opaque* — a prerequisite for use in a risk function.

---

## 8. Decision Support and Outputs

Argus's purpose is to change what a treasurer does next. Its outputs are organised by the progressive-disclosure principle: headline first, evidence on demand.

- **Risk-monitor dashboard.** The prioritised, persona-weighted list of current risks, each shown as a matrix cell (colour-coded Low/Medium/High) with its two coordinates and contributing categories.
- **Interactive geomap.** A geographic view of risk concentration aligned to the user's watchlist, with hover-for-headline and click-for-detail interaction.
- **Intelligence brief.** A short, RAG-grounded narrative of what matters now for the active persona, with citations to the underlying items.
- **Guardian chat.** A natural-language interface that answers grounded questions about the monitored environment, escalating to an agent for complex queries.
- **Action cards.** For each significant alert, concrete recommendations with a *why-now* justification, pros/cons, and (by design) a consistency check against historical precedent — turning a risk signal into a candidate decision rather than a dead-end notification.

Each surface embodies the principle of **actionable feedback**: the system never reports a problem without guiding the user toward the next step.

---

## 9. Data Collection (Upstream Summary) **[CO-AUTHOR SECTION]**

> *This section is a summary for completeness; the upstream data layer is documented in full in the companion AI Treasury Project report, and is owned by the data co-author.*

Argus does not collect data itself. The upstream **AI Treasury Project** fetches from **82 configured sources** — predominantly **FRED** economic series (policy rates, reference rates, yields, FX, CPI, monetary aggregates across the US, Eurozone, UK, Japan, Australia, Korea, India, Mexico, Canada, and others), plus news RSS feeds, **GDELT** geopolitical events, **ACLED** conflict events, **USGS** earthquakes, **ReliefWeb** disaster reports, **IMF** and **BIS** macro data, **Banxico** and **Bank of Canada** series, and US Treasury auctions and press releases. The fetchers normalise each source to tabular CSV and write to **Google Cloud Storage**, partitioned immutably by date (`raw/dt=YYYY-MM-DD/<source>.csv`). Argus reads this partitioned store read-only; a local crawler that previously existed in Argus is deprecated and not extended.

*Co-author hooks to expand here:* per-source coverage and reliability, the source-coverage gap (configured vs. consistently-producing sources), data-quality handling, and update latency for FRED-mirrored vs. native series.

---

## 10. Model Development and ML Scoring **[CO-AUTHOR SECTION]**

> *This section summarises the supervised ML layer (§5.4); depth is owned by the modelling co-author.*

The ML scoring layer estimates a forward downside-risk probability for covered entities and feeds the Probability axis of the matrix. The current implementation (`guardian_poc/`) trains a logistic-regression model on quantitative features derived from prices, balances, cash flows, income, and keyword signals, persists the artifact (`*.joblib`) with its metrics, and serves an inference path that loads the artifact and the latest feature row to emit a probability. 

*Co-author hooks to expand here:* feature engineering and selection, label definition for "downside," training/validation split and metrics (AUC/precision/recall), calibration of the model's probability onto the 1–5 axis, model-deployment workflow (artifact distribution and reproducibility), and the roadmap from a single demo model toward a more general scorer.

---

## 11. Evaluation

A risk-intelligence system must be evaluated on more than uptime. Argus is assessed along four dimensions.

**Functional coverage.** The end-to-end pipeline — ingest from GCS, classify, score across the three layers, resolve through the matrix, personalise, and surface — runs in production on a scheduled cadence (`daily_monitor.py`, cron-driven), and on demand via the web API. The matrix, persona engine, classification, and AI layer are all live.

**Personalisation efficacy.** The persona-evaluation harness (§6.4) measures whether distinct personas yield distinct, sensible alert sets, and was used to diagnose and correct a degenerate case in which personalisation was not biting (identical alert sets across personas, traced to a data-coverage root cause rather than to the persona logic itself).

**AI-layer cost and quality.** Telemetry (§7.4) shows autonomous-agent calls costing a few cents each in production, comfortably under the configured hard ceiling, and confirms that RAG grounding keeps LLM context focused. Retrieval quality is spot-checked by sampling queries and inspecting whether the top results are genuinely relevant.

**Engineering health.** The system carries the usual debts of a fast-moving project — uneven front-end test coverage, an ML-deployment workflow that is still partly manual, and observed cases where process discipline (staged roll-out with observation periods) was compressed under delivery pressure. These are documented honestly because a digital-finance case study is more instructive when its limitations are visible.

---

## 12. Discussion: Economic and Digital-Finance Implications

### 12.1 Automating a judgment instrument

The risk matrix has always been a *judgment* instrument: a human places events on the two axes. Argus's central move is to **automate the placement** while preserving the instrument's interpretability. This is a microcosm of a broader digital-finance pattern — using AI not to invent a new, opaque metric but to *scale an existing, trusted one*. The economic value is in throughput and consistency: a system can place thousands of events per day on the matrix with a stable, auditable rule, where a human team can place dozens.

### 12.2 Personalisation as a relevance function

Treating the persona as a weighting over risk categories and regions reframes "risk" from an absolute to a **conditional** quantity — risk *given a mandate and an exposure profile*. This is economically natural: the same event has different marginal consequences for different balance sheets. Argus makes that conditioning explicit and inspectable, which is precisely what a regulator or an auditor of a risk function would demand.

### 12.3 Disciplining the LLM

Perhaps the most transferable lesson for a digital-finance audience is architectural. The LLM is powerful but non-deterministic and prone to hallucination; left to "have the last word" it would be unsuitable for a risk function. Argus's design — LLM *proposes* coordinates, deterministic matrix *disposes* the level, RAG *grounds* the reasoning in the system's own record, and telemetry *meters* every call — is a template for **trustworthy AI in finance**: capability where it helps, determinism and auditability where they matter.

### 12.4 Relation to the broader digital-finance agenda

While Argus does not use blockchain, it sits squarely in the digital-finance research programme alongside it: both are about re-architecting financial processes around data, automation, and verifiability. Argus's contribution to that programme is a concrete demonstration that an *economically meaningful framework* (the matrix) and *modern AI* are complements, not substitutes.

---

## 13. Limitations and Future Work

**Discretisation and axis sensitivity.** The 5 × 5 matrix inherits the classical critiques of risk matrices: coarse ordinal cells, sensitivity to axis-label definitions, and the temptation to over-interpret ordinal levels. Argus mitigates the worst pathology (it does not multiply the axes into a false cardinal score) but the boundaries between cells remain a designed, not derived, choice.

**Calibration of the probability axis.** Mapping heterogeneous signals — a threshold breach, an LLM judgment, an ML probability — onto a common 1–5 probability scale is a calibration problem that is currently handled by fixed breakpoints and model-specific mappings. A principled, data-driven calibration (e.g. mapping ML probabilities onto axis levels via observed base rates) is future work.

**LLM non-determinism.** Even with RAG grounding and the propose/dispose discipline, repeated LLM scoring of the same item can yield different coordinates. Quantifying and bounding this variance is an open task.

**Data coverage.** Personalisation and scoring are only as good as the breadth of consistently-producing upstream sources; a known gap between *configured* and *reliably-producing* sources can flatten persona differentiation (§6.4, §11). Closing this is a shared upstream/downstream priority.

**Operational maturity.** Cost-alerting automation, ML-artifact deployment, and front-end test coverage are identified debts; staged roll-out discipline should be restored for future autonomous-agent changes.

**Future directions.** Promising extensions include: data-driven axis calibration; fine-tuning the embedding model on Argus's labelled history for better domain retrieval; a persistent-memory tier for agents (currently deliberately stateless); explicit confidence intervals on matrix coordinates; and back-testing the matrix's predictive validity against realised treasury outcomes.

---

## 14. Conclusion

Argus operationalises a classical risk-management instrument — the Probability × Impact matrix — for live treasury risk, and personalises its output to the role and exposures of each user. Its design thesis is that modern AI is most valuable in finance when embedded in, and disciplined by, an economically meaningful and auditable framework: the large language model proposes coordinates and explains itself, retrieval grounds it in the system's own record, a supervised model contributes where data supports it, and a deterministic matrix renders the final, interpretable judgment. The result is a system that reports *risk*, not merely *data*, and that adapts to *who is asking*. As a worked example for the study of digital finance, Argus illustrates a reusable pattern for trustworthy financial AI — capability where it helps, determinism and transparency where they are non-negotiable.

---

## References

*(Indicative; to be finalised in the project's citation style.)*

1. International Organization for Standardization. *ISO 31000: Risk Management — Guidelines.*
2. Committee of Sponsoring Organizations of the Treadway Commission (COSO). *Enterprise Risk Management — Integrating with Strategy and Performance.*
3. Cox, L. A., Jr. "What's Wrong with Risk Matrices?" *Risk Analysis*, 28(2), 2008.
4. Jorion, P. *Value at Risk: The New Benchmark for Managing Financial Risk.* McGraw-Hill.
5. Lewis, P., et al. "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks." *NeurIPS*, 2020.
6. Bao, W., Yue, J., Rao, Y. "Deep learning framework for financial time series." *PLoS ONE*, 2017.
7. Federal Reserve Economic Data (FRED), Federal Reserve Bank of St. Louis. *Public economic time-series database.*
8. The GDELT Project. *Global Database of Events, Language, and Tone.*

---

## Appendix A — The Resolution Matrix (reproduced)

| P \ I | 1 Negligible | 2 Minor | 3 Moderate | 4 Hazardous | 5 Catastrophic |
|:-----:|:---:|:---:|:---:|:---:|:---:|
| **5 Frequent** | Medium | High | High | High | High |
| **4 Likely** | Medium | Medium | High | High | High |
| **3 Occasional** | Low | Medium | Medium | High | High |
| **2 Seldom** | Low | Low | Medium | Medium | High |
| **1 Unlikely** | Low | Low | Low | Medium | Medium |

## Appendix B — The Thirteen Risk Categories

market · credit · liquidity · operational · political · regulatory · reputation · systematic · disaster · supply · demand · process · business

Each category carries a persona-specific weight (typically 0.3–1.7) and optional per-region overrides, per §6.

## Appendix C — Glossary

**Persona** — a parameterised model of a treasury role (category weights + region overrides + alert threshold). **Probability axis** — likelihood over the forward window (1 Unlikely – 5 Frequent). **Impact axis** — treasury consequence severity (1 Negligible – 5 Catastrophic). **RAG** — retrieval-augmented generation. **LLM router** — provider-agnostic model-access abstraction. **Alert threshold** — persona-specific score cut-off for surfacing an alert.

---

*End of Argus report. Sections marked **[CO-AUTHOR SECTION]** (§9, §10) are intended for expansion by the data-collection and modelling co-authors.*
