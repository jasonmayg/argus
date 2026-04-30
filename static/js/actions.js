/* ============================================================
   ARGUS — Actions Page (v2)
   - Softened language
   - 3 action options per risk (recommendation + 2 alternatives)
   - Popup with Why + SWOT per option
   - Risk Monitor deep-link support via localStorage
   ============================================================ */

// ── Threat templates by risk category ────────────────────
const THREAT_TEMPLATES = {
    'Market Risk':      'Adverse market movements may amplify losses beyond hedged positions.',
    'Credit Risk':      'Counterparty default could trigger cascading liquidity shortfalls.',
    'Liquidity Risk':   'Sudden funding withdrawal may force asset sales at unfavorable prices.',
    'Operational Risk': 'System disruption during peak settlement periods could delay critical payments.',
    'Supply Risk':      'Supply chain breakdown may increase input costs and compress margins.',
    'Demand Risk':      'Demand contraction could reduce revenue visibility and cash flow predictability.',
    'Process Risk':     'Logistics failure may interrupt cross-border payment flows.',
    'Business Risk':    'Trade policy shifts could restrict market access and increase compliance burden.',
    'Disaster Risk':    'Extreme weather events may disrupt regional operations and insurance coverage.',
    'Political Risk':   'Geopolitical escalation could trigger capital controls or asset freezes.',
    'Regulatory Risk':  'Regulatory tightening may increase compliance costs and restrict treasury flexibility.',
    'Reputation Risk':  'Negative media coverage could affect counterparty confidence and credit terms.',
    'Systematic Risk':  'Systemic financial stress may simultaneously impair multiple exposures across regions.',
};
const DEFAULT_THREAT = 'Broader macroeconomic deterioration may exacerbate this risk category.';

// ── Softened posture labels ───────────────────────────────
const POSTURE_LABELS = {
    'CRITICAL':  'Requires Immediate Attention',
    'ELEVATED':  'Elevated Priority',
    'MODERATE':  'Monitor Closely',
    'CONTAINED': 'Stable',
};

// ── Severity → Risk Monitor aligned labels ────────────────
const SEVERITY_LABELS = {
    'HIGH':   'High',
    'MEDIUM': 'Medium',
    'WATCH':  'Low',
};

// ── State ─────────────────────────────────────────────────
const ACTIONS_STORAGE_KEY = 'argusActionsSettings';

let actionsState = {
    data:         null,
    isGenerating: false,
    isStale:      false,
    settings:     loadActionsSettings(),
};

function loadActionsSettings() {
    try { return JSON.parse(localStorage.getItem(ACTIONS_STORAGE_KEY)) || defaultSettings(); }
    catch { return defaultSettings(); }
}
function defaultSettings() {
    return {
        minScore: 40,
        excludeCategories: ['Weather / NOAA', 'Disaster Risk'],
        sources: { guardian: true, data_alerts: true, matrix: true },
    };
}
function saveActionsSettings() {
    localStorage.setItem(ACTIONS_STORAGE_KEY, JSON.stringify(actionsState.settings));
}

// ── Posture calculation ───────────────────────────────────
function calcPosture(items) {
    if (!items?.length) return 'CONTAINED';
    const scores   = items.map(i => i.effective_score || i.score || 0);
    const highCount = items.filter(i => (i.effective_score || i.score || 0) >= 70).length;
    const medCount  = items.filter(i => (i.effective_score || i.score || 0) >= 40).length;
    const max       = Math.max(...scores);
    if (max >= 75 || highCount >= 3) return 'CRITICAL';
    if (max >= 55 || highCount >= 1) return 'ELEVATED';
    if (max >= 40 || medCount  >= 3) return 'MODERATE';
    return 'CONTAINED';
}

function postureStyle(posture) {
    const map = {
        'CRITICAL':  { bg:'var(--danger-bg)',  color:'var(--danger-text)',  border:'var(--danger-text)' },
        'ELEVATED':  { bg:'var(--warning-bg)', color:'var(--warning-text)', border:'var(--warning-text)' },
        'MODERATE':  { bg:'#FEF9C3',           color:'#854D0E',             border:'#CA8A04' },
        'CONTAINED': { bg:'var(--success-bg)', color:'var(--success-text)', border:'var(--success-text)' },
    };
    return map[posture] || map['CONTAINED'];
}

