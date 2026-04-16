# Argus Risk Intelligence Platform — User Guide

> **Version:** 1.0 · **Audience:** Treasury Managers, Risk Officers, FX & Ops Teams

---

## Table of Contents

1. [Introduction](#1-introduction)
2. [Navigation Overview](#2-navigation-overview)
3. [Home Dashboard](#3-home-dashboard)
4. [Persona Switching](#4-persona-switching)
5. [Materiality Weights Tuner](#5-materiality-weights-tuner)
6. [Risk Monitor](#6-risk-monitor)
7. [Geospatial Heatmap](#7-geospatial-heatmap)
8. [Intelligence Brief](#8-intelligence-brief)
9. [Actions](#9-actions)
10. [Cash Position](#10-cash-position)
11. [Regulatory Tracker](#11-regulatory-tracker)
12. [Guardian Chat (Beta)](#12-guardian-chat-beta)
13. [Persona Settings (Advanced)](#13-persona-settings-advanced)
14. [Data Source & Pipeline](#14-data-source--pipeline)

---

## 1. Introduction

**Argus Risk Intelligence Platform** is a dynamic, persona-driven risk monitoring tool for Alphabet's Treasury teams. It combines live data feeds from 37+ global sources, AI-generated risk assessments, and interactive visualizations into a single unified workspace.

Rather than presenting a generic dashboard, Argus continuously re-evaluates your risk landscape based on your selected role, your custom materiality weights, and the region you care about — so the most operationally relevant signals always surface first.

### Key Capabilities

- **Persona-driven prioritization** — five built-in roles that reshape every score and ranking
- **Real-time weight tuning** — adjust how much each risk category matters and see changes instantly
- **5×5 Risk Matrix** — probability-impact grid populated from news signals and live data feeds
- **Geospatial heatmap** — global country-level risk visualization with click-to-filter
- **AI Intelligence Brief** — one-click narrative summary of the current risk picture
- **AI-generated Actions** — recommended treasury actions keyed to your persona and current alerts
- **Cash Position tracker** — live multi-currency positions with regional breakdown and historical chart
- **Regulatory Changes tracker** — classified news items flagged as rule changes with before/after diffs
- **Guardian Chat** — conversational interface for querying risk data in natural language

---

## 2. Navigation Overview

The left sidebar contains the main navigation. Use **Ctrl+B** to collapse or expand it at any time.

| Page | What it shows |
|------|---------------|
| **Home** | KPI summary cards, risk category overview, geospatial map, intelligence brief |
| **Risk Monitor** | 5×5 risk matrix, Guardian multi-country alerts, data-threshold alerts |
| **Guardian Chat** | Conversational chatbot for natural-language risk queries |
| **Cash Position** | Multi-currency cash holdings, regional KPIs, historical trend chart |
| **Regulatory** | Classified regulatory change notices with severity and action items |
| **Actions** | AI-recommended treasury actions based on current risk posture |
| **Data Source** | Pipeline execution controls, agent status, raw logs |
| **Persona Settings** | Full weight editor with per-region overrides for all 13 risk categories |

The **topbar** always shows the current page title, a live clock (UTC), and the active role name.

---

## 3. Home Dashboard

The home page is the starting point for each monitoring session. It gives a compact, high-level picture before you drill into any specific area.

### KPI Cards

Four summary cards at the top of the page show:

- **Signals** — number of news and data events ingested in the last pipeline run
- **Risk Level** — aggregate posture label (`CONTAINED` / `MODERATE` / `ELEVATED` / `CRITICAL`)
- **Last Run** — timestamp of the most recent pipeline execution
- **Active Role** — the currently selected persona

### Risk Category Cards

Below the KPIs, a row of cards groups alerts by risk category (e.g., *Liquidity Risk*, *FX Risk*, *Geopolitical*, *Regulatory*, *Natural Disaster*). Each card shows:

- The category icon and name
- A count of **news signals** and **data signals** currently active in that category
- The title of the highest-priority item in that category
- A color-coded top border — **red** if five or more total signals, **orange** for two or more, **blue** if only data signals are present

Click any category card to jump directly to the **Risk Monitor** page filtered to that category.

---

## 4. Persona Switching

Argus is role-aware. The persona you select changes how every score is calculated — alerts that matter to a Cash Manager may be noise to a Treasury Tech specialist, and vice versa.

### Available Roles

| Role | Key Focus |
|------|-----------|
| **Cash Manager** | Liquidity, process integrity, credit exposure |
| **FX Manager** | Currency volatility, cross-border political risk, market dynamics |
| **Treasury Ops** | Regulatory compliance, operational continuity, process risk |
| **Treasury Tech** | Operational and process risk, disaster resilience, system dependencies |
| **Risk Manager** | Systemic risk, broad geopolitical exposure, reputation and credit |

### How to Switch

Use the **Role** dropdown in the sidebar (or the role selector at the top of any page) to choose a persona. The dashboard re-evaluates instantly — alert scores, sort order, and color indicators all update without a page reload.

> Each persona carries a preconfigured **alert threshold** (e.g., 40 for Cash Manager, 45 for FX Manager). Events scoring below this threshold are suppressed from the main views to reduce noise.

---

## 5. Materiality Weights Tuner

The Weights Tuner is how you tell Argus what matters most to *you*, independent of your role defaults.

### Opening the Tuner

Click **Tune Strategy** on the home page or Risk Monitor page. A modal dialog opens showing two columns of sliders — one for each of the 13 risk categories.

### Risk Categories

| Group | Categories |
|-------|------------|
| **Financial** | Market, Credit, Liquidity, Systematic |
| **Operational** | Operational, Process, Business |
| **Geopolitical** | Regulatory, Political, Reputation |
| **Environment** | Disaster, Supply, Demand |

### Region Tab

At the top of the tuner, select a **region tab** to view and adjust region-specific overrides:

| Region | Notable AI Defaults |
|--------|---------------------|
| **Global** | Balanced baseline for all personas |
| **UK** | Regulatory risk elevated (post-Brexit compliance) |
| **Japan** | Disaster risk elevated (seismic/climate exposure); political risk reduced |
| **China** | Regulatory and political risk significantly elevated |
| **Mexico** | Political and operational risk elevated |
| **Korea** | Systematic and political risk elevated |
| **India** | Political, regulatory, and operational risk elevated |
| **US / Canada** | Regulatory risk elevated; lower political risk than emerging markets |

Regional overrides apply *on top of* your global weights. Only categories that differ from global are shown as overridden — the rest inherit the global value.

### Adjusting Weights in Real Time

Drag any slider to change the multiplier for that risk category. The risk monitor and alert list update live as you drag. Higher values make that category's events score more prominently; lower values de-emphasize them.

### Saving and Loading Profiles

Your custom weight configuration is not saved automatically. To preserve it:

1. Click **Save Profile** after adjusting the sliders.
2. Enter a descriptive name (e.g., *"Q2 Conservative FX"*).
3. The profile is stored locally in your browser under your current persona.

To restore a saved configuration in a future session:

1. Open **Tune Strategy**.
2. Click **Load Profile** and choose from the list.
3. The sliders restore to your saved values and the dashboard refreshes.

> **Note:** Profiles are browser-local. They are not synced to a server and will be cleared if you clear browser storage. Export your configuration if long-term backup is needed.

---

## 6. Risk Monitor

The Risk Monitor provides the most detailed view of current alerts, organized by probability and impact.

### 5×5 Risk Matrix

The matrix is a standard probability-impact grid:

- **Columns (Impact):** Negligible → Minor → Moderate → Hazardous → Catastrophic
- **Rows (Probability):** Unlikely → Seldom → Occasional → Likely → Frequent
- **Cell colors:** Low (green) → Medium (yellow) → High (orange) → Extreme (red)

Each cell shows a count of active events placed there. Click a cell to filter the alert list below to only events in that probability-impact range. Click the cell again to clear the filter.

### Alert List

Below the matrix, alerts are listed sorted by their composite score (Extreme → High → Medium → Low). Each alert shows:

- **Title and source** — headline text and originating data source
- **Risk level badge** — color-coded Extreme / High / Medium / Low
- **Score** — numeric value (0–100) reflecting weighted materiality
- **Category** — the risk type assigned by the classifier

### Guardian Alerts Panel

The Guardian section aggregates alerts from 37+ live data sources (GDELT, NOAA, USGS, FRED, SEC filings, and more). These appear alongside news-derived alerts and are factored into the matrix.

### Data-Threshold Alerts

A separate panel shows alerts triggered when live macro indicators cross configured thresholds (e.g., FX rate moves, credit spread changes, commodity prices). These are rule-based rather than AI-classified and update on a 5-minute cache cycle.

---

## 7. Geospatial Heatmap

The heatmap on the home page gives a global, at-a-glance view of where risk is concentrated.

### Color Legend

| Color | Risk Level |
|-------|------------|
| Dark Red | Critical |
| Orange | High |
| Yellow | Medium |
| Green | Low / Minimal |
| Grey | No current data |

Colors reflect the aggregated alert scores for each country, weighted by your current persona and materiality settings.

### Focus Countries

Eight countries are always highlighted with a border and country label regardless of data state: **US, China, Canada, UK, Japan, Mexico, India, Korea**. These are Alphabet's primary Treasury exposure regions.

A circular **badge** on each focus country shows the live count of active alerts for that region.

### Hover for Details

Hovering over any country shows a **summary card** with:

- Country name and overall risk score
- Top contributing risk categories
- Most recent triggering event title and date

### Click to Filter

Clicking a country on the map filters the **Risk Monitor** alert list to show only events associated with that country. A filter tag appears at the top of the list. Click **× Clear Filter** to return to the global view.

---

## 8. Intelligence Brief

The Intelligence Brief is an AI-generated narrative that synthesizes the current risk picture into a readable summary, tailored to your active persona.

### Generating a Brief

Click **Generate Brief** on the home page. The brief is generated using GPT-4o and streams to the screen with a typewriter effect.

After generation, a row of **signal pills** appears below the brief, highlighting the key themes detected (e.g., *Monetary Policy — High*, *Geopolitical — Medium*).

### Regenerating

Click **Regenerate** to produce a new brief using the latest pipeline data. This does not clear the previous brief until the new one is ready.

> **Note:** The brief requires a completed pipeline run. If no results are available, the brief panel will prompt you to run the pipeline first.

---

## 9. Actions

The Actions page translates current risk alerts into concrete, role-specific treasury recommendations.

### Risk Posture Banner

At the top of the page, a banner displays the overall **Risk Posture** for your current configuration:

| Posture | Criteria |
|---------|----------|
| **CRITICAL** | Any alert ≥ 75, or three or more alerts ≥ 70 |
| **ELEVATED** | Any alert ≥ 55, or at least one alert ≥ 70 |
| **MODERATE** | Any alert ≥ 40, or three or more alerts ≥ 40 |
| **CONTAINED** | No alerts meeting the above thresholds |

### Action Cards

Each recommended action shows:

- **Why now** — the specific event or signal driving the recommendation
- **Recommended action** — a concrete step (e.g., *"Review FX hedge ratios for JPY exposure"*)
- **Country / Region** — the geography this action applies to
- **Risk category** and **score**

Actions are grouped by country when multiple recommendations apply to the same region.

### Filter Settings

Use the **Settings** button to configure which actions appear:

- **Minimum score** — hide low-confidence recommendations (default: 40)
- **Exclude categories** — suppress specific risk types (e.g., Weather, Disaster) that are less operationally relevant
- **Sources** — toggle between Guardian alerts, data-threshold alerts, or pipeline risk matrix items

### Stale Actions Banner

If you change your persona or adjust materiality weights after actions were last generated, a yellow banner appears indicating the actions may be outdated. Click **Re-generate** to refresh them against the current risk configuration.

---

## 10. Cash Position

The Cash Position page shows Alphabet Treasury's multi-currency cash holdings and allows regional and entity-level drill-down.

### KPI Row

Five summary cards at the top show:

- **Total USD** — aggregate holdings converted at current FX rates
- **Americas** — subtotal for the Americas region
- **Europe** — subtotal for the Europe region
- **APAC** — subtotal for the Asia-Pacific region
- **High-Risk Exposure** — total USD held in entities currently flagged as High risk

### Position Table

The main table lists each treasury entity with:

- **Country and entity name**
- **Local currency amount** (in native currency with standard abbreviations)
- **USD equivalent**
- **Risk level badge** — High / Medium / Low based on current country risk scores

#### Filtering the Table

Use the filter buttons above the table to show positions by region:

- **All** — show every entity
- **Americas** — US, Canada, Mexico entities only
- **Europe** — UK and European entities
- **APAC** — Asia-Pacific entities

### Portfolio Bar Chart

A horizontal stacked bar chart below the KPIs shows the proportional share of total USD holdings by region at a glance.

### Historical Trend Chart

A line chart shows monthly cash balance history by region. Hover over any data point to see the exact balance for that month. This uses data from the `history` sheet of the configured Excel file.

> **Data note:** Positions are loaded from `cash_positions.xlsx` and cached for 5 minutes. The *as of* date shown is derived from the file's data, marked as simulated.

---

## 11. Regulatory Tracker

The Regulatory Tracker surfaces news items that have been classified as regulatory changes — rule updates, compliance deadlines, enforcement actions, and policy shifts — relevant to treasury operations.

### Filters

Use the filter buttons above the list to narrow results by:

- **Country** — e.g., United States, United Kingdom, Japan
- **Category** — e.g., Tax Policy, Sanctions, Reporting Requirements, Capital Controls
- **Severity** — High / Medium / Low

Active filters are highlighted. Click an active filter again to clear it. Multiple filters combine with AND logic.

### Change Cards

Each regulatory item shows:

- **Title and source URL** — the originating news item
- **Severity badge** — color-coded High (red) / Medium (orange) / Low (grey)
- **Country and category** tags
- **Published date** and **Effective date** (when available)
- **Change Before / Change After** — a before-and-after summary of what rule changed (where extracted)
- **Action Required** — a plain-language description of what the treasury team may need to do
- **Confidence score** — the classifier's confidence that this item is genuinely a regulatory change (shown as a percentage)

Items with confidence below 30% are suppressed from the default view.

### Re-analyse

Click **✦ Re-analyse** to trigger a fresh classification pass on unprocessed news items in the database. This is useful after running the pipeline if newly ingested items have not yet been classified.

---

## 12. Guardian Chat (Beta)

Guardian Chat is an experimental conversational interface for querying Argus risk data in natural language. It is powered by GPT-4o and has access to the same underlying data as the rest of the dashboard.

### Starting a Conversation

Type your question into the input field at the bottom of the chat page and press Enter or click **Send**.

Example queries:
- *"What are the top regulatory risks in Europe this week?"*
- *"Summarize liquidity alerts for the APAC region."*
- *"Which events are driving the high risk score for Japan?"*
- *"What actions should I take given current FX exposure in Mexico?"*

### Chat Sessions

Argus maintains multiple **chat sessions** so you can keep separate threads for different topics. Use the **+ New Session** button to start a fresh conversation. Previous sessions are listed in the left panel and can be resumed at any time.

### Feedback

Each response has a **thumbs up / thumbs down** control. Use these to signal whether the response was accurate and useful. This feedback is used to improve the model over time.

### Beta Limitations

- Guardian Chat does **not** retain context across separate sessions.
- Responses may be incomplete or imprecise — always verify critical information against the main Risk Monitor.
- Response time depends on data volume and model load.
- The chat does not have write access — it cannot execute the pipeline or modify settings.

---

## 13. Persona Settings (Advanced)

The Persona Settings page provides a full editor for all 13 risk-category weights across all five personas, with per-region granularity. This is the power-user version of the Tune Strategy modal.

### Persona Tabs

Select a persona tab on the left to edit its weight profile. The tab shows the persona name and a brief description of its focus area.

### Slider Grid

Sliders are organized into four groups:

- **Financial** — Market, Credit, Liquidity, Systematic
- **Operational** — Operational, Process, Business
- **Geopolitical** — Regulatory, Political, Reputation
- **Environment** — Disaster, Supply, Demand

Each slider shows its current value as a multiplier (e.g., `1.6×`). Move the slider to adjust. The preview list on the right updates live to reflect the re-scored alert order.

### Alert Threshold

Each persona has a configurable **Alert Threshold** (0–100). Events scoring below this value are hidden from all views for this persona. Raise the threshold to focus only on high-confidence signals; lower it to see more events.

### Saving and Loading Profiles (from Settings)

The **Save Profile** and **Load Profile** controls here work identically to those in the Tune Strategy modal — profiles saved from either location are shared. See [Section 5](#5-materiality-weights-tuner) for details.

### Reset to Defaults

Click **Reset to AI Defaults** to discard your changes and restore the AI-generated baseline weights for the selected persona. This does not affect saved profiles.

---

## 14. Data Source & Pipeline

The Data Source page is the engine room of Argus. It exposes the multi-agent intelligence pipeline and lets you trigger data collection on demand.

### Running the Pipeline

Click **Run Daily Jobs** to execute a full pipeline run. The pipeline progresses through the following stages:

| Stage | Agent | What it does |
|-------|-------|--------------|
| 1 | **News Gatherer** | Crawls Federal Reserve press releases and tagged news sources |
| 2 | **Risk Classifier** | Classifies each item across 13 risk categories with confidence scores |
| 3 | **Database Agent** | Stores classified items and keyword matches to SQLite |
| 4 | **Analysis Agent** | Scores items using materiality weights; builds the 5×5 risk matrix |
| 5 | **Reporting Agent** | Generates structured output consumed by the dashboard |
| 6 | **Guardian Job** | Loads and classifies 37+ external data feeds (GDELT, NOAA, USGS, FRED, SEC, etc.) |
| 7 | **Data Alert Scan** | Evaluates live macro indicators against configured thresholds |
| 8 | **Regulatory Classifier** | Flags news items as regulatory changes with before/after diffs |
| 9 | **Actions Generator** | Produces persona-specific recommended actions from Stage 5–8 outputs |

A progress indicator shows each stage as it moves from **Idle → Running → Complete**. When the run finishes, the Intelligence Brief is generated automatically.

### Agent Status

Below the progress indicator, an **Agent Grid** shows each agent's current state with last-run timing and any errors. Click an agent card to expand its detail view and see the last N log lines.

### System Logs

The **Logs** section streams the live output from the current pipeline run. Use **Show ▾ / Hide ▴** to expand or collapse the log panel.

### Pipeline Schedule

For automated daily runs, the pipeline is designed to be scheduled with cron. The recommended schedule is every 4 hours:

```
0 */4 * * *  cd /path/to/argus && source venv/bin/activate && python daily_monitor.py
```

See `setup_cron.sh` for the full automated setup script.

---

*For internal support, contact the Argus platform team through your IT help desk. Feature requests and bug reports can be filed in the project repository.*
