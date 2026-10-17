/* ============================================================
   ARGUS — Tune Strategy Modal (live risk-weight preview)
   ============================================================ */

const TUNE_CATS = [
    // Left column
    { key: 'market_risk',      label: 'Market',      col: 0, group: 'Financial'    },
    { key: 'credit_risk',      label: 'Credit',      col: 0, group: 'Financial'    },
    { key: 'liquidity_risk',   label: 'Liquidity',   col: 0, group: 'Financial'    },
    { key: 'systematic_risk',  label: 'Systematic',  col: 0, group: 'Financial'    },
    { key: 'operational_risk', label: 'Operational', col: 0, group: 'Operational'  },
    { key: 'process_risk',     label: 'Process',     col: 0, group: 'Operational'  },
    { key: 'business_risk',    label: 'Business',    col: 0, group: 'Operational'  },
    // Right column
    { key: 'regulatory_risk',  label: 'Regulatory',  col: 1, group: 'Geopolitical' },
    { key: 'political_risk',   label: 'Political',   col: 1, group: 'Geopolitical' },
    { key: 'reputation_risk',  label: 'Reputation',  col: 1, group: 'Geopolitical' },
    { key: 'disaster_risk',    label: 'Disaster',    col: 1, group: 'Environment'  },
    { key: 'supply_risk',      label: 'Supply',      col: 1, group: 'Environment'  },
    { key: 'demand_risk',      label: 'Demand',      col: 1, group: 'Environment'  },
];

// Maps guardian alert .category → weight key
const TUNE_CAT_MAP = {
    'market risk':      'market_risk',
    'credit risk':      'credit_risk',
    'liquidity risk':   'liquidity_risk',
    'operational risk': 'operational_risk',
    'political risk':   'political_risk',
    'regulatory risk':  'regulatory_risk',
    'reputation risk':  'reputation_risk',
    'systematic risk':  'systematic_risk',
    'systemic risk':    'systematic_risk',
    'disaster risk':    'disaster_risk',
    'supply risk':      'supply_risk',
    'demand risk':      'demand_risk',
    'process risk':     'process_risk',
    'business risk':    'business_risk',
};

const TUNE_REGIONS = [
    { key: 'global',  label: 'Global'  },
    { key: 'us',      label: 'US'      },
    { key: 'canada',  label: 'Canada'  },
    { key: 'mexico',  label: 'Mexico'  },
    { key: 'china',   label: 'China'   },
    { key: 'japan',   label: 'Japan'   },
    { key: 'uk',      label: 'UK'      },
    { key: 'korea',   label: 'Korea'   },
    { key: 'india',   label: 'India'   },
];

let tsPersona  = localStorage.getItem('argusPersona') || 'cash_manager';
let tsRegion   = localStorage.getItem('argusRegion')  || 'global';
let tsDefaults = {};
let tsDraft    = {};
let _tuneRafId = null;

// Composite draft key: persona + region
function _tuneKey(persona, region) { return persona + '__' + (region || 'global'); }

// ── Open / Close ──────────────────────────────────────────────

async function openTuneModal() {
    const riskNavEl = document.querySelector('.nav-item[onclick*="risk"]');
    showPage('risk', riskNavEl);

    if (!Object.keys(tsDefaults).length) await tuneLoadDefaults();

    tsPersona = localStorage.getItem('argusPersona') || 'cash_manager';
    tsRegion  = localStorage.getItem('argusRegion')  || 'global';
    tuneRenderPersonaBar();
    tuneRenderRegionBar();
    tuneRenderSliders(tsPersona, tsRegion);
    await tuneRefreshProfileList();

    document.getElementById('tuneOverlay').classList.add('open');
    cancelAnimationFrame(_tuneRafId);
    _tuneRafId = requestAnimationFrame(() => { void tunePreviewLive(); });
}

function closeTuneModal() {
    document.getElementById('tuneOverlay').classList.remove('open');
    cancelAnimationFrame(_tuneRafId);
}

function handleTuneOverlayClick(e) {
    if (e.target === document.getElementById('tuneOverlay')) closeTuneModal();
}

// ── Load defaults ─────────────────────────────────────────────

async function tuneLoadDefaults() {
    try {
        const r = await fetch(`${API_BASE}/personas`);
        const d = await r.json();
        tsDefaults = {};
        tsDraft    = {};
        (d.personas || []).forEach(p => {
            const globalBase = { alert_threshold: p.alert_threshold ?? 40, risk_weights: p.risk_weights || {} };
            TUNE_REGIONS.forEach(reg => {
                const k = _tuneKey(p.key, reg.key);
                // Use region-specific defaults when available, else fall back to global
                const regionCfg = p.region_defaults?.[reg.key];
                const base = regionCfg && regionCfg.risk_weights
                    ? { alert_threshold: regionCfg.alert_threshold ?? globalBase.alert_threshold, risk_weights: regionCfg.risk_weights }
                    : globalBase;
                tsDefaults[k] = base;
                tsDraft[k]    = JSON.parse(JSON.stringify(base));
            });
        });
    } catch(e) { console.warn('tuneLoadDefaults error:', e); }
}

