/* ============================================================
   ARGUS — Home Risk Category Cards & Data Alerts
   ============================================================ */

const CATEGORY_ICONS = {
    'Liquidity Risk':              '💧',
    'Funding Cost Pressure':       '📈',
    'Payment & Disbursement Risk': '💳',
    'Cash Concentration':          '🏦',
    'Regulatory':                  '⚖️',
    'Geopolitical':                '🌍',
    'Macroeconomic':               '📊',
    'Inflation':                   '🔥',
    'FX Risk':                     '💱',
    'Monetary Policy':             '🏛️',
    'Natural Disaster':            '🌋',
};

async function loadRiskCategories() {
    const container = document.getElementById('riskCategoryCards');
    if (!container) return;

    try {
        const r = await fetch('/api/home/risk-categories');
        const d = await r.json();

        if (d.status !== 'success' || !d.categories?.length) {
            container.innerHTML = `<div style="grid-column:1/-1;text-align:center;padding:20px;
                color:var(--text-muted);font-size:11px;">No risk categories available. Run Daily Jobs first.</div>`;
            return;
        }

        container.innerHTML = d.categories.map(cat => {
            const icon       = CATEGORY_ICONS[cat.risk_category] || '◈';
            const newsCount  = cat.count      || 0;
            const dataCount  = cat.data_count || 0;
            const totalCount = newsCount + dataCount;

            const borderColor = totalCount >= 5 ? 'var(--danger-text)' :
                                totalCount >= 2 ? 'var(--warning-text)' :
                                dataCount  >= 1 ? 'var(--primary)' : 'var(--border)';
            const countColor  = totalCount >= 5 ? 'var(--danger-text)' :
                                totalCount >= 2 ? 'var(--warning-text)' : 'var(--text-main)';
            const topTitle    = (cat.top_title || '—').substring(0, 55) +
                                ((cat.top_title || '').length > 55 ? '…' : '');

            const newsBadge = newsCount > 0
                ? `<span style="font-size:9px;padding:1px 5px;border-radius:3px;background:#F8FAFC;color:var(--text-muted);font-family:var(--font-mono);">${newsCount} news</span>` : '';
            const dataBadge = dataCount > 0
                ? `<span style="font-size:9px;padding:1px 5px;border-radius:3px;background:#EFF6FF;color:var(--primary);font-family:var(--font-mono);">${dataCount} data</span>` : '';

            return `<div style="background:white;border:1px solid var(--border);
                                border-top:3px solid ${borderColor};border-radius:6px;padding:12px 14px;
                                cursor:pointer;transition:all 0.15s;"
                         onmouseover="this.style.boxShadow='0 2px 8px rgba(0,0,0,0.08)'"
                         onmouseout="this.style.boxShadow=''"
                         onclick="showPage('risk', document.querySelector('.nav-item[onclick*=risk]'))">
                <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:6px;">
                    <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;color:var(--primary);font-family:var(--font-mono);">${escapeHtml(cat.risk_category)}</div>
                    <span style="font-size:13px;">${icon}</span>
                </div>
                <div style="font-size:22px;font-weight:700;color:${countColor};font-family:var(--font-mono);line-height:1;margin-bottom:6px;">
                    ${totalCount}<span style="font-size:11px;font-weight:500;color:var(--text-muted);"> signals</span>
                </div>
                <div style="display:flex;gap:4px;flex-wrap:wrap;margin-bottom:6px;">${newsBadge}${dataBadge}</div>
                <div style="font-size:10px;color:var(--text-muted);line-height:1.4;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${escapeHtml(topTitle)}</div>
            </div>`;
        }).join('');
    } catch(e) {
        container.innerHTML = `<div style="grid-column:1/-1;text-align:center;padding:20px;
            color:var(--text-muted);font-size:11px;">Could not load risk categories.</div>`;
    }
}

