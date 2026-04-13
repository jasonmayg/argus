/* ============================================================
   ARGUS — Persona Settings Page (full weight editor)
   ============================================================ */

const PS_CATEGORIES = [
    { key: 'market_risk',      label: 'Market',      group: 'Financial'    },
    { key: 'credit_risk',      label: 'Credit',      group: 'Financial'    },
    { key: 'liquidity_risk',   label: 'Liquidity',   group: 'Financial'    },
    { key: 'systematic_risk',  label: 'Systematic',  group: 'Financial'    },
    { key: 'operational_risk', label: 'Operational', group: 'Operational'  },
    { key: 'process_risk',     label: 'Process',     group: 'Operational'  },
    { key: 'business_risk',    label: 'Business',    group: 'Operational'  },
    { key: 'regulatory_risk',  label: 'Regulatory',  group: 'Geopolitical' },
    { key: 'political_risk',   label: 'Political',   group: 'Geopolitical' },
    { key: 'reputation_risk',  label: 'Reputation',  group: 'Geopolitical' },
    { key: 'disaster_risk',    label: 'Disaster',    group: 'Environment'  },
    { key: 'supply_risk',      label: 'Supply',      group: 'Environment'  },
    { key: 'demand_risk',      label: 'Demand',      group: 'Environment'  },
];

let psActivePersona = localStorage.getItem('argusPersona') || 'cash_manager';
let psDefaults      = {};
let psDraft         = {};

async function loadPersonaSettings() {
    try {
        const r = await fetch(`${API_BASE}/personas`);
        const d = await r.json();
        const personas = d.personas || [];

        psDefaults = {};
        personas.forEach(p => {
            psDefaults[p.key] = {
                alert_threshold: p.alert_threshold ?? 40,
                risk_weights:    p.risk_weights || {},
            };
        });
        psDraft = JSON.parse(JSON.stringify(psDefaults));

        psRenderTabs(personas);
        psRenderSliders(psActivePersona);
        psRefreshProfileList();
    } catch(e) {
        console.warn('Persona settings load error:', e);
    }
}

function psRenderTabs(personas) {
    const nav = document.getElementById('psNav');
    nav.innerHTML = '<div class="ps-nav-label">ROLE</div>';
    personas.forEach(p => {
        const btn = document.createElement('button');
        btn.className = 'ps-tab' + (p.key === psActivePersona ? ' active' : '');
        btn.innerHTML = `<span class="ps-tab-name">${p.label}</span><span class="ps-tab-desc">${p.summary || ''}</span>`;
        btn.onclick = () => {
            psActivePersona = p.key;
            nav.querySelectorAll('.ps-tab').forEach(t => t.classList.remove('active'));
            btn.classList.add('active');
            psRenderSliders(p.key);
        };
        nav.appendChild(btn);
    });
}

function psRenderSliders(personaKey) {
    const cfg      = psDraft[personaKey]    || {};
    const defaults = psDefaults[personaKey] || {};

    const meta = (availablePersonas || []).find(p => p.key === personaKey);
    const titleEl = document.getElementById('psTitle');
    if (meta && titleEl) titleEl.textContent = meta.label + ' — Weight Configuration';

    // Threshold slider
    const threshold    = cfg.alert_threshold ?? 40;
    const defThreshold = defaults.alert_threshold ?? 40;
    const thSlider     = document.getElementById('psThresholdSlider');
    const thVal        = document.getElementById('psThresholdVal');
    thSlider.value     = threshold;
    thVal.textContent  = threshold;
    thSlider.className = 'ps-slider' + (threshold !== defThreshold ? ' modified' : '');
    thSlider.oninput   = () => {
        const v = parseInt(thSlider.value, 10);
        thVal.textContent = v;
        if (!psDraft[personaKey]) psDraft[personaKey] = {};
        psDraft[personaKey].alert_threshold = v;
        thSlider.className = 'ps-slider' + (v !== defThreshold ? ' modified' : '');
    };

    // Category sliders
    const grid = document.getElementById('psSliderGrid');
    grid.innerHTML = '';
    let lastGroup = null;

    PS_CATEGORIES.forEach(cat => {
        const weights    = cfg.risk_weights      || {};
        const defWeights = defaults.risk_weights || {};
        const val     = typeof weights[cat.key]    === 'number' ? weights[cat.key]    : 1.0;
        const defVal  = typeof defWeights[cat.key] === 'number' ? defWeights[cat.key] : 1.0;
        const modified = Math.abs(val - defVal) > 0.005;

        if (cat.group !== lastGroup) {
            const g = document.createElement('div');
            g.className   = 'ps-group-label';
            g.textContent = cat.group;
            grid.appendChild(g);
            lastGroup = cat.group;
        }

        const pct = ((val - 0.5) / (2.0 - 0.5)) * 100;
        const row = document.createElement('div');
        row.className = 'ps-row';
        row.innerHTML = `
            <span class="ps-cat-label">${cat.label}</span>
            <div class="ps-slider-wrap">
                <input type="range" class="ps-slider${modified ? ' modified' : ''}"
                    id="ps-${cat.key}" min="0.5" max="2.0" step="0.05"
                    value="${val.toFixed(2)}" style="--pct:${pct.toFixed(1)}%">
                <div class="ps-track-markers">
                    <span>0.5×</span><span>1.0×</span><span>1.5×</span><span>2.0×</span>
                </div>
            </div>
            <div class="ps-value-col ps-def-tip" data-tip="AI default: ${defVal.toFixed(2)}×">
                <span class="ps-multiplier" id="ps-val-${cat.key}">${val.toFixed(2)}×</span>
                <span class="ps-mod-badge${modified ? ' visible' : ''}" id="ps-mod-${cat.key}">MOD</span>
            </div>`;

        const slider   = row.querySelector('.ps-slider');
        const valEl    = row.querySelector('.ps-multiplier');
        const modBadge = row.querySelector('.ps-mod-badge');

        slider.addEventListener('input', () => {
            const v       = parseFloat(slider.value);
            const pctNew  = ((v - 0.5) / (2.0 - 0.5)) * 100;
            const changed = Math.abs(v - defVal) > 0.005;
            slider.style.setProperty('--pct', pctNew.toFixed(1) + '%');
            valEl.textContent = v.toFixed(2) + '×';
            slider.classList.toggle('modified', changed);
            modBadge.classList.toggle('visible', changed);
            if (!psDraft[personaKey].risk_weights) psDraft[personaKey].risk_weights = {};
            psDraft[personaKey].risk_weights[cat.key] = v;
        });

        grid.appendChild(row);
    });
}