// ── Persona pills ─────────────────────────────────────────────

function tuneRenderPersonaBar() {
    const bar = document.getElementById('tunePersonaBar');
    bar.innerHTML = '';
    (availablePersonas || []).forEach(p => {
        const pill = document.createElement('button');
        pill.className  = 'tune-pill' + (p.key === tsPersona ? ' active' : '');
        pill.textContent = p.label;
        pill.onclick = () => {
            tsPersona = p.key;
            bar.querySelectorAll('.tune-pill').forEach(b => b.classList.remove('active'));
            pill.classList.add('active');
            tuneRenderSliders(p.key, tsRegion);
            cancelAnimationFrame(_tuneRafId);
            _tuneRafId = requestAnimationFrame(() => { void tunePreviewLive(); });
            if (typeof markActionsStale === 'function') markActionsStale();
        };
        bar.appendChild(pill);
    });
}

// ── Region pills ──────────────────────────────────────────────

function tuneRenderRegionBar() {
    const bar = document.getElementById('tuneRegionBar');
    if (!bar) return;
    bar.innerHTML = '<span class="tune-region-label">Region</span>';
    TUNE_REGIONS.forEach(reg => {
        const pill = document.createElement('button');
        pill.className   = 'tune-region-pill' + (reg.key === tsRegion ? ' active' : '');
        pill.textContent = reg.label;
        pill.onclick = () => {
            tsRegion = reg.key;
            localStorage.setItem('argusRegion', tsRegion);
            bar.querySelectorAll('.tune-region-pill').forEach(b => b.classList.remove('active'));
            pill.classList.add('active');
            tuneRenderSliders(tsPersona, tsRegion);
            cancelAnimationFrame(_tuneRafId);
            _tuneRafId = requestAnimationFrame(() => { void tunePreviewLive(); });
        };
        bar.appendChild(pill);
    });
}

// ── Slider rendering ──────────────────────────────────────────

function tuneRenderSliders(personaKey, regionKey) {
    regionKey    = regionKey || tsRegion || 'global';
    const dk     = _tuneKey(personaKey, regionKey);
    const cfg    = tsDraft[dk]    || {};
    const def    = tsDefaults[dk] || {};

    const thresh    = cfg.alert_threshold ?? 40;
    const defThresh = def.alert_threshold ?? 40;
    const thSlider  = document.getElementById('tuneThresholdSlider');
    const thVal     = document.getElementById('tuneThresholdVal');
    thSlider.value       = thresh;
    thVal.textContent    = thresh;
    thSlider.className   = 'tune-range tune-thresh-range' + (thresh !== defThresh ? ' modified' : '');
    thSlider.oninput = () => {
        const v = parseInt(thSlider.value, 10);
        thVal.textContent = v;
        if (!tsDraft[dk]) tsDraft[dk] = {};
        tsDraft[dk].alert_threshold = v;
        thSlider.className = 'tune-range tune-thresh-range' + (v !== defThresh ? ' modified' : '');
        cancelAnimationFrame(_tuneRafId);
        _tuneRafId = requestAnimationFrame(() => { void tunePreviewLive(); });
    };

    const leftEl  = document.getElementById('tuneColLeft');
    const rightEl = document.getElementById('tuneColRight');
    leftEl.innerHTML  = '';
    rightEl.innerHTML = '';
    let lastGroupL = null, lastGroupR = null;

    TUNE_CATS.forEach(cat => {
        const weights    = cfg.risk_weights || {};
        const defWeights = def.risk_weights || {};
        const val        = typeof weights[cat.key]    === 'number' ? weights[cat.key]    : 1.0;
        const defVal     = typeof defWeights[cat.key] === 'number' ? defWeights[cat.key] : 1.0;
        const modified   = Math.abs(val - defVal) > 0.005;

        const target    = cat.col === 0 ? leftEl : rightEl;
        let   lastGroup = cat.col === 0 ? lastGroupL : lastGroupR;

        if (cat.group !== lastGroup) {
            const g = document.createElement('div');
            g.className   = 'tune-group';
            g.textContent = cat.group;
            target.appendChild(g);
            if (cat.col === 0) lastGroupL = cat.group;
            else               lastGroupR = cat.group;
        }

        const row = document.createElement('div');
        row.className       = 'tune-row';
        row.dataset.catKey  = cat.key;
        row.innerHTML = `
            <span class="tune-cat">${cat.label}</span>
            <input type="range" class="tune-range${modified ? ' modified' : ''}"
                id="tr-${cat.key}" min="0.5" max="2.0" step="0.05" value="${val.toFixed(2)}">
            <div class="tune-val-wrap">
                <span class="tune-val${modified ? ' changed' : ''}" id="tv-${cat.key}">${val.toFixed(2)}×</span>
                <span class="tune-mod-dot${modified ? ' on' : ''}" id="td-${cat.key}"></span>
            </div>`;

        const slider = row.querySelector('.tune-range');
        const valEl  = row.querySelector('.tune-val');
        const dotEl  = row.querySelector('.tune-mod-dot');

        slider.addEventListener('input', () => {
            const v       = parseFloat(slider.value);
            const changed = Math.abs(v - defVal) > 0.005;
            valEl.textContent = v.toFixed(2) + '×';
            valEl.classList.toggle('changed', changed);
            slider.classList.toggle('modified', changed);
            dotEl.classList.toggle('on', changed);
            if (!tsDraft[dk].risk_weights) tsDraft[dk].risk_weights = {};
            tsDraft[dk].risk_weights[cat.key] = v;
            cancelAnimationFrame(_tuneRafId);
            _tuneRafId = requestAnimationFrame(() => { void tunePreviewLive(); });
            if (typeof markActionsStale === 'function') markActionsStale();
        });

        target.appendChild(row);
    });
}