async function loadDataAlerts(fresh = false) {
    const body = document.getElementById('dataAlertsBody');
    if (!body) return;
    body.innerHTML = `<div style="text-align:center;padding:32px;color:var(--text-muted);font-size:11px;font-family:var(--font-mono);">Evaluating thresholds…</div>`;

    try {
        const url = '/api/data/alerts' + (fresh ? '?fresh=1' : '');
        const r   = await fetch(url);
        const d   = await r.json();
        if (d.status !== 'success') throw new Error(d.message || 'Failed');

        // Threshold-based alerts (already deduped by backend per country+indicator)
        const thresholdAlerts = d.alerts || [];

        // ── Merge GCS data update entries from Guardian ──────────────
        let gcsDataAlerts = [];
        try {
            const gr = await fetch(`${API_BASE}/guardian/dashboard`);
            const gd = await gr.json();
            if (gd.status === 'success') {
                // Dedup GCS entries by title — keep highest score per unique title
                const seenTitles = new Map();
                (gd.alerts || [])
                    .filter(a => {
                        const src   = (a.source || '').toLowerCase();
                        const title = (a.title  || '').trim();
                        return src === 'gcs_raw' || src === 'gcs_data' ||
                               /^US .* update:/i.test(title);
                    })
                    .forEach(a => {
                        const key      = (a.title || '').trim().toLowerCase();
                        const existing = seenTitles.get(key);
                        if (!existing || (a.score ?? 0) > (existing.score ?? 0)) {
                            seenTitles.set(key, a);
                        }
                    });

                gcsDataAlerts = Array.from(seenTitles.values()).map(a => ({
                    name:          a.title || 'GCS Data Update',
                    country:       a.country || 'USA',
                    indicator:     (a.category || 'gcs_data').toLowerCase().replace(' risk', ''),
                    category:      a.category || 'Market Data',
                    severity:      a.score >= 70 ? 'High' : a.score >= 40 ? 'Medium' : 'Low',
                    description:   a.recommendation || a.rationale || a.title || '',
                    display_value: `Score: ${(a.score ?? 0).toFixed(2)}`,
                    triggered_at:  a.published_at || a.raw_dt || '',
                    source:        'Guardian GCS',
                    operator:      '≥',
                    threshold:     40,
                }));
            }
        } catch(e) { /* Guardian unavailable */ }

        // ── Final merge: dedup across both sources by name ──────────
        const seenNames = new Set();
        const alerts = [...thresholdAlerts, ...gcsDataAlerts].filter(a => {
            if (a._error) return true; // keep errors
            const key = (a.name || '').trim().toLowerCase();
            if (seenNames.has(key)) return false;
            seenNames.add(key);
            return true;
        });

        // ── Update badges ────────────────────────────────────────────
        const total = d.total_triggered || 0;
        const high  = d.summary?.high   || 0;

        const totalBadge = document.getElementById('dataAlertTotalCount');
        const highBadge  = document.getElementById('dataAlertHighCount');
        if (totalBadge) {
            if ((d.alerts || []).length) {
                totalBadge.textContent = 'Recent trigger feed · 10 items';
            } else {
                const genAt = d.generated_at ? ' · ' + d.generated_at : '';
                totalBadge.textContent = total + ' Triggered' + genAt;
            }
        }
        if (highBadge) {
            highBadge.style.display = high > 0 ? '' : 'none';
            if (high > 0) highBadge.textContent = high + ' High';
        }

        if (!alerts.length) {
            body.innerHTML = `<div style="text-align:center;padding:32px;color:var(--success-text);font-size:11px;font-family:var(--font-mono);">✓ No threshold breaches detected</div>`;
            return;
        }

        const sevColor = s => s === 'High' ? 'var(--danger-text)'  : s === 'Medium' ? 'var(--warning-text)'  : 'var(--text-muted)';
        const sevBg    = s => s === 'High' ? 'var(--danger-bg)'    : s === 'Medium' ? 'var(--warning-bg)'    : 'var(--idle-bg)';

        const intro = d.historical_message
            ? `<div style="padding:10px 16px;border-bottom:1px solid var(--border);background:#F8FAFC;color:var(--text-secondary);font-size:11px;font-family:var(--font-mono);">${escapeHtml(d.historical_message)}</div>`
            : '';

        body.innerHTML = intro + alerts.filter(a => !a._error).map(a => `
            <div style="padding:10px 16px;border-bottom:1px solid var(--border);border-left:3px solid ${sevColor(a.severity)};">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
                    <div style="font-weight:700;font-size:12px;">${escapeHtml(a.name)}</div>
                    <div style="display:flex;align-items:center;gap:6px;">
                        <span style="font-size:10px;color:var(--text-muted);font-family:var(--font-mono);">${escapeHtml(a.triggered_at || '')}</span>
                        <span class="status-badge" style="background:${sevBg(a.severity)};color:${sevColor(a.severity)};">${escapeHtml(a.severity)}</span>
                    </div>
                </div>
                <div style="font-size:11px;color:var(--text-secondary);margin-bottom:6px;">${escapeHtml(a.description)}</div>
                <div style="display:flex;gap:10px;font-size:10px;font-family:var(--font-mono);color:var(--text-muted);flex-wrap:wrap;">
                    <span>${escapeHtml(a.country)} · ${escapeHtml(a.indicator)}</span>
                    <span style="color:${sevColor(a.severity)};font-weight:700;">Actual: ${escapeHtml(a.display_value)}</span>
                    <span>Threshold: ${a.operator} ${a.threshold}</span>
                    <span style="background:var(--bg-color);padding:1px 6px;border-radius:3px;">${escapeHtml(a.category)}</span>
                </div>
            </div>`).join('');

    } catch(e) {
        body.innerHTML = `<div style="text-align:center;padding:24px;color:var(--text-muted);font-size:11px;">
            Could not load data alerts: ${escapeHtml(e.message)}</div>`;
    }
}
