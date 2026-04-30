/* ============================================================
   ARGUS — Pipeline Results & Risk Matrix
   ============================================================ */

const PROB_LABELS   = ['Unlikely','Seldom','Occasional','Likely','Frequent'];
const IMPACT_LABELS = ['Negligible','Minor','Moderate','Hazardous','Catastrophic'];
const MATRIX_LEVEL  = [
    ['Low','Low','Low','Medium','Medium'],
    ['Low','Low','Medium','Medium','High'],
    ['Low','Medium','Medium','High','High'],
    ['Medium','Medium','High','High','Extreme'],
    ['Medium','High','High','Extreme','Extreme'],
];

// Active matrix cell filter: { p, i } (1-indexed) or null = show all
let _matrixFilter = null;

// ── Risk Level calculation ─────────────────────────────────

const RISK_LEVELS = [
    {
        label: 'CRITICAL',
        color: 'var(--danger-text)',
        bg:    'var(--danger-bg)',
        border:'#EF4444',
        rule:  'Any signal score ≥ 75, or 3+ High alerts',
        test:  (scores, highCount) => scores.some(s => s >= 75) || highCount >= 3,
    },
    {
        label: 'ELEVATED',
        color: '#92400E',
        bg:    '#FEF3C7',
        border:'#F59E0B',
        rule:  'Any signal score ≥ 55, or 1+ High alert',
        test:  (scores, highCount) => scores.some(s => s >= 55) || highCount >= 1,
    },
    {
        label: 'MODERATE',
        color: '#1E40AF',
        bg:    '#EFF6FF',
        border:'#3B82F6',
        rule:  'Any signal score ≥ 40, or 3+ Medium alerts',
        test:  (scores, highCount, medCount) => scores.some(s => s >= 40) || medCount >= 3,
    },
    {
        label: 'CONTAINED',
        color: 'var(--success-text)',
        bg:    'var(--success-bg)',
        border:'#22C55E',
        rule:  'No significant signals detected',
        test:  () => true,   // fallback
    },
];

function _computeRiskLevel(analysis, guardianData) {
    // Collect all scores from pipeline scored items
    const scored   = analysis?.scored_items || [];
    const scores   = scored.map(i => i.matrix_score || 0);

    // Also pull scores from guardian alerts if available
    const alerts   = guardianData?.alerts || [];
    alerts.forEach(a => { if (a.score) scores.push(a.score); });

    const highCount = alerts.filter(a => a.risk_level === 'High').length;
    const medCount  = alerts.filter(a => a.risk_level === 'Medium').length;

    // Find the highest matching level
    for (const level of RISK_LEVELS) {
        if (level.test(scores, highCount, medCount)) {
            // Build trigger reason
            const maxScore = scores.length ? Math.max(...scores) : 0;
            const triggers = [];
            if (maxScore >= 40)   triggers.push(`Max signal score: ${maxScore.toFixed(0)}`);
            if (highCount > 0)    triggers.push(`${highCount} High alert${highCount > 1 ? 's' : ''}`);
            if (medCount  > 0)    triggers.push(`${medCount} Medium alert${medCount > 1 ? 's' : ''}`);

            // Top triggering signal title
            const topItem = scored.sort((a,b) => (b.matrix_score||0) - (a.matrix_score||0))[0];

            return { ...level, triggers, topItem, maxScore, highCount, medCount };
        }
    }
    return { ...RISK_LEVELS[3], triggers: [], topItem: null, maxScore: 0, highCount: 0, medCount: 0 };
}