function resetPersonaWeights() {
    const defaults = psDefaults[psActivePersona];
    if (defaults) psDraft[psActivePersona] = JSON.parse(JSON.stringify(defaults));
    psRenderSliders(psActivePersona);
    psShowFeedback('Sliders reset to AI defaults. Use "Save Profile" to persist a named configuration.', 'success');
}

function psShowFeedback(msg, type) {
    const el = document.getElementById('psFeedback');
    el.textContent = msg;
    el.style.display = 'block';
    el.className = `ps-feedback ps-feedback-${type}`;
    clearTimeout(el._timer);
    el._timer = setTimeout(() => { el.style.display = 'none'; }, 5000);
}

function psProfileFeedback(msg, type) {
    const el = document.getElementById('psProfileFeedback');
    el.textContent = msg;
    el.style.display = 'block';
    el.className = `ps-feedback ps-feedback-${type}`;
    clearTimeout(el._timer);
    el._timer = setTimeout(() => { el.style.display = 'none'; }, 6000);
}

async function psRefreshProfileList() {
    try {
        const r = await fetch(`${API_BASE}/list_profiles`);
        const d = await r.json();
        const sel = document.getElementById('psProfileSelect');
        const profiles = d.profiles || [];
        sel.innerHTML = '<option value="">— select a saved profile —</option>' +
            profiles.map(p => {
                const label = p.name + (p.saved_at ? '  ·  ' + p.saved_at.slice(0, 10) : '');
                return `<option value="${p.filename}">${label}</option>`;
            }).join('');
    } catch(e) { console.warn('Could not load profiles:', e); }
}

async function psSaveCurrentAsProfile() {
    const name = (document.getElementById('psProfileNameInput').value || '').trim();
    if (!name) { psProfileFeedback('Enter a profile name first.', 'error'); return; }
    const cfg = psDraft[psActivePersona] || {};
    try {
        const r = await fetch(`${API_BASE}/save_profile`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, persona_key: psActivePersona, weights: cfg }),
        });
        const d = await r.json();
        if (d.status === 'success') {
            document.getElementById('psProfileNameInput').value = '';
            psProfileFeedback(`Profile "${name}" saved.`, 'success');
            await psRefreshProfileList();
        } else {
            psProfileFeedback('Save failed: ' + (d.message || 'unknown'), 'error');
        }
    } catch(e) {
        psProfileFeedback('Save failed: ' + e.message, 'error');
    }
}

async function psLoadSelectedProfile() {
    const sel      = document.getElementById('psProfileSelect');
    const filename = sel.value;
    if (!filename) { psProfileFeedback('Select a profile first.', 'error'); return; }

    const btn = document.querySelector('.ps-profile-load-btn');
    btn.disabled    = true;
    btn.textContent = 'Loading…';

    try {
        const r = await fetch(`${API_BASE}/load_profile`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ filename }),
        });
        const d = await r.json();
        if (d.status !== 'success') { psProfileFeedback('Load failed: ' + (d.message || 'unknown'), 'error'); return; }

        const profile = d.profile || {};
        const weights = profile.weights || {};
        const pKey    = profile.persona_key || psActivePersona;
        psActivePersona = pKey;

        document.querySelectorAll('.ps-tab').forEach(t => t.classList.remove('active'));
        const matchingTab = [...document.querySelectorAll('.ps-tab')].find(t =>
            t.textContent.trim().startsWith((availablePersonas.find(p => p.key === pKey) || {}).label || '')
        );
        if (matchingTab) matchingTab.classList.add('active');

        if (!psDraft[pKey]) psDraft[pKey] = {};
        if (weights.alert_threshold !== undefined) psDraft[pKey].alert_threshold = weights.alert_threshold;
        if (weights.risk_weights) psDraft[pKey].risk_weights = Object.assign(psDraft[pKey].risk_weights || {}, weights.risk_weights);

        psRenderSliders(pKey);

        if (d.rescored) {
            renderRiskMatrix(d.rescored);
            psProfileFeedback(`Profile "${profile.name}" loaded. Risk matrix re-scored with ${d.rescored.total_items} items.`, 'success');
        } else {
            psProfileFeedback(`Profile "${profile.name}" loaded. Run the Daily Jobs pipeline to apply weights to new data.`, 'success');
        }
    } catch(e) {
        psProfileFeedback('Load failed: ' + e.message, 'error');
    } finally {
        btn.disabled    = false;
        btn.textContent = 'Load & Re-score';
    }
}
