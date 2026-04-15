/* ============================================================
   ARGUS — Actions Page
   actions.js  (loaded at bottom of dashboard.html)
   ============================================================ */

// ── State ────────────────────────────────────────────────────
const ACTIONS_STORAGE_KEY = 'argusActionsSettings';

let actionsState = {
    data:        null,   // latest /api/actions response
    isGenerating: false,
    isStale:      false,
    settings: loadActionsSettings(),
};

function loadActionsSettings() {
    try {
        return JSON.parse(localStorage.getItem(ACTIONS_STORAGE_KEY)) || defaultSettings();
    } catch { return defaultSettings(); }
}

function defaultSettings() {
    return {
        minScore:        40,
        excludeCategories: ['Weather / NOAA', 'Disaster Risk'],
        sources: { guardian: true, data_alerts: true, matrix: true },
    };
}

function saveActionsSettings() {
    localStorage.setItem(ACTIONS_STORAGE_KEY, JSON.stringify(actionsState.settings));
}

// ── Risk Posture ─────────────────────────────────────────────
function calcPosture(items) {
    if (!items || !items.length) return 'CONTAINED';
    const highCount = items.filter(i => (i.effective_score || i.score || 0) >= 70).length;
    const medCount  = items.filter(i => (i.effective_score || i.score || 0) >= 40).length;
    const maxScore  = Math.max(...items.map(i => i.effective_score || i.score || 0));
    if (maxScore >= 75 || highCount >= 3) return 'CRITICAL';
    if (maxScore >= 55 || highCount >= 1) return 'ELEVATED';
    if (maxScore >= 40 || medCount  >= 3) return 'MODERATE';
    return 'CONTAINED';
}

function postureStyle(posture) {
    const map = {
        'CRITICAL':  { bg: 'var(--danger-bg)',  color: 'var(--danger-text)',  border: 'var(--danger-text)' },
        'ELEVATED':  { bg: 'var(--warning-bg)', color: 'var(--warning-text)', border: 'var(--warning-text)' },
        'MODERATE':  { bg: '#FEF9C3',           color: '#854D0E',              border: '#CA8A04' },
        'CONTAINED': { bg: 'var(--success-bg)', color: 'var(--success-text)', border: 'var(--success-text)' },
    };
    return map[posture] || map['CONTAINED'];
}

// ── Mark actions stale (called from persona/weight changes) ──
function markActionStale() {
    actionsState.isStale = true;
    const btn = document.getElementById('actionsRegenBtn');
    const banner = document.getElementById('actionsStaleBanner');
    if (btn) {
        btn.style.background    = '#EF4444';
        btn.style.color         = 'white';
        btn.style.borderColor   = '#EF4444';
        btn.textContent         = '↻ Re-generate (outdated)';
    }
    if (banner) banner.style.display = 'block';
}

// ── Settings Panel ────────────────────────────────────────────
function renderActionsSettings() {
    const s = actionsState.settings;
    const ALL_CATS = ['Weather / NOAA', 'Disaster Risk', 'Geopolitical', 'Regulatory',
                      'Macroeconomic', 'FX Risk', 'Liquidity Risk', 'Inflation'];
    const panel = document.getElementById('actionsSettingsPanel');
    if (!panel) return;

    panel.innerHTML = `
    <div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:20px; padding:16px 18px;">

      <!-- Min Score -->
      <div>
        <div class="section-title" style="margin-bottom:10px;">Min Score Threshold</div>
        <div style="display:flex; align-items:center; gap:12px;">
          <input type="range" id="actionsMinScore" min="0" max="100" value="${s.minScore}"
            style="flex:1; accent-color:var(--primary);"
            oninput="document.getElementById('actionsMinScoreVal').textContent=this.value;
                     actionsState.settings.minScore=+this.value; saveActionsSettings();">
          <span id="actionsMinScoreVal" style="font-family:var(--font-mono);font-weight:700;
                font-size:14px;color:var(--primary);min-width:28px;">${s.minScore}</span>
        </div>
        <div style="font-size:10px;color:var(--text-muted);margin-top:4px;">
          Alerts below this score are excluded
        </div>
      </div>

      <!-- Exclude Categories -->
      <div>
        <div class="section-title" style="margin-bottom:10px;">Exclude Categories</div>
        <div style="display:flex; flex-direction:column; gap:6px;">
          ${ALL_CATS.map(cat => {
            const checked = s.excludeCategories.includes(cat);
            return `<label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-size:11px;color:var(--text-secondary);">
              <input type="checkbox" ${checked ? 'checked' : ''}
                style="accent-color:var(--primary);"
                onchange="toggleExcludeCat('${cat}', this.checked)">
              ${cat}
            </label>`;
          }).join('')}
        </div>
      </div>

      <!-- Data Sources -->
      <div>
        <div class="section-title" style="margin-bottom:10px;">Data Sources</div>
        <div style="display:flex; flex-direction:column; gap:8px;">
          ${[
            ['guardian',    '📰 News'],
            ['data_alerts', '📊 Data Alerts'],
            ['matrix',      '◈ Matrix Nodes'],
          ].map(([key, label]) => `
            <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-size:11px;color:var(--text-secondary);">
              <input type="checkbox" ${s.sources[key] ? 'checked' : ''}
                style="accent-color:var(--primary);"
                onchange="actionsState.settings.sources['${key}']=this.checked; saveActionsSettings();">
              ${label}
            </label>`).join('')}
        </div>

        <button class="btn btn-primary" onclick="generateActions()"
          style="width:100%; justify-content:center; margin-top:16px;
                 padding:10px; font-size:12px; letter-spacing:0.08em;">
          ✦ Generate Recommendations
        </button>
      </div>

    </div>`;
}