function _renderRiskLevelCard(levelData) {
    const el = document.getElementById('kpiRisk');
    if (!el) return;

    const { label, color, bg, border, rule, triggers, topItem } = levelData;

    // Divider line style
    const divider = `border-top:1px solid rgba(0,0,0,0.08);margin:8px 0;padding-top:8px;`;

    // Trigger bullets
    const triggerHtml = triggers.length
        ? triggers.map(t => `<div style="display:flex;gap:5px;margin-bottom:3px;">
                <span style="color:${color};font-weight:700;">·</span>
                <span>${escapeHtml(t)}</span>
            </div>`).join('')
        : `<div style="color:var(--text-muted);">No significant signals</div>`;

    // Top signal (truncated)
    const topSignalHtml = topItem
        ? `<div style="${divider}font-size:9px;font-weight:700;text-transform:uppercase;
                        letter-spacing:0.08em;color:var(--text-muted);margin-bottom:4px;">
                Top Signal
            </div>
            <div style="font-size:10px;color:var(--text-secondary);line-height:1.4;
                        display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;">
                ${escapeHtml(topItem.title || '')}
            </div>`
        : '';

    // Level legend row
    const legendHtml = RISK_LEVELS.map(l =>
        `<span style="font-size:9px;font-family:var(--font-mono);
                      color:${l.label === label ? l.color : 'var(--text-muted)'};
                      font-weight:${l.label === label ? '700' : '400'};">
            ${l.label}
        </span>`
    ).join('<span style="color:var(--border);margin:0 3px;">|</span>');

    // Replace kpiRisk content — card wrapper is in dashboard.html
    el.innerHTML = `
        <!-- Level badge -->
        <div style="font-size:20px;font-weight:700;color:${color};
                    font-family:var(--font-mono);letter-spacing:0.04em;
                    margin-bottom:6px;">
            ${label}
        </div>

        <!-- Legend row -->
        <div style="display:flex;align-items:center;flex-wrap:wrap;gap:2px;margin-bottom:10px;">
            ${legendHtml}
        </div>

        <!-- Divider -->
        <div style="${divider}"></div>

        <!-- Trigger reason -->
        <div style="font-size:9px;font-weight:700;text-transform:uppercase;
                    letter-spacing:0.08em;color:var(--text-muted);
                    font-family:var(--font-mono);margin-bottom:6px;">
            ⚠ Trigger Reason
        </div>
        <div style="font-size:11px;color:var(--text-secondary);line-height:1.5;">
            ${triggerHtml}
        </div>

        <!-- Top signal -->
        ${topSignalHtml}

        <!-- Rule description -->
        <div style="${divider}font-size:9px;color:var(--text-muted);
                    font-style:italic;line-height:1.4;">
            Rule: ${escapeHtml(rule)}
        </div>`;

    // Also update the card border to match level color
    const card = el.closest('[style*="border"]') || el.parentElement;
    if (card) {
        card.style.borderTop = `3px solid ${border}`;
    }
}

// ── Load Results (updated) ────────────────────────────────

async function loadResults() {
    try {
        const r = await fetch(`${API_BASE}/results`);
        const d = await r.json();
        if (d.status !== 'success') return;

        const count      = d.stages.news_gathering?.count || 0;
        const kpiSignals = document.getElementById('kpiSignals');
        if (kpiSignals) kpiSignals.textContent = count;

        const now = new Date().toISOString().replace('T', ' ').substr(0, 16);
        const lastRunEl = document.getElementById('homeLastRun');
        if (lastRunEl) lastRunEl.textContent = 'Last run: ' + now + ' UTC';

        const riskBadge = document.getElementById('riskBadge');
        if (riskBadge) riskBadge.style.display = '';

        // Fetch guardian data for alert counts
        let guardianData = {};
        try {
            const gr = await fetch(`${API_BASE}/guardian/dashboard`);
            const gd = await gr.json();
            if (gd.status === 'success') guardianData = gd;
        } catch(e) { /* guardian unavailable */ }

        // Compute and render risk level card
        const analysis   = d.stages.analysis?.analysis;
        const levelData  = _computeRiskLevel(analysis, guardianData);
        _renderRiskLevelCard(levelData);

        await renderRiskMatrix(analysis);
    } catch(e) {
        log('Error loading results: ' + e.message, 'error');
    }
}

// ── Risk Matrix (unchanged) ───────────────────────────────