// ── Live preview ──────────────────────────────────────────────

async function tunePreviewLive() {
    const cfg = tsDraft[_tuneKey(tsPersona, tsRegion)] || {};

    if (guardianBaseAlerts && guardianBaseAlerts.length) {
        try {
            const r = await fetch(`${API_BASE}/guardian/news-alerts/rescore`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    persona_key: tsPersona,
                    region_key: tsRegion,
                    weights: cfg,
                }),
            });
            const d = await r.json();
            if (d.status === 'success') {
                guardianAdjustedActive = true;
                guardianTopAlerts = d.alerts || d.critical_alerts_top5 || [];
                const newsCountBadge = document.getElementById('newsAlertCount');
                if (newsCountBadge) {
                    const nc = guardianTopAlerts.length;
                    newsCountBadge.textContent  = nc + ' Alert' + (nc !== 1 ? 's' : '');
                    newsCountBadge.style.background = nc > 0 ? 'var(--warning-bg)' : 'var(--idle-bg)';
                    newsCountBadge.style.color      = nc > 0 ? 'var(--warning-text)' : 'var(--idle-text)';
                }
                if (_guardianCountryFilter) _applyCountryFilter(_guardianCountryFilter);
                else renderGuardianAlerts(guardianTopAlerts, true);
            }
        } catch(e) {
            console.warn('tunePreviewLive guardian rescore error:', e);
        }
    }

    if (window._lastScoredItems && window._lastScoredItems.length) {
        const weights = cfg.risk_weights || {};
        const recolored = window._lastScoredItems.map(item => {
            const wKey = TUNE_CAT_MAP[String(item.risk_category || '').toLowerCase().trim()] || '';
            const w    = typeof weights[wKey] === 'number' ? weights[wKey] : 1.0;
            const eff  = Math.min(100, parseFloat(item.risk_score || 0) * w);
            let lvl = 'Low';
            if (eff >= 60) lvl = 'High';
            else if (eff >= 35) lvl = 'Medium';
            return { ...item, _effScore: eff, _effLevel: lvl };
        });
        recolored.sort((a, b) => b._effScore - a._effScore);
        tuneUpdateMatrixCards(recolored);
    }
}

function tuneUpdateMatrixCards(items) {
    const container = document.getElementById('riskMatrixItems');
    if (!container) return;
    const levelStyle = {
        'High':   { border: '#EF4444', badge: '#991B1B', badgeBg: '#FEE2E2' },
        'Medium': { border: '#F59E0B', badge: '#92400E', badgeBg: '#FEF3C7' },
        'Low':    { border: '#22C55E', badge: '#166534', badgeBg: '#DCFCE7' },
    };
    items.forEach((item, i) => {
        const card = container.querySelector(`[data-item-idx="${i}"]`);
        if (!card) return;
        const lvl   = item._effLevel || item.matrix_level || 'Low';
        const style = levelStyle[lvl] || levelStyle['Low'];
        card.style.borderColor = style.border;
        const badge = card.querySelector('.tune-lvl-badge');
        if (badge) {
            badge.textContent      = lvl;
            badge.style.background = style.badgeBg;
            badge.style.color      = style.badge;
        }
    });
}

// ── Profile save / load / reset ───────────────────────────────