function toggleExcludeCat(cat, checked) {
    const excl = actionsState.settings.excludeCategories;
    if (checked && !excl.includes(cat))  actionsState.settings.excludeCategories.push(cat);
    if (!checked) actionsState.settings.excludeCategories = excl.filter(c => c !== cat);
    saveActionsSettings();
}

function toggleActionsSettings() {
    const inner  = document.getElementById('actionsSettingsInner');
    const toggle = document.getElementById('actionsSettingsToggle');
    if (!inner) return;
    const show = inner.style.display === 'none';
    inner.style.display = show ? 'block' : 'none';
    toggle.textContent  = show ? '▴ Hide' : '▾ Settings';
}

// ── Generate ─────────────────────────────────────────────────
async function generateActions() {
    if (actionsState.isGenerating) return;
    actionsState.isGenerating = true;

    const btn     = document.getElementById('actionsRegenBtn');
    const genBtn  = document.getElementById('actionsGenerateBtn');
    const container = document.getElementById('actionsContainer');
    const summaryEl = document.getElementById('actionsSummary');

    if (btn)    { btn.disabled = true; btn.textContent = '⟳ Generating…'; }
    if (genBtn) { genBtn.disabled = true; genBtn.textContent = '⟳ Generating…'; }
    if (summaryEl) summaryEl.textContent = '—';

    // Show skeleton loader
    container.innerHTML = _skeletonCards(3);

    try {
        const payload = {
            persona:            typeof currentPersona !== 'undefined' ? currentPersona : 'cash_manager',
            min_score:          actionsState.settings.minScore,
            exclude_categories: actionsState.settings.excludeCategories,
            sources:            actionsState.settings.sources,
        };

        const r = await fetch('/api/actions/generate', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify(payload),
        });
        const d = await r.json();
        if (d.status !== 'success') throw new Error(d.message || 'Generation failed');

        actionsState.data    = d;
        actionsState.isStale = false;

        // Collapse settings now that we have data
        const inner  = document.getElementById('actionsSettingsInner');
        const toggle = document.getElementById('actionsSettingsToggle');
        if (inner)  inner.style.display  = 'none';
        if (toggle) toggle.textContent   = '▾ Settings';

        renderActionsResult(d);

    } catch(e) {
        container.innerHTML = `<div class="card" style="text-align:center;padding:40px;color:var(--danger-text);">
            <div style="font-size:24px;margin-bottom:8px;">⚠</div>
            <div style="font-size:13px;font-weight:600;">Generation failed</div>
            <div style="font-size:11px;color:var(--text-muted);margin-top:6px;">${e.message}</div>
        </div>`;
    }

    actionsState.isGenerating = false;
    if (btn)    { btn.disabled = false; btn.style.background = ''; btn.style.color = ''; btn.style.borderColor = ''; btn.textContent = '↻ Re-generate'; }
    if (genBtn) { genBtn.disabled = false; genBtn.textContent = '✦ Generate Recommendations'; }

    // Hide stale banner
    const banner = document.getElementById('actionsStaleBanner');
    if (banner) banner.style.display = 'none';
}

// ── Re-generate (header button) ──────────────────────────────
async function regenerateActions() {
    await generateActions();
}