async function renderRiskMatrix(analysis) {
    if (!analysis) return;
    document.getElementById('riskEmptyState').style.display   = 'none';
    document.getElementById('riskMatrixSection').style.display = 'block';

    const gridEl  = document.getElementById('riskMatrixGrid');
    const itemsEl = document.getElementById('riskMatrixItems');
    const summary = analysis.matrix_summary;

    let gcsItems = [];
    try {
        const gr = await fetch('/api/data/alerts/matrix-items');
        const gd = await gr.json();
        if (gd.status === 'success') gcsItems = gd.items || [];
    } catch(e) { /* GCS alerts unavailable */ }

    const newsScored = analysis.scored_items || [];
    const allScored  = [...newsScored, ...gcsItems];

    const _lvlOrder = { 'Extreme': 0, 'High': 1, 'Medium': 2, 'Low': 3 };
    allScored.sort((a, b) => {
        const la = _lvlOrder[a.matrix_level] ?? 9;
        const lb = _lvlOrder[b.matrix_level] ?? 9;
        if (la !== lb) return la - lb;
        return (b.matrix_score || 0) - (a.matrix_score || 0);
    });

    const combinedSummary = Array.from({ length: 5 }, () => Array(5).fill(0));
    if (summary) {
        for (let p = 0; p < 5; p++)
            for (let i = 0; i < 5; i++)
                combinedSummary[p][i] = Number(summary[p]?.[i] || 0);
    }
    gcsItems.forEach(item => {
        const p = Math.max(0, Math.min(4, (item.probability || 3) - 1));
        const i = Math.max(0, Math.min(4, (item.impact      || 3) - 1));
        combinedSummary[p][i]++;
    });

    let gridHtml = '<table class="matrix-grid"><thead><tr><th class="matrix-axis-label">PROB \\ IMPACT</th>';
    IMPACT_LABELS.forEach(lbl => gridHtml += `<th class="matrix-axis-label">${lbl}</th>`);
    gridHtml += '</tr></thead><tbody>';
    for (let p = 5; p >= 1; p--) {
        gridHtml += `<tr><th class="matrix-axis-label">${PROB_LABELS[p - 1]}</th>`;
        for (let i = 1; i <= 5; i++) {
            const level    = MATRIX_LEVEL[p - 1][i - 1];
            const count    = combinedSummary[p - 1][i - 1] || 0;
            const hasItems = count > 0;
            const cellStyle = hasItems
                ? 'cursor:pointer;transition:filter 0.15s,box-shadow 0.15s;'
                : 'opacity:0.45;';
            gridHtml += `<td class="matrix-cell-${level.toLowerCase()} matrix-cell-clickable"
                data-p="${p}" data-i="${i}"
                style="${cellStyle}"
                ${hasItems ? `onclick="matrixCellClick(${p},${i},this)"` : ''}
                title="${hasItems
                    ? `${count} item${count !== 1 ? 's' : ''} — P:${p} × I:${i} (${PROB_LABELS[p-1]} / ${IMPACT_LABELS[i-1]})`
                    : 'No items in this cell'}">
                <div style="font-size:14px;font-weight:700;">${count}</div>
                <div style="font-size:8px;opacity:0.8;text-transform:uppercase;">${level}</div>
            </td>`;
        }
        gridHtml += '</tr>';
    }
    gridHtml += '</tbody></table>';
    gridEl.innerHTML = gridHtml;

    window._lastScoredItems = allScored;
    _matrixFilter = null;
    _renderScoredItems(allScored, newsScored.length, gcsItems.length);
}

// ── Matrix cell click ─────────────────────────────────────

