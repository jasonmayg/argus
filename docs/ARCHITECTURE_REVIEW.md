# Argus — Principal Architecture Review

**Reviewer:** Principal Software Architect
**Date:** 2026-04-16
**Scope:** Pre-deployment review (Cloud Run / external users)
**Repo state:** `main` @ `d2ee80e`

This review reads the full Python/Flask backend (`frontend_app.py`, `routes/*`, `personas/persona_loader.py`, `state.py`) and the Vanilla JS frontend (`static/js/*`). Findings are sequenced by deployment risk: blockers first, then design issues, then UX, then concrete patches.

---

## 1. Critical Bugs / Blockers

These must be resolved before exposing the application to external users.

### 1.1 A live OpenAI API key is sitting in `.env` on disk

`.env` (line 1) contains a real, usable secret:

```
OPENAI_API_KEY=sk-proj-REDACTED...
```

`.env` is gitignored, so it is not (currently) in the public repo, but the key is on a developer laptop in `~/Downloads`, will be baked into any container that copies `.env`, and is invoked by `routes/ai_routes.py`, `routes/actions_routes.py`, `core/agents.py`, `classify_news.py`, `classify_regulatory.py`, and `guardian_poc/src/guardian.py`. **Treat it as compromised and rotate it now**, regardless of whether the deployment pipeline ever shipped it. Verify by running `git log --all -- .env` (currently empty — good) and by searching the GitHub repo's secrets scan.

For Cloud Run, never bake `.env` into the image. Use Secret Manager + `--update-secrets`:

```bash
gcloud run deploy argus \
  --image gcr.io/<proj>/argus \
  --update-secrets=OPENAI_API_KEY=openai-api-key:latest,\
ANTHROPIC_API_KEY=anthropic-api-key:latest
```

### 1.2 Production server is the Werkzeug dev server with `debug=True`

`frontend_app.py:55`:

```python
if __name__ == "__main__":
    ...
    app.run(debug=True, port=API_PORT)
```

`debug=True` enables the Werkzeug interactive debugger and the PIN-protected RCE console. It also single-threads the server. CLAUDE.md already mandates Gunicorn — that mandate is not enforced anywhere in the repo (no `Procfile`, no `Dockerfile`, no `gunicorn` in `requirements.txt`).

**Fix.** Add Gunicorn, drop `debug` for prod, keep dev mode opt-in:

```python
# frontend_app.py
import os
app = create_app()

if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=API_PORT, debug=debug)
```

```
# requirements.txt
gunicorn==23.0.0
```

```
# Procfile (or Dockerfile CMD)
web: gunicorn --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120 frontend_app:app
```

Note `--workers 2`: with multiple workers you must also fix §2.3 (shared mutable state).

### 1.3 Stateful filesystem writes will be wiped on every Cloud Run revision

Cloud Run containers are ephemeral. Anything written outside `/tmp` is read-only; anything written inside `/tmp` survives only for the lifetime of that single container instance and is invisible to sibling instances during scale-out. The following write paths assume a persistent local FS:

| File | Writer | Consequence on Cloud Run |
| --- | --- | --- |
| `personas/profiles/<name>.json` | `routes/guardian_routes.py:348` (`save_profile`) | Saved profiles vanish on restart; users on instance B can't see profiles saved by users on instance A. |
| `shared_storage/latest_results.json` | `routes/pipeline_routes.py:39` (`_save_results`) | Pipeline output disappears between runs; `/api/results` will lie that there are no results. |
| `shared_storage/latest_actions.json` | `routes/actions_routes.py:49` (`_save_cache`) | AI-generated action cache lost; UI shows "empty" minutes after generation. |
| `data/cash_positions.xlsx` | Read-only at runtime, but expected to live next to the app | The current image-baked copy works, but treats the spreadsheet like config. |
| `data/fed_news/argus_news.db` (SQLite) | Pipeline writes; web reads via `routes/ai_routes.py`, `routes/regulatory_routes.py` | SQLite on Cloud Run is single-instance and lost on restart. |

**Fix — short-term (single-instance Cloud Run, `--max-instances=1`)**: redirect all writes to `/tmp` (the only writable location in a Cloud Run container) **and** acknowledge that everything is lost on cold start. Acceptable only for the PoC demo.