// ── Stale detection ───────────────────────────────────────
function markActionStale() {
    actionsState.isStale = true;
    const btn    = document.getElementById('actionsRegenBtn');
    const banner = document.getElementById('actionsStaleBanner');
    if (btn) {
        btn.style.background  = '#EF4444';
        btn.style.color       = 'white';
        btn.style.borderColor = '#EF4444';
        btn.textContent       = '↻ Refresh (outdated)';
    }
    if (banner) banner.style.display = 'block';
}

// ── Settings panel ────────────────────────────────────────
function renderActionsSettings() {
    const s = actionsState.settings;
    const ALL_CATS = ['Weather / NOAA','Disaster Risk','Geopolitical','Regulatory',
                      'Macroeconomic','FX Risk','Liquidity Risk','Inflation'];
    const panel = document.getElementById('actionsSettingsPanel');
    if (!panel) return;

    panel.innerHTML = `
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:20px;padding:16px 18px;">
      <div>
        <div class="section-title" style="margin-bottom:10px;">Min Score Threshold</div>
        <div style="display:flex;align-items:center;gap:12px;">
          <input type="range" id="actionsMinScore" min="0" max="100" value="${s.minScore}"
            style="flex:1;accent-color:var(--primary);"
            oninput="document.getElementById('actionsMinScoreVal').textContent=this.value;
                     actionsState.settings.minScore=+this.value;saveActionsSettings();">
          <span id="actionsMinScoreVal" style="font-family:var(--font-mono);font-weight:700;
                font-size:14px;color:var(--primary);min-width:28px;">${s.minScore}</span>
        </div>
        <div style="font-size:10px;color:var(--text-muted);margin-top:4px;">Signals below this score are excluded</div>
      </div>
      <div>
        <div class="section-title" style="margin-bottom:10px;">Exclude Categories</div>
        <div style="display:flex;flex-direction:column;gap:6px;">
          ${ALL_CATS.map(cat => `
            <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-size:11px;color:var(--text-secondary);">
              <input type="checkbox" ${s.excludeCategories.includes(cat)?'checked':''}
                style="accent-color:var(--primary);"
                onchange="toggleExcludeCat('${cat}',this.checked)">
              ${cat}
            </label>`).join('')}
        </div>
      </div>
      <div>
        <div class="section-title" style="margin-bottom:10px;">Data Sources</div>
        <div style="display:flex;flex-direction:column;gap:8px;">
          ${[['guardian','📰 Guardian News'],['data_alerts','📊 Data Alerts'],['matrix','◈ Matrix Nodes']].map(([k,l]) => `
            <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-size:11px;color:var(--text-secondary);">
              <input type="checkbox" ${s.sources[k]?'checked':''}
                style="accent-color:var(--primary);"
                onchange="actionsState.settings.sources['${k}']=this.checked;saveActionsSettings();">
              ${l}
            </label>`).join('')}
        </div>
        <button class="btn btn-primary" onclick="generateActions()"
          style="width:100%;justify-content:center;margin-top:16px;padding:10px;font-size:12px;letter-spacing:0.08em;">
          ✦ Generate Suggestions
        </button>
      </div>
    </div>`;
}

