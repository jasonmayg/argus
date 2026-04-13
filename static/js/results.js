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

async function loadResults() {
    try {
        const r = await fetch(`${API_BASE}/results`);
        const d = await r.json();
        if (d.status !== 'success') return;

        const count = d.stages.news_gathering?.count || 0;
        const kpiSignals = document.getElementById('kpiSignals');
        const kpiRisk    = document.getElementById('kpiRisk');
        if (kpiSignals) kpiSignals.textContent = count;
        if (kpiRisk)    kpiRisk.textContent    = 'ELEVATED';

        const now = new Date().toISOString().replace('T', ' ').substr(0, 16);
        const lastRunEl = document.getElementById('homeLastRun');
        if (lastRunEl) lastRunEl.textContent = 'Last run: ' + now + ' UTC';

        const riskBadge = document.getElementById('riskBadge');
        if (riskBadge) riskBadge.style.display = '';

        await renderRiskMatrix(d.stages.analysis?.analysis);
    } catch(e) {
        log('Error loading results: ' + e.message, 'error');
    }
}

async function renderRiskMatrix(analysis) {
    if (!analysis) return;
    document.getElementById('riskEmptyState').style.display   = 'none';
    document.getElementById('riskMatrixSection').style.display = 'block';

    const gridEl  = document.getElementById('riskMatrixGrid');
    const itemsEl = document.getElementById('riskMatrixItems');
    const summary = analysis.matrix_summary;

    // Fetch GCS data-alert matrix items
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

    // Build combined 5×5 summary grid
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

    // Render matrix table with clickable cells
    let gridHtml = '<table class="matrix-grid"><thead><tr><th class="matrix-axis-label">PROB \\ IMPACT</th>';
    IMPACT_LABELS.forEach(lbl => gridHtml += `<th class="matrix-axis-label">${lbl}</th>`);
    gridHtml += '</tr></thead><tbody>';
    for (let p = 5; p >= 1; p--) {
        gridHtml += `<tr><th class="matrix-axis-label">${PROB_LABELS[p - 1]}</th>`;
        for (let i = 1; i <= 5; i++) {
            const level = MATRIX_LEVEL[p - 1][i - 1];
            const count = combinedSummary[p - 1][i - 1] || 0;
            const hasItems = count > 0;
            const cellStyle = hasItems
                ? 'cursor:pointer;transition:filter 0.15s,box-shadow 0.15s;'
                : 'opacity:0.45;';
            gridHtml += `<td class="matrix-cell-${level.toLowerCase()} matrix-cell-clickable"
                data-p="${p}" data-i="${i}"
                style="${cellStyle}"
                ${hasItems ? `onclick="matrixCellClick(${p},${i},this)"` : ''}
                title="${hasItems ? `${count} item${count !== 1 ? 's' : ''} — P:${p} × I:${i} (${PROB_LABELS[p-1]} / ${IMPACT_LABELS[i-1]})` : 'No items in this cell'}">
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

// ── Matrix cell click ─────────────────────────────────────────

function matrixCellClick(p, i, cellEl) {
    const allScored = window._lastScoredItems || [];

    // Toggle: clicking the same cell again clears the filter
    if (_matrixFilter && _matrixFilter.p === p && _matrixFilter.i === i) {
        _matrixFilter = null;
        _clearMatrixCellSelection();
        _renderScoredItems(allScored);
        return;
    }

    // Set new filter
    _matrixFilter = { p, i };

    // Visual: dim all cells, highlight selected
    _clearMatrixCellSelection();
    cellEl.classList.add('matrix-cell-active');

    // Filter items to this P×I bucket
    const filtered = allScored.filter(item =>
        Number(item.probability) === p && Number(item.impact) === i
    );

    _renderScoredItems(filtered, null, null, { p, i });

    // Scroll to items section
    const itemsEl = document.getElementById('riskMatrixItems');
    if (itemsEl) itemsEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function _clearMatrixCellSelection() {
    document.querySelectorAll('.matrix-cell-active').forEach(el => el.classList.remove('matrix-cell-active'));
}

// ── Render scored-item cards ──────────────────────────────────

const _levelStyle = {
    'Extreme': { bg:'#FEE2E2', border:'#EF4444', badge:'#991B1B', badgeBg:'#FEE2E2' },
    'High':    { bg:'#FFF7ED', border:'#F97316', badge:'#9A3412', badgeBg:'#FFEDD5' },
    'Medium':  { bg:'#FEFCE8', border:'#EAB308', badge:'#854D0E', badgeBg:'#FEF9C3' },
    'Low':     { bg:'#F0FDF4', border:'#E2E8F0', badge:'#166534', badgeBg:'#DCFCE7' },
};

function _renderScoredItems(items, newsCount, dataCount, filter) {
    const itemsEl = document.getElementById('riskMatrixItems');
    const header  = document.getElementById('riskMatrixItemsHeader');
    if (!itemsEl) return;

    if (filter) {
        const level = MATRIX_LEVEL[filter.p - 1][filter.i - 1];
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
        itemsEl.innerHTML = `<div style="grid-column:1/-1;text-align:center;padding:32px;color:var(--text-muted);font-size:12px;font-family:var(--font-mono);">
            No items in this cell.</div>`;
        return;
    }

    itemsEl.innerHTML = items.map((item, idx) => {
        const lvl   = item.matrix_level || 'Low';
        const style = _levelStyle[lvl] || _levelStyle['Low'];
        const prob  = Number(item.probability) || 1;
        const imp   = Number(item.impact)      || 1;
        const srcTag = item.source === 'GCS Data Alert'
            ? `<span style="font-size:9px;font-weight:700;padding:1px 6px;border-radius:3px;background:#EFF6FF;color:var(--primary);font-family:var(--font-mono);">DATA</span>`
            : `<span style="font-size:9px;font-weight:700;padding:1px 6px;border-radius:3px;background:#F8FAFC;color:var(--text-muted);font-family:var(--font-mono);">NEWS</span>`;
        const newsLink = item.url
            ? `<a href="${escapeHtml(item.url)}" target="_blank"
                 style="display:inline-flex;align-items:center;gap:4px;margin-top:10px;font-size:11px;
                        color:var(--primary);text-decoration:none;font-family:var(--font-mono);">
                 Read source →</a>`
            : '';
        return `
        <div class="card" data-item-idx="${idx}"
             style="border-left:3px solid ${style.border};background:${style.bg};transition:border-color 0.25s,background 0.25s;">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;padding-bottom:8px;border-bottom:1px solid rgba(0,0,0,0.06);">
                <div style="display:flex;gap:6px;align-items:center;">
                    <span class="status-badge tune-lvl-badge"
                          style="background:${style.badgeBg};color:${style.badge};font-size:10px;transition:background 0.25s,color 0.25s;">${lvl}</span>
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
            ${newsLink}
        </div>`;
    }).join('');
}

function matrixClearFilter() {
    _matrixFilter = null;
    _clearMatrixCellSelection();
    _renderScoredItems(window._lastScoredItems || []);
}