**Fix — production**: introduce a tiny storage adapter so callers don't care about the backend. Two-line example using GCS for profiles:

```python
# core/storage.py
import os, json
from google.cloud import storage

_BUCKET = os.getenv("ARGUS_STATE_BUCKET")  # e.g. "argus-state-prod"
_client = storage.Client() if _BUCKET else None

def write_json(key: str, data: dict) -> None:
    if not _client:                       # local dev
        from pathlib import Path
        p = Path("shared_storage") / key
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2))
        return
    _client.bucket(_BUCKET).blob(key).upload_from_string(
        json.dumps(data), content_type="application/json"
    )

def read_json(key: str) -> dict | None:
    if not _client:
        from pathlib import Path
        p = Path("shared_storage") / key
        return json.loads(p.read_text()) if p.exists() else None
    blob = _client.bucket(_BUCKET).blob(key)
    return json.loads(blob.download_as_text()) if blob.exists() else None
```

Then `pipeline_routes._save_results`, `guardian_routes.save_profile`, and `actions_routes._save_cache` each become a single `write_json(...)` call. Profiles should also list/load from the same bucket. For the SQLite news DB, migrate to Cloud SQL (Postgres) or Firestore — this is non-trivial but unavoidable for a multi-instance deployment.

### 1.4 Service-account JSON path is hard-coded to a developer laptop

`.env` line 5:
```
GOOGLE_APPLICATION_CREDENTIALS=/path/to/Downloads/argus/service-account.json
```

This path doesn't exist in any container. On Cloud Run, **don't ship the JSON key at all** — attach the service account to the revision and let ADC find it automatically:

```bash
gcloud run deploy argus --service-account=argus-runtime@<proj>.iam.gserviceaccount.com
```

Then remove `GOOGLE_APPLICATION_CREDENTIALS` from `.env` for the cloud build.

The JSON file `service-account.json` is in `.gitignore` via the wildcard `treasury-guardian-agent-*.json` — confirmed not in git history. **Still rotate this key**, since it lived unencrypted in `~/Downloads` for weeks.

### 1.5 `app.json_encoder` is a Flask 2.2-and-earlier API

`frontend_app.py:31`:

```python
app.json_encoder = SafeJSONEncoder
```

In Flask ≥ 2.3 (the version in `requirements.txt` per the README) this attribute is ignored — `jsonify` uses `app.json` (a `JSONProvider`) instead. `core/json_utils.SafeJSONEncoder` becomes dead code, and the `None`-handling fix referenced in `MEMORY.md` silently regresses.

**Fix.** Provide a `JSONProvider`:

```python
# core/json_utils.py
from flask.json.provider import DefaultJSONProvider

class SafeJSONProvider(DefaultJSONProvider):
    sort_keys = False
    def default(self, o):
        try:
            return super().default(o)
        except TypeError:
            return None  # drop unserialisable values, do not crash jsonify
```

```python
# frontend_app.py
from core.json_utils import SafeJSONProvider
def create_app():
    app = Flask(__name__)
    app.json = SafeJSONProvider(app)
    ...
```

Keep `sanitize_for_json()` on the call sites that deliberately strip `None` values; the provider is the safety net.

---

## 2. Architecture Flaws

### 2.1 Persona merge: shallow `.update()` is a foot-gun waiting to fire

The CLAUDE.md rule says "use Python's `.update()` (or deep merge)" — the codebase currently does both, inconsistently:

- **`personas/persona_loader.py:91-92` (`load_defaults_for_region`)** uses shallow `dict.update()` on `risk_weights`.
- **`personas/persona_loader.py:150-158` (`_deep_merge`, used by `merge_weights` → `load_profile`)** does a proper recursive merge.

`risk_weights` happens to be a flat `{str: float}` map today, so the shallow path is correct *right now*. But the moment anyone adds a nested override (e.g. `risk_weights.market_risk: { weight: 1.4, decay: 0.7 }`), the shallow `update()` will replace the entire nested dict and silently delete the AI defaults. This is exactly the kind of silent corruption that's hard to diagnose post-deploy.

**Fix.** Always use `_deep_merge`. Bonus: `load_defaults_for_region` currently *drops* `region_overrides` from the returned config (line 93–96), which is fine for clients but undocumented — call it out in the docstring.