// ── Load cached actions on page show ─────────────────────────
async function loadActions() {
    try {
        const r = await fetch('/api/actions');
        const d = await r.json();
        if (d.status === 'success' && d.actions?.length) {
            actionsState.data = d;
            renderActionsResult(d);
            // Collapse settings panel since we have data
            const inner  = document.getElementById('actionsSettingsInner');
            const toggle = document.getElementById('actionsSettingsToggle');
            if (inner)  inner.style.display  = 'none';
            if (toggle) toggle.textContent   = '▾ Settings';
        } else {
            renderActionsEmpty();
        }
    } catch {
        renderActionsEmpty();
    }
}

function renderActionsEmpty() {
    const container = document.getElementById('actionsContainer');
    if (!container) return;
    container.innerHTML = `
    <div style="text-align:center; padding:60px 20px;">
        <div style="font-size:40px; margin-bottom:16px; opacity:0.3;">▶</div>
        <div style="font-size:14px; font-weight:700; color:var(--text-main); margin-bottom:8px;">
            No recommendations yet
        </div>
        <div style="font-size:12px; color:var(--text-muted); margin-bottom:24px;">
            Configure your settings above and generate AI-powered treasury action recommendations.
        </div>
        <button id="actionsGenerateBtn" class="btn btn-primary"
            onclick="generateActions()"
            style="padding:12px 32px; font-size:13px; letter-spacing:0.08em;">
            ✦ Generate Recommendations
        </button>
    </div>`;
}

// ── Render Results ────────────────────────────────────────────
function renderActionsResult(d) {
    const actions   = d.actions || [];
    const summary   = d.summary || '';
    const posture   = d.posture  || calcPosture(actions);
    const meta      = d.generated_at || new Date().toISOString().replace('T',' ').substr(0,16) + ' UTC';
    const persona   = d.persona  || (typeof currentPersona !== 'undefined' ? currentPersona : '—');
    const weighted  = d.custom_weights ? ' · Custom Weights' : '';

    // Update header badges
    const postureBadge = document.getElementById('actionsPostureBadge');
    const metaEl       = document.getElementById('actionsMeta');
    if (postureBadge) {
        const ps = postureStyle(posture);
        postureBadge.textContent              = posture;
        postureBadge.style.background         = ps.bg;
        postureBadge.style.color              = ps.color;
        postureBadge.style.borderColor        = ps.border;
    }
    if (metaEl) metaEl.textContent = `Generated ${meta} · ${persona}${weighted}`;

    // Summary
    const summaryEl = document.getElementById('actionsSummary');
    if (summaryEl) summaryEl.textContent = summary || '—';

    if (!actions.length) {
        document.getElementById('actionsContainer').innerHTML = `
        <div class="card" style="text-align:center;padding:40px;color:var(--text-muted);">
            <div style="font-size:13px;">No actionable signals above threshold.</div>
        </div>`;
        return;
    }

    // Group by country
    const byCountry = {};
    actions.forEach(a => {
        const c = a.country || 'Global';
        if (!byCountry[c]) byCountry[c] = [];
        byCountry[c].push(a);
    });

    // Sort countries by max effective score desc
    const sortedCountries = Object.entries(byCountry).sort((a, b) => {
        const ma = Math.max(...a[1].map(x => x.effective_score || x.score || 0));
        const mb = Math.max(...b[1].map(x => x.effective_score || x.score || 0));
        return mb - ma;
    });

    document.getElementById('actionsContainer').innerHTML =
        sortedCountries.map(([country, items]) => _countryCard(country, items, d)).join('');
}