async function tuneRefreshProfileList() {
    try {
        const r = await fetch(`${API_BASE}/list_profiles`);
        const d = await r.json();
        const sel = document.getElementById('tuneProfileSelect');
        if (!sel) return;
        sel.innerHTML = '<option value="">Load saved profile…</option>' +
            (d.profiles || []).map(p =>
                `<option value="${p.filename}">${p.name}${p.saved_at ? '  ·  ' + p.saved_at.slice(0, 10) : ''}</option>`
            ).join('');
    } catch(e) { console.warn('tuneRefreshProfileList error:', e); }
}

async function tuneSaveProfile() {
    const name = (document.getElementById('tuneProfileName').value || '').trim();
    if (!name) { tuneFeedback('Enter a profile name first.', 'error'); return; }
    const btn = document.querySelector('.tune-save-btn');
    btn.disabled = true;
    try {
        const r = await fetch(`${API_BASE}/save_profile`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name,
                persona_key: tsPersona,
                region_key:  tsRegion,
                weights: tsDraft[_tuneKey(tsPersona, tsRegion)] || {},
            }),
        });
        const d = await r.json();
        if (d.status === 'success') {
            document.getElementById('tuneProfileName').value = '';
            tuneFeedback(`"${name}" saved.`, 'success');
            await tuneRefreshProfileList();
        } else { tuneFeedback('Save failed: ' + (d.message || 'error'), 'error'); }
    } catch(e) { tuneFeedback('Save failed: ' + e.message, 'error'); }
    finally    { btn.disabled = false; }
}

async function tuneLoadProfile() {
    const sel      = document.getElementById('tuneProfileSelect');
    const filename = sel ? sel.value : '';
    if (!filename) { tuneFeedback('Select a profile first.', 'error'); return; }

    const btn = document.querySelector('.tune-load-btn');
    if (btn) { btn.disabled = true; btn.textContent = '…'; }
    try {
        const r = await fetch(`${API_BASE}/load_profile`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ filename }),
        });
        const d = await r.json();
        if (d.status !== 'success') { tuneFeedback('Load failed: ' + (d.message || 'error'), 'error'); return; }

        const profile = d.profile || {};
        const pKey    = profile.persona_key || tsPersona;
        const rKey    = profile.region_key  || tsRegion || 'global';
        const weights = profile.weights     || {};
        tsPersona = pKey;
        tsRegion  = rKey;
        localStorage.setItem('argusRegion', tsRegion);

        const dk = _tuneKey(pKey, rKey);
        if (!tsDraft[dk]) tsDraft[dk] = {};
        if (weights.alert_threshold !== undefined) tsDraft[dk].alert_threshold = weights.alert_threshold;
        if (weights.risk_weights) tsDraft[dk].risk_weights = Object.assign(tsDraft[dk].risk_weights || {}, weights.risk_weights);

        tuneRenderPersonaBar();
        tuneRenderRegionBar();
        tuneRenderSliders(pKey, rKey);
        if (typeof markActionsStale === 'function') markActionsStale();

        if (d.rescored) {
            renderRiskMatrix(d.rescored);
            tuneFeedback(`"${profile.name}" loaded · ${d.rescored.total_items} items re-scored.`, 'success');
        } else {
            tuneFeedback(`"${profile.name}" loaded · run Daily Jobs to apply to new data.`, 'success');
        }
    } catch(e) { tuneFeedback('Load failed: ' + e.message, 'error'); }
    finally    { if (btn) { btn.disabled = false; btn.textContent = 'Load'; } }
}

function tuneReset() {
    const dk  = _tuneKey(tsPersona, tsRegion);
    const def = tsDefaults[dk];
    if (def) tsDraft[dk] = JSON.parse(JSON.stringify(def));
    tuneRenderSliders(tsPersona, tsRegion);
    guardianAdjustedActive = false;
    guardianTopAlerts = guardianBaseAlerts.slice();
    const newsCountBadge = document.getElementById('newsAlertCount');
    if (newsCountBadge) {
        const nc = guardianTopAlerts.length;
        newsCountBadge.textContent  = nc + ' Alert' + (nc !== 1 ? 's' : '');
        newsCountBadge.style.background = nc > 0 ? 'var(--warning-bg)' : 'var(--idle-bg)';
        newsCountBadge.style.color      = nc > 0 ? 'var(--warning-text)' : 'var(--idle-text)';
    }
    if (_guardianCountryFilter) _applyCountryFilter(_guardianCountryFilter);
    else if (guardianTopAlerts.length) renderGuardianAlerts(guardianTopAlerts);
    tuneFeedback('Sliders reset to AI defaults.', 'success');
}

function tuneFeedback(msg, type) {
    const el = document.getElementById('tuneFeedback');
    if (!el) return;
    el.textContent   = msg;
    el.style.display = 'block';
    el.className     = `tune-feedback tune-feedback-${type}`;
    clearTimeout(el._t);
    el._t = setTimeout(() => { el.style.display = 'none'; }, 5000);
}