```python
# personas/persona_loader.py
def load_defaults_for_region(persona_key: str, region_key: str) -> Dict[str, Any]:
    base = load_defaults(persona_key)
    if not base or region_key == "global" or region_key not in _KNOWN_REGIONS:
        return base
    overrides = base.get("region_overrides", {}).get(region_key, {})
    if not overrides:
        return base
    merged_weights = _deep_merge(base.get("risk_weights", {}), overrides)  # ← was .update()
    return {
        "alert_threshold": base.get("alert_threshold", 40),
        "risk_weights":    merged_weights,
    }
```

Frontend equivalent: `static/js/persona-settings.js:240` (`Object.assign(... weights.risk_weights)`) — same shallow concern, should be a deep merge if profile schema ever grows.

### 2.2 In-memory module globals will diverge across Gunicorn workers

`state.py` defines `_holder["orchestrator"]` and `execution_state` at module scope. Every Gunicorn worker imports a private copy. The flow `POST /api/execute` (worker A, sets `is_running=True`) → `GET /api/status` (worker B, sees `is_running=False`) is a guaranteed race.

The same applies to:
- `routes/ai_routes.py:35` `_news_cache`
- `routes/cash_routes.py:32` `_cache` + `routes/cash_routes.py:295` `/api/cash/cache/invalidate` (only invalidates the worker that handled the request)
- `routes/data_alert_routes.py:35` `_cache`

**Fix options:**

1. **Pin to `--workers 1 --threads N`** for the PoC. Acceptable for a small audience.
2. Move pipeline state to a shared store (Redis, Firestore). Even Cloud Memorystore is overkill for a PoC; Firestore with a single document is fine.
3. For caches, add a TTL key in GCS or use `cachelib.FileSystemCache(/tmp)` and accept per-instance variance.

Also wrap the `is_running` check in a lock — `pipeline_routes.py:62` reads then writes without one:

```python
import threading
_pipeline_lock = threading.Lock()

@pipeline_bp.route("/api/execute", methods=["POST"])
def execute_pipeline():
    with _pipeline_lock:
        if execution_state["is_running"]:
            return jsonify({"error": "Pipeline already running"}), 400
        execution_state["is_running"] = True
    ...
```

### 2.3 No persistence abstraction: storage is sprinkled across routes

Counted seven places that hand-roll `Path(...).write_text(...)` or `with open(... 'w')`. Replacing the FS with GCS today means touching seven call sites and remembering to update tests. Introduce `core/storage.py` (sketch in §1.3) and require all writes go through it. Same for reads of `latest_results.json` and `latest_actions.json`.

### 2.4 CORS is wide open

`frontend_app.py:30`: `CORS(app)` allows any origin. For an internally-served SPA on the same Cloud Run domain, you can drop `flask-cors` entirely. If you keep it, restrict origins:

```python
CORS(app, resources={r"/api/*": {"origins": [
    "https://argus.example.com",
    "https://argus-staging.example.com",
]}})
```

### 2.5 Path traversal partially blocked but not robust

`routes/guardian_routes.py:389`:

```python
if not filename or "/" in filename or "\\" in filename or not filename.endswith(".json"):
    return jsonify({"status": "error", "message": "Invalid filename"}), 400
```

This catches the common cases but relies on string matching. Replace with a positive identity check:

```python
from pathlib import PurePosixPath
candidate = (_PROFILES_DIR / filename).resolve()
if not str(candidate).startswith(str(_PROFILES_DIR.resolve())) or PurePosixPath(filename).name != filename:
    return jsonify({"status": "error", "message": "Invalid filename"}), 400
```

### 2.6 LLM cost & abuse have no server-side guardrails

`/api/chat`, `/api/brief`, and `/api/actions/generate` all call paid LLM endpoints with no rate limiting, no per-IP quota, and no response-length cap beyond `max_tokens`. An unauthenticated external attacker can drain the OpenAI bill in minutes once this is public.

**Fix.** At minimum add Flask-Limiter on the LLM routes:

```python
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
limiter = Limiter(get_remote_address, app=app, default_limits=["200/hour"])
limiter.limit("20/hour")(api_bp.view_functions["api_chat"])
```