// ── Country Card ──────────────────────────────────────────────
function _countryCard(country, items, d) {
    const maxScore    = Math.max(...items.map(i => i.effective_score || i.score || 0));
    const catCount    = items.length;
    const cardId      = 'country_' + country.replace(/\W/g,'_');

    // Cash position for this country (from CASH_DATA if available)
    let cashBadge = '';
    if (typeof CASH_DATA !== 'undefined') {
        const cashRow = CASH_DATA.find(r => r.country === country);
        if (cashRow) {
            cashBadge = `<span style="font-size:10px;font-family:var(--font-mono);
                color:var(--primary);background:#EFF6FF;padding:2px 8px;border-radius:3px;
                border:1px solid #BFDBFE;">
                Cash: ${fmtUSD ? fmtUSD(cashRow.usd_equivalent||0) : '$—'}
            </span>`;
        }
    }

    const ps = postureStyle(calcPosture(items));

    // Sort items by effective score desc
    const sorted = [...items].sort((a,b)=>
        (b.effective_score||b.score||0) - (a.effective_score||a.score||0));

    return `
    <div class="card" style="margin-bottom:12px; padding:0; overflow:hidden;">

      <!-- Country Header -->
      <div style="display:flex; justify-content:space-between; align-items:center;
                  padding:12px 16px; background:#F8FAFC; border-bottom:1px solid var(--border);
                  cursor:pointer;"
           onclick="toggleCountryCard('${cardId}')">
        <div style="display:flex; align-items:center; gap:10px;">
          <div style="font-size:15px; font-weight:700; color:var(--text-main);">
            ${_flagEmoji(country)} ${country}
          </div>
          <span style="font-size:10px; font-family:var(--font-mono); color:var(--text-muted);">
            ${catCount} signal${catCount!==1?'s':''}
          </span>
          ${cashBadge}
        </div>
        <div style="display:flex; align-items:center; gap:10px;">
          <span style="font-size:11px; font-weight:700; padding:3px 10px; border-radius:12px;
                       background:${ps.bg}; color:${ps.color}; border:1px solid ${ps.border};
                       font-family:var(--font-mono);">
            Score ${maxScore.toFixed(0)}
          </span>
          <span id="toggle_${cardId}" style="font-size:12px; color:var(--text-muted);">▾</span>
        </div>
      </div>

      <!-- Country Body -->
      <div id="${cardId}" style="padding:12px 16px; display:flex; flex-direction:column; gap:8px;">
        ${sorted.map(item => _categoryCard(item)).join('')}
      </div>
    </div>`;
}

function toggleCountryCard(id) {
    const el     = document.getElementById(id);
    const toggle = document.getElementById('toggle_' + id);
    if (!el) return;
    const visible = el.style.display !== 'none';
    el.style.display    = visible ? 'none' : 'flex';
    toggle.textContent  = visible ? '▸' : '▾';
}