function matrixCellClick(p, i, cellEl) {
    const allScored = window._lastScoredItems || [];

    if (_matrixFilter && _matrixFilter.p === p && _matrixFilter.i === i) {
        _matrixFilter = null;
        _clearMatrixCellSelection();
        _renderScoredItems(allScored);
        return;
    }

    _matrixFilter = { p, i };
    _clearMatrixCellSelection();
    cellEl.classList.add('matrix-cell-active');

    const filtered = allScored.filter(item =>
        Number(item.probability) === p && Number(item.impact) === i
    );
    _renderScoredItems(filtered, null, null, { p, i });

    const itemsEl = document.getElementById('riskMatrixItems');
    if (itemsEl) itemsEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function _clearMatrixCellSelection() {
    document.querySelectorAll('.matrix-cell-active').forEach(el => el.classList.remove('matrix-cell-active'));
}

// ── Render scored-item cards (unchanged) ──────────────────

const _levelStyle = {
    'Extreme': { bg:'#FEE2E2', border:'#EF4444', badge:'#991B1B', badgeBg:'#FEE2E2' },
    'High':    { bg:'#FFF7ED', border:'#F97316', badge:'#9A3412', badgeBg:'#FFEDD5' },
    'Medium':  { bg:'#FEFCE8', border:'#EAB308', badge:'#854D0E', badgeBg:'#FEF9C3' },
    'Low':     { bg:'#F0FDF4', border:'#E2E8F0', badge:'#166534', badgeBg:'#DCFCE7' },
};


// ── Extract country from scored item ─────────────────────
// Uses source field + title keyword matching (mirrors risk_categories.json)
const _COUNTRY_KEYWORDS = {
    'USA':         ['federal reserve','fed ','u.s.','united states','america','treasury','sofr','fomc','powell','washington','fednow','new york fed'],
    'Canada':      ['canada','bank of canada','boc','canadian','ottawa','cad','corra'],
    'UK':          ['bank of england','boe','united kingdom','britain','london','gbp','sterling'],
    'Germany':     ['germany','deutsche','berlin','bundesbank','german'],
    'France':      ['france','french','paris','banque de france'],
    'EU':          ['ecb','european central bank','eurozone','euro area',' eu ','european union','lagarde','brussels'],
    'Japan':       ['bank of japan','boj','japan','yen','jpy','tokyo'],
    'China':       ['china','pboc','renminbi','yuan','cny','beijing','chinese'],
    'Brazil':      ['brazil','bcb','brl','real','brasilia'],
    'India':       ['india','rbi','reserve bank of india','rupee','inr','mumbai'],
    'Australia':   ['australia','rba','aud','sydney'],
    'Mexico':      ['mexico','banxico','mxn','peso'],
    'South Korea': ['korea','bank of korea','krw','won','seoul'],
    'Hong Kong':   ['hong kong','hkma','hkd'],
    'Singapore':   ['singapore','mas','sgd'],
};

const _SOURCE_COUNTRY = {
    'federal_reserve': 'USA',
    'bank_of_canada':  'Canada',
    'ecb':             'EU',
    'bank_of_england': 'UK',
    'bank_of_japan':   'Japan',
};

// GCS Data Alert titles often contain country name — extract from title
// e.g. "US CPI Rapid Acceleration — USA CPI" → USA
const _TITLE_COUNTRY_PATTERNS = [
    [/USA?|U\.S\.|United States/i, 'USA'],
    [/Canada|CAD/i,                'Canada'],
    [/UK|United Kingdom|Britain|GBP/i, 'UK'],
    [/ECB|Eurozone|Euro/i,    'EU'],
    [/Japan|JPY|Yen/i,        'Japan'],
    [/China|CNY|Yuan/i,       'China'],
    [/Canada|CORRA/i,             'Canada'],
    [/Australia|AUD/i,            'Australia'],
    [/India|INR/i,                'India'],
    [/Brazil|BRL/i,               'Brazil'],
    [/Mexico|MXN/i,               'Mexico'],
];

function _extractCountry(item) {
    // 1. Direct source mapping
    const srcKey = (item.source || '').toLowerCase();
    if (_SOURCE_COUNTRY[srcKey]) return _SOURCE_COUNTRY[srcKey];

    // 2. Title regex patterns (fast, handles "USA CPI", "US DXY" etc.)
    const title = item.title || '';
    for (const [pattern, country] of _TITLE_COUNTRY_PATTERNS) {
        if (pattern.test(title)) return country;
    }

    // 3. Title + rationale keyword match (fallback)
    const text = (title + ' ' + (item.impact_reasoning || '')).toLowerCase();
    for (const [country, keywords] of Object.entries(_COUNTRY_KEYWORDS)) {
        if (keywords.some(kw => text.includes(kw))) return country;
    }
    return null;  // no match — don't show link
}

function _renderScoredItems(items, newsCount, dataCount, filter) {
    const itemsEl = document.getElementById('riskMatrixItems');
    const header  = document.getElementById('riskMatrixItemsHeader');
    if (!itemsEl) return;

    if (filter) {
        const probLabel   = PROB_LABELS[filter.p - 1];
        const impactLabel = IMPACT_LABELS[filter.i - 1];
        if (header) header.innerHTML =
            `<span>Filtered: P${filter.p} × I${filter.i} — ${probLabel} / ${impactLabel}</span>
             <span style="font-size:10px;font-weight:500;color:var(--primary);margin-left:8px;">${items.length} item${items.length !== 1 ? 's' : ''}</span>
             <button onclick="matrixClearFilter()" style="margin-left:12px;font-size:10px;padding:1px 8px;
                border:1px solid var(--border);border-radius:3px;background:white;cursor:pointer;
                color:var(--text-secondary);">✕ Clear filter</button>`;
    } else {
        const total = (window._lastScoredItems || []).length;
        const nNews = newsCount ?? total;
        const nData = dataCount ?? 0;
        if (header) header.textContent =
            `Scored Intelligence Nodes (${total} total — ${nNews} news + ${nData} data alerts)`;
    }

    if (!items.length) {
        itemsEl.innerHTML = `<div style="grid-column:1/-1;text-align:center;padding:32px;
            color:var(--text-muted);font-size:12px;font-family:var(--font-mono);">
            No items in this cell.</div>`;
        return;
    }

    itemsEl.innerHTML = items.map((item, idx) => {
        const lvl    = item.matrix_level || 'Low';
        const style  = _levelStyle[lvl] || _levelStyle['Low'];
        const prob   = Number(item.probability) || 1;
        const imp    = Number(item.impact)      || 1;
        const srcTag = item.source === 'GCS Data Alert'
            ? `<span style="font-size:9px;font-weight:700;padding:1px 6px;border-radius:3px;background:#EFF6FF;color:var(--primary);font-family:var(--font-mono);">DATA</span>`
            : `<span style="font-size:9px;font-weight:700;padding:1px 6px;border-radius:3px;background:#F8FAFC;color:var(--text-muted);font-family:var(--font-mono);">NEWS</span>`;
        const newsLink = item.url
            ? `<a href="${escapeHtml(item.url)}" target="_blank"
                 style="display:inline-flex;align-items:center;gap:4px;margin-top:10px;font-size:11px;
                        color:var(--primary);text-decoration:none;font-family:var(--font-mono);">
                 Read source →</a>`
            : '';
        const _itemCountry = _extractCountry(item);
        const actionsLink = _itemCountry
            ? `<a onclick="viewActionsForCountry('${escapeHtml(_itemCountry)}')"
                 style="display:inline-flex;align-items:center;gap:4px;margin-top:10px;margin-left:12px;
                        font-size:11px;color:var(--primary);text-decoration:none;
                        font-family:var(--font-mono);cursor:pointer;">
                 View suggested actions (${escapeHtml(_itemCountry)}) →</a>`
            : '';
        return `
        <div class="card" data-item-idx="${idx}"
             style="border-left:3px solid ${style.border};background:${style.bg};transition:border-color 0.25s,background 0.25s;">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;padding-bottom:8px;border-bottom:1px solid rgba(0,0,0,0.06);">
                <div style="display:flex;gap:6px;align-items:center;">
                    <span class="status-badge" style="background:${style.badgeBg};color:${style.badge};font-size:10px;">${lvl}</span>
                    ${srcTag}
                </div>
                <span style="font-family:var(--font-mono);font-size:10px;color:var(--text-muted);">P:${prob} × I:${imp}</span>
            </div>
            <div style="font-weight:700;font-size:12px;margin-bottom:10px;line-height:1.4;color:var(--text-main);">${escapeHtml(item.title)}</div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:10px;">
                <div style="background:rgba(255,255,255,0.7);padding:7px 10px;border:1px solid rgba(0,0,0,0.06);border-radius:4px;">
                    <div style="font-size:9px;font-weight:700;text-transform:uppercase;color:var(--text-muted);margin-bottom:2px;">Probability</div>
                    <div style="font-size:13px;font-weight:700;color:${style.badge};">${item.probability}</div>
                </div>
                <div style="background:rgba(255,255,255,0.7);padding:7px 10px;border:1px solid rgba(0,0,0,0.06);border-radius:4px;">
                    <div style="font-size:9px;font-weight:700;text-transform:uppercase;color:var(--text-muted);margin-bottom:2px;">Impact</div>
                    <div style="font-size:13px;font-weight:700;color:${style.badge};">${item.impact}</div>
                </div>
            </div>
            <div style="font-size:11px;line-height:1.5;color:var(--text-secondary);">
                <span style="font-size:9px;font-weight:700;text-transform:uppercase;color:var(--text-muted);display:block;margin-bottom:3px;">Rationale</span>
                ${escapeHtml(item.impact_reasoning || '—')}
            </div>
            <div style="display:flex;flex-wrap:wrap;">
                ${newsLink}
                ${actionsLink}
            </div>
        </div>`;
    }).join('');
}

function matrixClearFilter() {
    _matrixFilter = null;
    _clearMatrixCellSelection();
    _renderScoredItems(window._lastScoredItems || []);
}


// ── Risk Monitor → Actions deep-link ─────────────────────
function viewActionsForCountry(country) {
    localStorage.setItem('argusActionsFilter', JSON.stringify({ country }));
    const navEl = document.querySelector('.nav-item[onclick*="actions"]');
    if (typeof showPage === 'function') showPage('actions', navEl);
}