Behind Cloud Run, swap `get_remote_address` for `X-Forwarded-For` parsing or use Cloud Armor for IP throttling.

### 2.7 Synchronous LLM calls inside a request thread

`routes/ai_routes.py` uses blocking `requests.post(..., timeout=30)` inside the request handler. With Werkzeug dev server (single-threaded), one slow LLM call blocks the entire app. With Gunicorn `--threads 4`, you'll exhaust threads under modest concurrency. For Cloud Run, this also means each in-flight chat counts toward the 60-second request limit.

**Fix.** Either (a) move LLM work off the request path (Pub/Sub + worker), or (b) at least bump Gunicorn `--threads` and set `--timeout 120`, and tell the frontend to time out after 25s and show a retry. Streaming the response (SSE) would also let the user see early tokens before the request limit hits.

---

## 3. Guardian Chat — conversation memory

### Where state lives today

- **Frontend (`static/js/chat.js:5`)** — sessions are stored in `localStorage.argusChatSessions`. On every send, the client passes `history` in the request body.
- **Backend (`routes/ai_routes.py:308-336`)** — `api_chat` reads `body.get("history", [])`, slices to last 10 turns (line 81: `history[-10:]`), and forwards them to OpenAI. `session_id` is accepted but **completely ignored**. The backend has zero memory across requests.

### What's good
- Statelessness fits Cloud Run perfectly — no sticky sessions needed.
- The 10-turn cap prevents prompt blow-up.

### What's broken or risky
1. **`session_id` is theatre.** The frontend sends it (`chat.js:104`); the backend never reads it. Either drop it from the API or actually use it (e.g. server-side log correlation).
2. **History trust boundary.** The client controls what history reaches OpenAI. A malicious client could inject any conversation, including system prompts, into turns marked `"role": "user"`. Today the system prompt is appended after history (`ai_routes.py:80-86`), which mitigates leakage of the system role, but injected `assistant`-role messages can still bias the next reply.
3. **No persistence on logout.** Clearing `localStorage` (incognito tab, "clear data") destroys all conversation history. Treasury professionals will expect their chats to survive.
4. **`localStorage` is ~5 MB and silently throws on quota exceeded.** `chat.js:9` `localStorage.setItem(...)` is unwrapped. A long power-user session will lose new messages without UI feedback.

**Recommendation.** For PoC, leave statelessness in place but:
- Drop the unused `session_id` from the API contract, or honour it by writing to Firestore with `{session_id, persona, turns[]}`.
- Wrap `saveSessions()` in `try/catch` and warn the user if quota is hit.
- Add a server-side total-token cap in addition to `max_tokens=1024`, since `history[-10:]` of 1k-token messages ≈ 10k input tokens per call.

```javascript
// chat.js
function saveSessions() {
    try {
        localStorage.setItem('argusChatSessions', JSON.stringify(chatSessions));
    } catch (e) {
        console.warn('Chat history full — pruning oldest session', e);
        chatSessions = chatSessions.slice(0, Math.max(1, chatSessions.length - 1));
        try { localStorage.setItem('argusChatSessions', JSON.stringify(chatSessions)); }
        catch { /* give up silently */ }
    }
}
```

---

## 4. Frontend Reactivity (Vanilla JS)

The frontend is a hand-rolled module-per-file system with shared globals (`API_BASE`, `currentPersona`, `availablePersonas`, `guardianTopAlerts`, `tsDraft`, `_lastScoredItems`). It works for a 5-page demo, but is hard to evolve.

### 4.1 Persona changes don't propagate

`static/js/persona.js:74` — `savePersonaSelection` only updates labels and `localStorage`. It does **not** re-fetch the dashboard, the risk matrix, or the chat context. So if the user is on the Risk Monitor page and changes persona, the page keeps showing data scored for the old persona until they navigate away and back.

**Fix.** Centralise persona-change as a custom event and let interested modules subscribe:

```javascript
// persona.js
function savePersonaSelection() {
    currentPersona = selectedPersonaKey;
    localStorage.setItem('argusPersona', currentPersona);
    closePersonaLogin();
    document.dispatchEvent(new CustomEvent('argus:persona-changed', {
        detail: { persona: currentPersona }
    }));
}

// guardian.js, results.js, risk-categories.js
document.addEventListener('argus:persona-changed', () => {
    loadGuardianDashboard();   // refresh alerts
    loadResults();             // refresh matrix
    loadRiskCategories();      // refresh home cards
});
```

This is also the right hook for the tune modal — instead of `if (typeof markActionsStale === 'function') markActionsStale()` sprinkled in `tune-modal.js:128`, dispatch `argus:weights-changed` and let the actions module decide.

### 4.2 Race condition in country-filter handoff

`static/js/guardian.js:13-17`:

```javascript
function showCountryEvents(countryName) {
    showPage('risk', riskNav);
    _guardianCountryFilter = countryName;
    setTimeout(() => _applyCountryFilter(countryName), 400);
}
```

The 400 ms timeout assumes `loadGuardianDashboard()` completes within that window. On a cold cache or slow network, the dashboard returns *after* 400 ms and the filter applies to the old (or empty) `guardianTopAlerts`, then the dashboard finishes and overwrites the filtered render.

**Fix.** Make `loadGuardianDashboard` return a promise and chain the filter explicitly:

```javascript
function showCountryEvents(countryName) {
    _guardianCountryFilter = countryName;
    showPage('risk', document.querySelector('.nav-item[onclick*="risk"]'));
    loadGuardianDashboard().then(() => _applyCountryFilter(countryName));
}
```

`loadGuardianDashboard` already does `async`/`await`, so it returns a promise; the caller just needs to use it.

### 4.3 Concurrent fetches with no cancellation

`static/js/navigation.js:26-31` — clicking the Risk page fires three concurrent fetches with no AbortController. Toggling pages quickly leaks responses that then race to render. Use `AbortController` and abort in-flight requests on `showPage` transition:

```javascript
let _pageAbort = null;
function showPage(pageId, navEl) {
    _pageAbort?.abort();
    _pageAbort = new AbortController();
    // pass _pageAbort.signal into every fetch on this page
}
```

### 4.4 Tightly coupled global state

`tune-modal.js` mutates `tsDraft` directly; `results.js` writes `window._lastScoredItems`; `guardian.js` writes `guardianTopAlerts`. There's no single source of truth, so it's easy to introduce bugs where one module's render lags another's update.

For the PoC the cost of refactoring outweighs the benefit. But before adding the *next* persona-aware widget, consider a tiny store:

```javascript
// static/js/store.js
const ArgusStore = {
    state: { persona: 'cash_manager', alerts: [], scoredItems: [], weights: {} },
    set(patch) { Object.assign(this.state, patch); document.dispatchEvent(new CustomEvent('argus:state', { detail: this.state })); },
};
```

### 4.5 XSS via `innerHTML` of API data

Multiple files do `innerHTML = ... ${escapeHtml(x)} ...` — but several others render API fields **without** escaping. Examples:

- `risk-categories.js:33-67` — `escapeHtml` is used consistently; good.
- `guardian.js:101-105` (`countryBoardGrid`) — `escapeHtml(c.country)` and `${c.alert_count}` (numeric, fine), but `${c.top_category || ''}` is not escaped. If the backend ever surfaces a category from third-party data containing HTML, it executes.
- `tune-modal.js:206-213` — `${val.toFixed(2)}` (safe) and `cat.label` (constant, safe). OK.

**Fix.** Audit every interpolation that sources from `/api/*` and wrap in `escapeHtml`. Better long-term: use `textContent`/`<template>` cloning and stop using `innerHTML` for anything that includes API data.

---

## 5. UX/UI Improvements

### 5.1 No actionable error states
`/api/results` returning `{"status":"no_results"}` causes `loadResults` to silently exit. The empty state does not tell the user *why* (pipeline never ran, vs. ran and failed). Per CLAUDE.md "Actionable Feedback", surface "Run Daily Jobs to populate" with a button.

### 5.2 The brief endpoint ignores region context
`api_brief` (`ai_routes.py:246`) takes `persona` but not `region`. Selecting a region in the tune modal does not change the home brief. Either pass `region` through, or document that the brief is intentionally global.