// ── Category Card ─────────────────────────────────────────────
function _categoryCard(item) {
    const score    = item.effective_score || item.score || 0;
    const severity = score >= 70 ? 'HIGH' : score >= 40 ? 'MEDIUM' : 'WATCH';
    const sevIcon  = severity === 'HIGH' ? '🔴' : severity === 'MEDIUM' ? '🟡' : '⚪';
    const ps       = postureStyle(severity === 'HIGH' ? 'CRITICAL' : severity === 'MEDIUM' ? 'ELEVATED' : 'CONTAINED');
    const catId    = 'cat_' + Math.random().toString(36).substr(2,8);

    // Alerts breakdown rows
    const alertRows = (item.alerts || []).map(a => `
        <div style="display:grid; grid-template-columns:1fr auto auto;
                    gap:8px; align-items:center; padding:5px 0;
                    border-bottom:1px solid var(--border);">
            <div style="font-size:11px; color:var(--text-secondary);
                        white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">
                ${a.title || '—'}
            </div>
            <div style="font-size:10px; font-family:var(--font-mono); color:var(--text-muted);
                        white-space:nowrap;">
                ×${(a.weight||1).toFixed(2)}
            </div>
            <div style="font-size:11px; font-weight:700; font-family:var(--font-mono);
                        color:${ps.color}; white-space:nowrap;">
                ${((a.base_score||0)*(a.weight||1)).toFixed(1)}
            </div>
        </div>`).join('');

    // Pros / Cons
    const pros = (item.pros || []).map(p =>
        `<div style="display:flex;gap:6px;font-size:11px;color:var(--success-text);margin-bottom:4px;">
            <span style="flex-shrink:0;">✓</span><span>${p}</span>
        </div>`).join('');
    const cons = (item.cons || []).map(c =>
        `<div style="display:flex;gap:6px;font-size:11px;color:var(--danger-text);margin-bottom:4px;">
            <span style="flex-shrink:0;">✗</span><span>${c}</span>
        </div>`).join('');

    // Alternatives
    const alts = (item.alternatives || []).map((a, i) =>
        `<div style="font-size:11px; color:var(--text-secondary); margin-bottom:4px;">
            <span style="font-family:var(--font-mono); font-weight:700;
                         color:var(--text-muted);">${i+1}.</span> ${a}
        </div>`).join('');

    return `
    <div style="border:1px solid var(--border); border-left:3px solid ${ps.border};
                border-radius:6px; overflow:hidden; transition:border-color 0.15s;"
         onmouseover="this.style.borderColor='${ps.border}'"
         onmouseout="this.style.borderColor='var(--border)'; this.style.borderLeftColor='${ps.border}'">

      <!-- Category Row (collapsed header) -->
      <div style="display:flex; justify-content:space-between; align-items:center;
                  padding:10px 14px; cursor:pointer; background:white;"
           onclick="toggleCategoryCard('${catId}')">
        <div style="display:flex; align-items:center; gap:8px; flex:1; min-width:0;">
          <span style="font-size:13px;">${sevIcon}</span>
          <div style="font-size:11px; font-weight:700; text-transform:uppercase;
                      letter-spacing:0.06em; color:${ps.color};
                      font-family:var(--font-mono); white-space:nowrap;">${severity}</div>
          <div style="font-size:12px; font-weight:600; color:var(--text-main); margin-left:4px;
                      white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">
              ${item.category || '—'}
          </div>
          <div style="font-size:11px; color:var(--text-muted); flex:1; overflow:hidden;
                      text-overflow:ellipsis; white-space:nowrap; min-width:0;">
              — ${(item.top_title || item.recommendation || '').substring(0,60)}…
          </div>
        </div>
        <div style="display:flex; align-items:center; gap:8px; flex-shrink:0;">
          <span style="font-family:var(--font-mono); font-weight:700; font-size:13px;
                       color:${ps.color};">${score.toFixed(0)}</span>
          <span id="cattoggle_${catId}" style="font-size:11px; color:var(--text-muted);">▸</span>
        </div>
      </div>

      <!-- Category Detail (expanded) -->
      <div id="${catId}" style="display:none; padding:14px; border-top:1px solid var(--border);
                                 background:#FAFAFA;">

        <!-- ① Score Breakdown -->
        ${alertRows ? `
        <div style="margin-bottom:16px;">
          <div style="font-size:9px; font-weight:700; text-transform:uppercase;
                      letter-spacing:0.1em; color:var(--text-muted); font-family:var(--font-mono);
                      margin-bottom:8px;">① Effective Score Breakdown</div>
          ${alertRows}
        </div>` : ''}

        <!-- ② Why Now -->
        ${item.why_now ? `
        <div style="margin-bottom:16px;">
          <div style="font-size:9px; font-weight:700; text-transform:uppercase;
                      letter-spacing:0.1em; color:var(--text-muted); font-family:var(--font-mono);
                      margin-bottom:6px;">② Why Now</div>
          <div style="font-size:12px; color:var(--text-secondary); line-height:1.6;">
              ${item.why_now}
          </div>
        </div>` : ''}

        <!-- ③ Recommended Action -->
        ${item.recommendation ? `
        <div style="margin-bottom:16px; background:#EFF6FF; border:1px solid #BFDBFE;
                    border-radius:5px; padding:12px 14px;">
          <div style="font-size:9px; font-weight:700; text-transform:uppercase;
                      letter-spacing:0.1em; color:var(--primary); font-family:var(--font-mono);
                      margin-bottom:6px;">③ ▶ Recommended Action</div>
          <div style="font-size:12px; font-weight:600; color:var(--primary); line-height:1.5;">
              ${item.recommendation}
          </div>
        </div>` : ''}

        <!-- ④ Expected Outcome -->
        ${item.expected_outcome ? `
        <div style="margin-bottom:16px;">
          <div style="font-size:9px; font-weight:700; text-transform:uppercase;
                      letter-spacing:0.1em; color:var(--text-muted); font-family:var(--font-mono);
                      margin-bottom:6px;">④ Expected Outcome</div>
          <div style="font-size:12px; color:var(--text-secondary); line-height:1.6;">
              ${item.expected_outcome}
          </div>
        </div>` : ''}

        <!-- ⑤ Pros / Cons -->
        ${(pros || cons) ? `
        <div style="margin-bottom:16px;">
          <div style="font-size:9px; font-weight:700; text-transform:uppercase;
                      letter-spacing:0.1em; color:var(--text-muted); font-family:var(--font-mono);
                      margin-bottom:8px;">⑤ Pros / Cons</div>
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px;">
            <div>${pros || '<div style="font-size:11px;color:var(--text-muted);">—</div>'}</div>
            <div>${cons || '<div style="font-size:11px;color:var(--text-muted);">—</div>'}</div>
          </div>
        </div>` : ''}

        <!-- ⑥ Alternative Actions -->
        ${alts ? `
        <div>
          <div style="font-size:9px; font-weight:700; text-transform:uppercase;
                      letter-spacing:0.1em; color:var(--text-muted); font-family:var(--font-mono);
                      margin-bottom:8px;">⑥ Alternative Actions</div>
          ${alts}
        </div>` : ''}

      </div>
    </div>`;
}

