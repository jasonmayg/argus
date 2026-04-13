/* ============================================================
   ARGUS — Cash Position Page
   ============================================================ */

let CASH_DATA         = [];
let CASH_HISTORY      = [];
let cashCurrentFilter = 'all';
let cashHistoryChart  = null;

function fmtUSD(n) {
    if (Math.abs(n) >= 1e9) return '$' + (n / 1e9).toFixed(2) + 'B';
    if (Math.abs(n) >= 1e6) return '$' + (n / 1e6).toFixed(1) + 'M';
    return '$' + Math.round(n).toLocaleString();
}

function fmtLocal(n, currency) {
    if (Math.abs(n) >= 1e12) return (n / 1e12).toFixed(2) + 'T ' + currency;
    if (Math.abs(n) >= 1e9)  return (n / 1e9).toFixed(2)  + 'B ' + currency;
    if (Math.abs(n) >= 1e6)  return (n / 1e6).toFixed(1)  + 'M ' + currency;
    return Math.round(n).toLocaleString() + ' ' + currency;
}

function riskBadgeStyle(level) {
    if (level === 'High')   return 'background:var(--danger-bg);color:var(--danger-text);';
    if (level === 'Medium') return 'background:var(--warning-bg);color:var(--warning-text);';
    return 'background:var(--success-bg);color:var(--success-text);';
}

function filterCash(filter, btn) {
    cashCurrentFilter = filter;
    document.querySelectorAll('.cash-filter').forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');
    renderCashTable();
    renderCashBar();
}

async function renderCashPage() {
    const kpiRow = document.getElementById('cashKpiRow');
    if (kpiRow) kpiRow.innerHTML = `<div style="grid-column:1/-1;text-align:center;padding:20px;color:var(--text-muted);font-family:var(--font-mono);font-size:11px;">Loading cash positions…</div>`;

    try {
        const [posResp, histResp] = await Promise.all([
            fetch('/api/cash/positions'),
            fetch('/api/cash/history'),
        ]);
        const posData  = await posResp.json();
        const histData = await histResp.json();

        if (posData.status !== 'success') throw new Error(posData.message || 'Failed to load positions');

        CASH_DATA    = posData.positions || [];
        CASH_HISTORY = histData.history  || [];

        const asOf = document.getElementById('cashAsOf');
        if (asOf) asOf.textContent = posData.as_of + ' (simulated)';

        renderCashKPIs(posData);
        renderCashTable();
        renderCashBar();
        renderCashHistoryChart();
    } catch(e) {
        if (kpiRow) kpiRow.innerHTML = `<div style="grid-column:1/-1;text-align:center;padding:20px;color:var(--danger-text);font-size:12px;">Error loading cash data: ${e.message}</div>`;
    }
}

function renderCashKPIs(posData) {
    const totalUSD    = posData.total_usd || 0;
    const regions     = posData.regional_totals || {};
    const highRisk    = posData.high_risk_usd || 0;
    const entityCount = posData.entity_count || 0;
    const americas = regions['Americas'] || 0;
    const europe   = regions['Europe']   || 0;
    const apac     = regions['APAC']     || 0;

    const kpiCard = (label, value, sub, accent = '') => `
        <div style="background:white;border:1px solid var(--border);border-radius:6px;padding:12px 16px;${accent ? 'border-left:3px solid ' + accent + ';' : ''}">
            <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:0.1em;color:var(--text-muted);font-family:var(--font-mono);margin-bottom:4px;">${label}</div>
            <div style="font-size:20px;font-weight:700;color:${accent === 'var(--danger-text)' ? accent : 'var(--text-main)'};font-family:var(--font-mono);">${value}</div>
            <div style="font-size:10px;color:var(--text-muted);margin-top:2px;">${sub}</div>
        </div>`;

    document.getElementById('cashKpiRow').innerHTML =
        kpiCard('Total Global Cash', fmtUSD(totalUSD), entityCount + ' entities', 'var(--primary)') +
        kpiCard('Americas', fmtUSD(americas), (totalUSD ? (americas / totalUSD * 100).toFixed(1) : 0) + '% of total') +
        kpiCard('Europe',   fmtUSD(europe),   (totalUSD ? (europe   / totalUSD * 100).toFixed(1) : 0) + '% of total') +
        kpiCard('Asia Pacific', fmtUSD(apac), (totalUSD ? (apac     / totalUSD * 100).toFixed(1) : 0) + '% of total') +
        kpiCard('High-Risk Exposure', fmtUSD(highRisk), (totalUSD ? (highRisk / totalUSD * 100).toFixed(1) : 0) + '% of total', 'var(--danger-text)');
}