### 5.3 Pipeline is a fire-and-forget thread
`pipeline_routes.execute_pipeline` (line 67) spawns a thread and returns immediately. The user has no progress feedback unless they poll `/api/status`. There's no UI for that. Either add a polling hook to the frontend, or stream events via SSE.

### 5.4 Settings page conflates "draft" and "saved" state
`persona-settings.js:38` — `psDraft` is initialised from defaults, never cleared on tab switch. A user who tweaks `cash_manager` sliders, switches to `fx_manager`, then navigates away — comes back to a draft they don't remember making. Show a "Modified — not saved" banner whenever `psDraft != psDefaults`.

### 5.5 Chat empty state mentions data the bot may not have
`chat.js:30` — the greeting promises "access to your pipeline results, risk data, and market signals." If `latest_results.json` is missing (fresh deploy), the bot will answer from general knowledge but the greeting doesn't say so. Trim or condition the greeting on `/api/results.status`.

---

## 6. Actionable Fixes — minimum changes to ship

In rough priority order. Treat the first three as ship-blockers.

| # | File / Area | Change | Effort |
| --- | --- | --- | --- |
| 1 | `.env`, OpenAI dashboard | Rotate the leaked `sk-proj-…` key. Switch to Secret Manager for Cloud Run. | 30 min |
| 2 | `frontend_app.py`, `requirements.txt`, `Procfile`/`Dockerfile` | Add Gunicorn, drop `app.run(debug=True)`, set `host=0.0.0.0`. | 1 hr |
| 3 | `core/storage.py`, `routes/pipeline_routes.py`, `routes/guardian_routes.py`, `routes/actions_routes.py` | Introduce GCS-backed storage adapter; remove direct FS writes. | 4–6 hr |
| 4 | `frontend_app.py`, `core/json_utils.py` | Replace `app.json_encoder = …` with a `JSONProvider`. | 30 min |
| 5 | `personas/persona_loader.py:91` | Replace shallow `.update()` with `_deep_merge`. | 10 min |
| 6 | `state.py`, `pipeline_routes.py:62` | Add `threading.Lock` around `is_running` check; pin to `--workers 1` until pipeline state is externalised. | 30 min |
| 7 | `routes/ai_routes.py`, `routes/actions_routes.py`, `routes/guardian_routes.py` | Add Flask-Limiter on LLM endpoints (e.g. 20/hr/IP for chat, 10/hr/IP for brief). | 1 hr |
| 8 | `frontend_app.py` | Restrict CORS origins or remove `flask-cors`. | 5 min |
| 9 | `routes/guardian_routes.py:389` | Replace string-prefix path check with `Path.resolve` + `is_relative_to`. | 15 min |
| 10 | `static/js/persona.js`, `guardian.js`, `results.js`, `risk-categories.js` | Wire `argus:persona-changed` event so persona changes refresh widgets. | 2 hr |
| 11 | `static/js/guardian.js:13-17` | Replace `setTimeout(..., 400)` with promise chain. | 15 min |
| 12 | `static/js/chat.js:9` | Wrap `localStorage.setItem` in try/catch with prune-oldest fallback. | 15 min |
| 13 | `routes/ai_routes.py` | Drop unused `session_id` from contract or persist server-side. | 1 hr |
| 14 | `static/js/*` | Audit `innerHTML` interpolations for unescaped API fields. | 2 hr |

### Suggested deployment sequence

1. **Fixes 1, 2, 4** → safe to push to Cloud Run with `--max-instances=1`, no real users yet.
2. **Fixes 3, 6, 7, 8, 9** → safe to expose to a small invite-only cohort.
3. **Fixes 5, 10–14** → before opening to general external users.

Items not on the list (synchronous LLM calls in §2.7, persona/region brief context in §5.2, SSE streaming) are post-launch hardening — they degrade UX under load but don't break the demo.

---

## 7. Summary

The PoC is functionally complete and architecturally coherent. The blocking issues are entirely in the deployment seam: secrets in `.env`, the dev server, and filesystem writes that don't survive a stateless container. Fixing those three things, plus the persona-merge consistency bug in §2.1 and the JSON-provider regression in §1.5, gets the system into a state where you can put it behind Cloud Run with confidence. Everything else in this document is an investment in resilience and maintainability rather than a launch blocker.