function toggleCategoryCard(id) {
    const el     = document.getElementById(id);
    const toggle = document.getElementById('cattoggle_' + id);
    if (!el) return;
    const visible = el.style.display !== 'none';
    el.style.display   = visible ? 'none' : 'block';
    toggle.textContent = visible ? '▸' : '▾';
}

// ── Skeleton Loader ───────────────────────────────────────────
function _skeletonCards(n) {
    const shimmer = `
        @keyframes shimmer {
            0%   { background-position: -400px 0; }
            100% { background-position:  400px 0; }
        }`;
    const bar = (w, h=10) =>
        `<div style="width:${w}; height:${h}px; border-radius:3px;
             background: linear-gradient(90deg, #F1F5F9 25%, #E2E8F0 50%, #F1F5F9 75%);
             background-size: 800px 100%;
             animation: shimmer 1.4s infinite linear; margin-bottom:8px;"></div>`;

    return `<style>${shimmer}</style>` + Array.from({length: n}, () => `
        <div class="card" style="margin-bottom:12px; padding:16px;">
            ${bar('40%', 14)}
            ${bar('60%', 10)}
            ${bar('80%', 10)}
            ${bar('55%', 10)}
        </div>`).join('');
}

// ── Flag Emoji helper ─────────────────────────────────────────
function _flagEmoji(country) {
    const map = {
        'USA':'🇺🇸','United States':'🇺🇸','UK':'🇬🇧','United Kingdom':'🇬🇧',
        'Germany':'🇩🇪','France':'🇫🇷','Japan':'🇯🇵','China':'🇨🇳',
        'India':'🇮🇳','Brazil':'🇧🇷','Canada':'🇨🇦','Australia':'🇦🇺',
        'Mexico':'🇲🇽','South Korea':'🇰🇷','Hong Kong':'🇭🇰',
        'Singapore':'🇸🇬','Switzerland':'🇨🇭','Netherlands':'🇳🇱',
        'Global':'🌐','Multiple':'🌐',
    };
    return map[country] || '🏳';
}

// ── Page init: wire up the Actions page HTML ──────────────────
function initActionsPage() {
    // Render settings panel
    renderActionsSettings();

    // Load cached data or show empty
    loadActions();
}

// ── Hook into showPage ────────────────────────────────────────
// Wrap existing showPage so Actions page gets initialised on first show
(function patchShowPage() {
    const _orig = window.showPage;
    if (!_orig) return;
    window.showPage = function(pageId, navEl) {
        _orig(pageId, navEl);
        if (pageId === 'actions') initActionsPage();
    };
})();

// Also patch savePersonaSelection to mark stale
(function patchPersona() {
    const _orig = window.savePersonaSelection;
    if (!_orig) return;
    window.savePersonaSelection = function() {
        _orig();
        markActionStale();
    };
})();

// ── DOMContentLoaded: insert Settings panel HTML into page ────
document.addEventListener('DOMContentLoaded', () => {
    const actionsPage = document.getElementById('page-actions');
    if (!actionsPage) return;

    // Insert the collapsible settings panel BEFORE actionsContainer
    const container = document.getElementById('actionsContainer');
    if (!container) return;

    const settingsDiv = document.createElement('div');
    settingsDiv.innerHTML = `
    <div class="card" style="padding:0; overflow:hidden; margin-bottom:12px;">
      <div style="display:flex; justify-content:space-between; align-items:center;
                  padding:10px 16px; background:#F8FAFC; border-bottom:1px solid var(--border);
                  cursor:pointer;" onclick="toggleActionsSettings()">
        <div style="font-size:9px; font-weight:700; text-transform:uppercase;
                    letter-spacing:0.1em; color:var(--text-muted); font-family:var(--font-mono);">
            ⚙ Generation Settings
        </div>
        <span id="actionsSettingsToggle" style="font-size:11px; color:var(--text-muted);">▾ Settings</span>
      </div>
      <div id="actionsSettingsInner">
        <div id="actionsSettingsPanel"></div>
      </div>
    </div>`;

    actionsPage.insertBefore(settingsDiv, container);
});