function renderCashTable() {
    const totalUSD = CASH_DATA.reduce((s, r) => s + (r.usd_equivalent || 0), 0);
    const filtered = cashCurrentFilter === 'all'
        ? CASH_DATA
        : CASH_DATA.filter(r => (r.region || '').toLowerCase() === cashCurrentFilter.toLowerCase());

    const tbody = document.getElementById('cashTableBody');
    const tfoot = document.getElementById('cashTableFoot');
    if (!tbody) return;

    tbody.innerHTML = filtered.map(row => {
        const usd      = row.usd_equivalent || 0;
        const pct      = totalUSD ? (usd / totalUSD * 100).toFixed(1) : '0.0';
        const chg      = row.change_30d_pct || 0;
        const chgColor = chg >= 0 ? 'var(--success-text)' : 'var(--danger-text)';
        const chgSign  = chg >= 0 ? '+' : '';
        const barWidth = Math.min(100, totalUSD ? usd / totalUSD * 100 * 5 : 0);
        const risk     = row.risk_level || 'Low';
        return `<tr>
            <td>
                <div style="font-weight:700;font-size:12px;">${escapeHtml(row.country || '')}</div>
                <div style="font-size:10px;color:var(--text-muted);">${escapeHtml(row.entity || '')}</div>
            </td>
            <td style="font-family:var(--font-mono);font-weight:600;">${row.currency || ''}</td>
            <td style="text-align:right;font-family:var(--font-mono);">${fmtLocal(row.local_balance || 0, row.currency || '')}</td>
            <td style="text-align:right;font-family:var(--font-mono);font-weight:700;">${fmtUSD(usd)}</td>
            <td style="text-align:right;">
                <div style="display:flex;align-items:center;justify-content:flex-end;gap:6px;">
                    <div style="width:48px;background:#F1F5F9;border-radius:2px;height:4px;overflow:hidden;">
                        <div style="width:${barWidth}%;background:var(--primary);height:100%;"></div>
                    </div>
                    <span style="font-family:var(--font-mono);font-size:11px;">${pct}%</span>
                </div>
            </td>
            <td style="text-align:right;font-family:var(--font-mono);color:${chgColor};font-weight:600;">${chgSign}${chg.toFixed(1)}%</td>
            <td><span class="status-badge" style="${riskBadgeStyle(risk)}">${risk}</span></td>
            <td style="font-family:var(--font-mono);font-size:11px;color:var(--text-muted);">${(row.fx_rate || 0).toFixed(4)}</td>
            <td style="font-family:var(--font-mono);font-size:10px;color:var(--text-muted);">${row.notes || '—'}</td>
        </tr>`;
    }).join('');

    const filteredUSD = filtered.reduce((s, r) => s + (r.usd_equivalent || 0), 0);
    if (tfoot) tfoot.innerHTML = `<tr style="background:#F8FAFC;border-top:2px solid var(--border);">
        <td colspan="3" style="font-weight:700;font-size:12px;padding:10px 12px;">Subtotal (${filtered.length} entities)</td>
        <td style="text-align:right;font-family:var(--font-mono);font-weight:700;font-size:13px;padding:10px 12px;">${fmtUSD(filteredUSD)}</td>
        <td style="text-align:right;font-family:var(--font-mono);font-size:11px;padding:10px 12px;">${totalUSD ? (filteredUSD / totalUSD * 100).toFixed(1) : 0}%</td>
        <td colspan="4"></td>
    </tr>`;
}

function renderCashBar() {
    const totalUSD = CASH_DATA.reduce((s, r) => s + (r.usd_equivalent || 0), 0);
    const sorted   = [...CASH_DATA].sort((a, b) => (b.usd_equivalent || 0) - (a.usd_equivalent || 0)).slice(0, 10);
    const maxUSD   = sorted[0]?.usd_equivalent || 1;
    const el       = document.getElementById('cashBarChart');
    if (!el) return;

    el.innerHTML = sorted.map(row => {
        const usd      = row.usd_equivalent || 0;
        const barPct   = (usd / maxUSD * 100).toFixed(1);
        const barColor = row.risk_level === 'High'   ? '#EF4444' :
                         row.risk_level === 'Medium' ? '#F59E0B' : '#2563EB';
        return `<div style="display:grid;grid-template-columns:110px 1fr 80px;gap:8px;align-items:center;">
            <div style="font-size:11px;font-weight:600;text-align:right;color:var(--text-main);">${escapeHtml(row.country || '')}</div>
            <div style="background:#F1F5F9;border-radius:2px;height:16px;overflow:hidden;">
                <div style="width:${barPct}%;background:${barColor};height:100%;border-radius:2px;transition:width 0.6s ease;"></div>
            </div>
            <div style="font-family:var(--font-mono);font-size:11px;color:var(--text-muted);">${fmtUSD(usd)}</div>
        </div>`;
    }).join('');
}

function renderCashHistoryChart() {
    const chartContainer = document.getElementById('cashHistoryChartWrap');
    if (!chartContainer || !CASH_HISTORY.length) return;

    const selector = document.getElementById('cashHistoryCountry');
    const selected = selector ? selector.value : 'top5';

    let countries;
    if (selected === 'top5') {
        countries = [...CASH_DATA]
            .sort((a, b) => (b.usd_equivalent || 0) - (a.usd_equivalent || 0))
            .slice(0, 5).map(r => r.country);
    } else {
        countries = [selected];
    }

    const dates = [...new Set(CASH_HISTORY.map(h => h.date))].sort();
    const colors = ['#2563EB','#F59E0B','#10B981','#EF4444','#8B5CF6'];

    const datasets = countries.map((country, i) => ({
        label:           country,
        data:            dates.map(d => { const row = CASH_HISTORY.find(h => h.country === country && h.date === d); return row ? row.usd_equivalent : null; }),
        borderColor:     colors[i],
        backgroundColor: colors[i] + '18',
        borderWidth:     1.5,
        pointRadius:     3,
        tension:         0.3,
        fill:            false,
    }));

    const ctx = document.getElementById('cashHistoryChart');
    if (!ctx) return;
    if (cashHistoryChart) cashHistoryChart.destroy();

    cashHistoryChart = new Chart(ctx, {
        type: 'line',
        data: { labels: dates, datasets },
        options: {
            responsive:          true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend:  { display: true, labels: { font: { size: 10 } } },
                tooltip: { callbacks: { label: ctx => ` ${ctx.dataset.label}: ${fmtUSD(ctx.parsed.y)}` } },
            },
            scales: {
                x: { ticks: { font: { size: 10 } }, grid: { color: '#F1F5F9' } },
                y: { ticks: { font: { size: 10 }, callback: v => fmtUSD(v) }, grid: { color: '#F1F5F9' } },
            },
        },
    });
}