function toggleExcludeCat(cat, checked) {
    const excl = actionsState.settings.excludeCategories;
    if (checked && !excl.includes(cat)) excl.push(cat);
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

// ── Generate ──────────────────────────────────────────────
async function generateActions() {
    if (actionsState.isGenerating) return;
    actionsState.isGenerating = true;

    const btn       = document.getElementById('actionsRegenBtn');
    const genBtn    = document.getElementById('actionsGenerateBtn');
    const container = document.getElementById('actionsContainer');
    const summaryEl = document.getElementById('actionsSummary');

    if (btn)    { btn.disabled = true; btn.textContent = '⟳ Generating…'; }
    if (genBtn) { genBtn.disabled = true; genBtn.textContent = '⟳ Generating…'; }
    if (summaryEl) summaryEl.textContent = '—';
    container.innerHTML = _skeletonCards(3);

    try {
        const payload = {
            persona:            typeof currentPersona !== 'undefined' ? currentPersona : 'cash_manager',
            min_score:          actionsState.settings.minScore,
            exclude_categories: actionsState.settings.excludeCategories,
            sources:            actionsState.settings.sources,
        };
        const r = await fetch('/api/actions/generate', {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify(payload),
        });
        const d = await r.json();
        if (d.status !== 'success') throw new Error(d.message || 'Generation failed');

        actionsState.data    = d;
        actionsState.isStale = false;

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
    if (btn) {
        btn.disabled = false;
        btn.style.background = btn.style.color = btn.style.borderColor = '';
        btn.textContent = '↻ Refresh';
    }
    if (genBtn) { genBtn.disabled = false; genBtn.textContent = '✦ Generate Suggestions'; }
    const banner = document.getElementById('actionsStaleBanner');
    if (banner) banner.style.display = 'none';
}

async function regenerateActions() { await generateActions(); }

// ── Load cached ───────────────────────────────────────────
async function loadActions() {
    try {
        const r = await fetch('/api/actions');
        const d = await r.json();
        if (d.status === 'success' && d.actions?.length) {
            actionsState.data = d;
            renderActionsResult(d);
            const inner  = document.getElementById('actionsSettingsInner');
            const toggle = document.getElementById('actionsSettingsToggle');
            if (inner)  inner.style.display  = 'none';
            if (toggle) toggle.textContent   = '▾ Settings';
            // Apply deep-link filter from Risk Monitor
            _applyDeepLink();
        } else {
            renderActionsEmpty();
        }
    } catch { renderActionsEmpty(); }
}

function renderActionsEmpty() {
    const container = document.getElementById('actionsContainer');
    if (!container) return;
    container.innerHTML = `
    <div style="text-align:center;padding:60px 20px;">
        <div style="font-size:40px;margin-bottom:16px;opacity:0.3;">▶</div>
        <div style="font-size:14px;font-weight:700;color:var(--text-main);margin-bottom:8px;">No suggestions yet</div>
        <div style="font-size:12px;color:var(--text-muted);margin-bottom:24px;">
            Configure your settings above and generate treasury action suggestions.
        </div>
        <button id="actionsGenerateBtn" class="btn btn-primary" onclick="generateActions()"
            style="padding:12px 32px;font-size:13px;letter-spacing:0.08em;">
            ✦ Generate Suggestions
        </button>
    </div>`;
}

// ── Render result ─────────────────────────────────────────
function renderActionsResult(d) {
    const actions  = d.actions || [];
    const posture  = d.posture || calcPosture(actions);
    const meta     = d.generated_at || new Date().toISOString().replace('T',' ').substr(0,16) + ' UTC';
    const persona  = d.persona || '';

    const postureBadge = document.getElementById('actionsPostureBadge');
    const metaEl       = document.getElementById('actionsMeta');
    if (postureBadge) {
        const ps = postureStyle(posture);
        const softLabel = POSTURE_LABELS[posture] || posture;
        postureBadge.textContent       = softLabel;
        postureBadge.style.background  = ps.bg;
        postureBadge.style.color       = ps.color;
        postureBadge.style.borderColor = ps.border;
    }
    if (metaEl) metaEl.textContent = `Generated ${meta} · ${persona}`;

    const summaryEl = document.getElementById('actionsSummary');
    if (summaryEl) summaryEl.textContent = d.summary || '—';

    if (!actions.length) {
        document.getElementById('actionsContainer').innerHTML = `
        <div class="card" style="text-align:center;padding:40px;color:var(--text-muted);">
            <div style="font-size:13px;">No actionable signals above threshold.</div>
        </div>`;
        return;
    }

    // Group by country, sort by max score
    const byCountry = {};
    actions.forEach(a => {
        const c = a.country || 'Global';
        if (!byCountry[c]) byCountry[c] = [];
        byCountry[c].push(a);
    });
    const sortedCountries = Object.entries(byCountry).sort((a, b) => {
        const ma = Math.max(...a[1].map(x => x.effective_score || x.score || 0));
        const mb = Math.max(...b[1].map(x => x.effective_score || x.score || 0));
        return mb - ma;
    });

    document.getElementById('actionsContainer').innerHTML =
        sortedCountries.map(([country, items]) => _countryCard(country, items)).join('');
}

// ── Country card ──────────────────────────────────────────
function _countryCard(country, items) {
    const maxScore = Math.max(...items.map(i => i.effective_score || i.score || 0));
    const cardId   = 'country_' + country.replace(/\W/g,'_');
    const ps       = postureStyle(calcPosture(items));
    const sorted   = [...items].sort((a,b) =>
        (b.effective_score||b.score||0) - (a.effective_score||a.score||0));

    return `
    <div class="card" id="country_card_${cardId}" style="margin-bottom:12px;padding:0;overflow:hidden;">
      <div style="display:flex;justify-content:space-between;align-items:center;
                  padding:12px 16px;background:#F8FAFC;border-bottom:1px solid var(--border);
                  cursor:pointer;" onclick="toggleCountryCard('${cardId}')">
        <div style="display:flex;align-items:center;gap:10px;">
          <div style="font-size:15px;font-weight:700;color:var(--text-main);">
              ${_flagEmoji(country)} ${country}
          </div>
          <span style="font-size:10px;font-family:var(--font-mono);color:var(--text-muted);">
              ${items.length} signal${items.length!==1?'s':''}
          </span>
        </div>
        <div style="display:flex;align-items:center;gap:10px;">
          <span style="font-size:11px;font-weight:700;padding:3px 10px;border-radius:12px;
                       background:${ps.bg};color:${ps.color};border:1px solid ${ps.border};
                       font-family:var(--font-mono);">Score ${maxScore.toFixed(0)}</span>
          <span id="toggle_${cardId}" style="font-size:12px;color:var(--text-muted);">▾</span>
        </div>
      </div>
      <div id="${cardId}" style="padding:12px 16px;display:flex;flex-direction:column;gap:8px;">
        ${sorted.map(item => _categoryCard(item)).join('')}
      </div>
    </div>`;
}

function toggleCountryCard(id) {
    const el     = document.getElementById(id);
    const toggle = document.getElementById('toggle_' + id);
    if (!el) return;
    const visible = el.style.display !== 'none';
    el.style.display   = visible ? 'none' : 'flex';
    toggle.textContent = visible ? '▸' : '▾';
}

// ── Category card with 3 options ──────────────────────────
function _categoryCard(item) {
    const score    = item.effective_score || item.score || 0;
    const rawSev   = item.severity || (score >= 70 ? 'HIGH' : score >= 40 ? 'MEDIUM' : 'WATCH');
    const severity = SEVERITY_LABELS[rawSev] || rawSev;
    const sevColor = severity === 'High'   ? 'var(--danger-text)'  :
                     severity === 'Medium' ? 'var(--warning-text)' : 'var(--text-muted)';
    const sevBg    = severity === 'High'   ? 'var(--danger-bg)'    :
                     severity === 'Medium' ? 'var(--warning-bg)'   : 'var(--idle-bg)';
    const catId    = 'cat_' + Math.random().toString(36).substr(2,8);
    const category = item.category || '—';

    // Build 3 options from recommendation + alternatives
    const options = [];
    if (item.recommendation || item.recommended_action) {
        options.push(item.recommendation || item.recommended_action);
    }
    (item.alternatives || []).slice(0, 2).forEach(a => options.push(a));
    // Pad to 3 if needed
    while (options.length < 3 && options.length > 0) {
        options.push(options[options.length - 1]);
    }

    const optionCards = options.map((opt, idx) => {
        const optId = catId + '_opt' + idx;
        return `
        <div onclick="openSwotPopup('${optId}')"
             style="background:white;border:1px solid var(--border);border-radius:6px;
                    padding:10px 14px;cursor:pointer;transition:all 0.15s;
                    display:flex;justify-content:space-between;align-items:center;"
             onmouseover="this.style.borderColor='var(--primary)';this.style.background='#F8FAFF'"
             onmouseout="this.style.borderColor='var(--border)';this.style.background='white'">
          <div style="display:flex;align-items:flex-start;gap:10px;flex:1;min-width:0;">
            <div style="font-size:10px;font-weight:700;color:white;background:var(--primary);
                        border-radius:50%;width:20px;height:20px;display:flex;align-items:center;
                        justify-content:center;flex-shrink:0;margin-top:1px;">${idx+1}</div>
            <div style="font-size:12px;color:var(--text-main);line-height:1.5;">${escapeHtml(opt)}</div>
          </div>
          <span style="font-size:11px;color:var(--primary);font-family:var(--font-mono);
                       white-space:nowrap;margin-left:10px;flex-shrink:0;">Details →</span>
        </div>
        <!-- Hidden SWOT data -->
        <div id="${optId}_data" style="display:none;"
             data-option="${escapeHtml(opt)}"
             data-category="${escapeHtml(category)}"
             data-why="${escapeHtml(item.why_now || item.why || '')}"
             data-pros="${escapeHtml(JSON.stringify(item.pros || []))}"
             data-cons="${escapeHtml(JSON.stringify(item.cons || []))}"
             data-outcome="${escapeHtml(item.expected_outcome || '')}">
        </div>`;
    }).join('');

    return `
    <div style="border:1px solid var(--border);border-left:3px solid ${sevColor};border-radius:6px;overflow:hidden;">
      <!-- Category header -->
      <div style="display:flex;justify-content:space-between;align-items:center;
                  padding:10px 14px;background:#FAFAFA;cursor:pointer;"
           onclick="toggleCategoryCard('${catId}')">
        <div style="display:flex;align-items:center;gap:8px;">
          <span class="status-badge" style="background:${sevBg};color:${sevColor};font-size:10px;">${severity}</span>
          <span style="font-size:12px;font-weight:600;color:var(--text-main);">${escapeHtml(category)}</span>
          <span style="font-size:10px;color:var(--text-muted);font-family:var(--font-mono);">
              — ${escapeHtml((item.top_title||'').substring(0,50))}${(item.top_title||'').length>50?'…':''}
          </span>
        </div>
        <div style="display:flex;align-items:center;gap:8px;flex-shrink:0;">
          <span style="font-family:var(--font-mono);font-weight:700;font-size:13px;color:${sevColor};">${score.toFixed(0)}</span>
          <span id="cattoggle_${catId}" style="font-size:11px;color:var(--text-muted);">▸</span>
        </div>
      </div>
      <!-- Options (collapsed by default) -->
      <div id="${catId}" style="display:none;padding:12px 14px;background:white;
                                border-top:1px solid var(--border);display:flex;
                                flex-direction:column;gap:8px;">
        <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;
                    color:var(--text-muted);font-family:var(--font-mono);margin-bottom:4px;">
            Suggested Actions — click any option for details
        </div>
        ${optionCards}
      </div>
    </div>`;
}

function toggleCategoryCard(id) {
    const el     = document.getElementById(id);
    const toggle = document.getElementById('cattoggle_' + id);
    if (!el) return;
    const visible = el.style.display !== 'none';
    el.style.display   = visible ? 'none' : 'flex';
    toggle.textContent = visible ? '▸' : '▾';
}

// ── SWOT Popup ────────────────────────────────────────────
function openSwotPopup(optId) {
    const dataEl  = document.getElementById(optId + '_data');
    if (!dataEl) return;

    const option   = dataEl.dataset.option   || '';
    const category = dataEl.dataset.category || '';
    const why      = dataEl.dataset.why      || '';
    const pros     = JSON.parse(dataEl.dataset.pros  || '[]');
    const cons     = JSON.parse(dataEl.dataset.cons  || '[]');
    const outcome  = dataEl.dataset.outcome  || '';
    const threat   = THREAT_TEMPLATES[category] || DEFAULT_THREAT;

    const swotCell = (letter, label, color, bg, items) => `
        <div style="background:${bg};border-radius:6px;padding:12px 14px;">
            <div style="font-size:10px;font-weight:700;color:${color};
                        font-family:var(--font-mono);margin-bottom:8px;">
                <span style="font-size:14px;margin-right:4px;">${letter}</span>${label}
            </div>
            ${items.map(i => `
                <div style="display:flex;gap:6px;font-size:11px;color:var(--text-secondary);margin-bottom:4px;">
                    <span style="color:${color};flex-shrink:0;">·</span>
                    <span>${escapeHtml(i)}</span>
                </div>`).join('')}
        </div>`;

    const popup = document.createElement('div');
    popup.id    = 'swotPopup';
    popup.style.cssText = `
        position:fixed;inset:0;background:rgba(10,14,26,0.6);
        display:flex;align-items:center;justify-content:center;
        z-index:9999;padding:20px;`;
    popup.onclick = e => { if (e.target === popup) closeSwotPopup(); };

    popup.innerHTML = `
    <div style="background:white;border-radius:10px;width:min(680px,95vw);max-height:90vh;
                overflow-y:auto;box-shadow:0 20px 50px rgba(0,0,0,0.15);">

      <!-- Header -->
      <div style="display:flex;justify-content:space-between;align-items:flex-start;
                  padding:20px 24px 16px;border-bottom:1px solid var(--border);">
        <div>
          <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:0.1em;
                      color:var(--primary);font-family:var(--font-mono);margin-bottom:6px;">
              ${escapeHtml(category)}
          </div>
          <div style="font-size:14px;font-weight:700;color:var(--text-main);line-height:1.4;
                      max-width:520px;">${escapeHtml(option)}</div>
        </div>
        <button onclick="closeSwotPopup()"
            style="background:transparent;border:none;font-size:20px;color:var(--text-muted);
                   cursor:pointer;padding:0;margin-left:16px;flex-shrink:0;">&times;</button>
      </div>

      <div style="padding:20px 24px;display:flex;flex-direction:column;gap:16px;">

        <!-- Why -->
        ${why ? `
        <div style="background:#EFF6FF;border-radius:6px;padding:14px 16px;">
          <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:0.1em;
                      color:var(--primary);font-family:var(--font-mono);margin-bottom:8px;">
              💡 Why This Matters Now
          </div>
          <div style="font-size:12px;color:var(--text-secondary);line-height:1.6;">${escapeHtml(why)}</div>
        </div>` : ''}

        <!-- SWOT grid -->
        <div>
          <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:0.1em;
                      color:var(--text-muted);font-family:var(--font-mono);margin-bottom:10px;">
              SWOT Analysis
          </div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
            ${swotCell('S','Strengths',  '#166534','#DCFCE7', pros.length   ? pros   : ['Mitigates identified risk exposure'])}
            ${swotCell('W','Weaknesses', '#991B1B','#FEE2E2', cons.length   ? cons   : ['Requires resource allocation'])}
            ${swotCell('O','Opportunity','#1E40AF','#DBEAFE', outcome       ? [outcome] : ['Improved risk-adjusted positioning'])}
            ${swotCell('T','Threat',     '#92400E','#FEF3C7', [threat])}
          </div>
        </div>

      </div>

      <!-- Footer -->
      <div style="padding:14px 24px;border-top:1px solid var(--border);
                  display:flex;justify-content:flex-end;gap:10px;">
        <button onclick="closeSwotPopup()"
            style="padding:8px 20px;border:1px solid var(--border);border-radius:6px;
                   background:white;color:var(--text-secondary);font-size:12px;
                   font-weight:600;cursor:pointer;">Close</button>
      </div>
    </div>`;

    document.body.appendChild(popup);
    document.body.style.overflow = 'hidden';
}

function closeSwotPopup() {
    const popup = document.getElementById('swotPopup');
    if (popup) popup.remove();
    document.body.style.overflow = '';
}

// ── Deep-link from Risk Monitor ───────────────────────────
function _applyDeepLink() {
    const raw = localStorage.getItem('argusActionsFilter');
    if (!raw) return;
    localStorage.removeItem('argusActionsFilter');
    try {
        const { country } = JSON.parse(raw);
        if (!country) return;
        // Wait for DOM then scroll + highlight
        setTimeout(() => {
            const cardId  = 'country_' + country.replace(/\W/g,'_');
            const wrapper = document.getElementById('country_card_' + cardId);
            if (wrapper) {
                wrapper.scrollIntoView({ behavior: 'smooth', block: 'start' });
                wrapper.style.outline      = '2px solid var(--primary)';
                wrapper.style.outlineOffset = '2px';
                setTimeout(() => { wrapper.style.outline = wrapper.style.outlineOffset = ''; }, 2500);
            }
        }, 400);
    } catch(e) {}
}

// ── Skeleton loader ───────────────────────────────────────
function _skeletonCards(n) {
    const bar = (w, h=10) =>
        `<div style="width:${w};height:${h}px;border-radius:3px;margin-bottom:8px;
             background:linear-gradient(90deg,#F1F5F9 25%,#E2E8F0 50%,#F1F5F9 75%);
             background-size:800px 100%;animation:shimmer 1.4s infinite linear;"></div>`;
    return `<style>@keyframes shimmer{0%{background-position:-400px 0}100%{background-position:400px 0}}</style>` +
        Array.from({length:n}, () =>
            `<div class="card" style="margin-bottom:12px;padding:16px;">
                ${bar('40%',14)}${bar('60%')}${bar('80%')}${bar('55%')}
            </div>`).join('');
}

// ── Flag emoji ────────────────────────────────────────────
function _flagEmoji(country) {
    const map = {
        'USA':'🇺🇸','United States':'🇺🇸','UK':'🇬🇧','United Kingdom':'🇬🇧',
        'Germany':'🇩🇪','France':'🇫🇷','Japan':'🇯🇵','China':'🇨🇳',
        'India':'🇮🇳','Brazil':'🇧🇷','Canada':'🇨🇦','Australia':'🇦🇺',
        'Mexico':'🇲🇽','South Korea':'🇰🇷','Hong Kong':'🇭🇰',
        'Singapore':'🇸🇬','Switzerland':'🇨🇭','Global':'🌐',
    };
    return map[country] || '🏳';
}

// ── Page init ─────────────────────────────────────────────
function initActionsPage() {
    renderActionsSettings();
    loadActions();
}

// Patch showPage
(function patchShowPage() {
    const _orig = window.showPage;
    if (!_orig) return;
    window.showPage = function(pageId, navEl) {
        _orig(pageId, navEl);
        if (pageId === 'actions') initActionsPage();
    };
})();

// Patch savePersonaSelection to mark stale
(function patchPersona() {
    const _orig = window.savePersonaSelection;
    if (!_orig) return;
    window.savePersonaSelection = function() {
        _orig();
        markActionStale();
    };
})();

// Insert settings panel on DOM ready
document.addEventListener('DOMContentLoaded', () => {
    const actionsPage = document.getElementById('page-actions');
    if (!actionsPage) return;
    const container = document.getElementById('actionsContainer');
    if (!container) return;

    const settingsDiv = document.createElement('div');
    settingsDiv.innerHTML = `
    <div class="card" style="padding:0;overflow:hidden;margin-bottom:12px;">
      <div style="display:flex;justify-content:space-between;align-items:center;
                  padding:10px 16px;background:#F8FAFC;border-bottom:1px solid var(--border);
                  cursor:pointer;" onclick="toggleActionsSettings()">
        <div style="font-size:9px;font-weight:700;text-transform:uppercase;
                    letter-spacing:0.1em;color:var(--text-muted);font-family:var(--font-mono);">
            ⚙ Generation Settings
        </div>
        <span id="actionsSettingsToggle" style="font-size:11px;color:var(--text-muted);">▾ Settings</span>
      </div>
      <div id="actionsSettingsInner">
        <div id="actionsSettingsPanel"></div>
      </div>
    </div>`;
    actionsPage.insertBefore(settingsDiv, container);
});