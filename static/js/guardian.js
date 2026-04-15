/* ============================================================
   ARGUS — Guardian Dashboard (Risk Monitor page)
   ============================================================ */

let guardianTopAlerts      = [];
let _guardianCountryFilter = null;   // country name string or null = show all

// Called from the map when a country is clicked
function showCountryEvents(countryName) {
    // Navigate to Risk Monitor
    const riskNav = document.querySelector('.nav-item[onclick*="risk"]');
    showPage('risk', riskNav);

    // Wait one tick for loadGuardianDashboard to fire, then apply filter
    _guardianCountryFilter = countryName;
    setTimeout(() => _applyCountryFilter(countryName), 400);
}

function _applyCountryFilter(countryName) {
    const filtered = guardianTopAlerts.filter(
        a => (a.country || '').toLowerCase() === countryName.toLowerCase()
    );
    _renderAlertsWithFilter(filtered, countryName);
}

function _renderAlertsWithFilter(alerts, countryName) {
    // Show/update the filter banner inside the News Alerts card header
    let banner = document.getElementById('newsAlertsFilterBanner');
    if (!banner) {
        const header = document.querySelector('#criticalAlertsTableBody')
            ?.closest('.card')
            ?.querySelector('div[style*="border-bottom"]');
        if (header) {
            banner = document.createElement('div');
            banner.id = 'newsAlertsFilterBanner';
            banner.style.cssText =
                'display:flex;align-items:center;justify-content:space-between;' +
                'padding:6px 16px;background:#EFF6FF;border-bottom:1px solid #BFDBFE;' +
                'font-size:10px;font-family:var(--font-mono);color:#1D4ED8;';
            header.insertAdjacentElement('afterend', banner);
        }
    }
    if (banner) {
        banner.style.display = 'flex';
        banner.innerHTML =
            `<span>Showing: <strong>${escapeHtml(countryName)}</strong> · ${alerts.length} alert${alerts.length !== 1 ? 's' : ''}</span>` +
            `<button onclick="clearCountryFilter()" style="font-size:10px;padding:1px 8px;border:1px solid #BFDBFE;` +
            `border-radius:3px;background:white;cursor:pointer;color:#1D4ED8;">✕ All countries</button>`;
    }
    renderGuardianAlerts(alerts);
}

function clearCountryFilter() {
    _guardianCountryFilter = null;
    const banner = document.getElementById('newsAlertsFilterBanner');
    if (banner) banner.style.display = 'none';
    renderGuardianAlerts(guardianTopAlerts);
}

async function loadGuardianDashboard() {
    try {
        const r = await fetch(`${API_BASE}/guardian/dashboard`);
        const d = await r.json();
        if (d.status !== 'success') {
            document.getElementById('riskEmptyState').style.display = 'block';
            return;
        }
        document.getElementById('riskEmptyState').style.display = 'none';

        const es = d.executive_summary || {};
        const setKpi = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };

        setKpi('riskKpiCountries', es.at_risk_country_count ?? es.high_risk_country_count ?? '—');
        setKpi('riskKpiAlerts',    es.total_risk_alert_count ?? '—');
        setKpi('riskKpiHigh',      es.high_risk_alert_count  ?? '—');
        setKpi('kpiCountries',     es.at_risk_country_count  ?? '—');

        const trend = es.trend || {};
        if (trend.direction) {
            const arrow = trend.direction === 'UP' ? '↑' : trend.direction === 'DOWN' ? '↓' : '→';
            setKpi('riskKpiAlerts', `${es.total_risk_alert_count ?? '—'} ${arrow}`);
        }

        const gd = d.google_downside || {};
        if (gd.status === 'unavailable') {
            setKpi('riskKpiDownside',    'N/A');
            setKpi('riskKpiDownsideSub', gd.reason || 'Unavailable');
        } else {
            setKpi('riskKpiDownside',    (gd.percent ?? '—') + '%');
            setKpi('riskKpiDownsideSub', gd.level || '');
        }

        const fx = d.fx_summary || {};
        setKpi('riskKpiFX', fx.high_risk_country_count ?? fx.medium_or_above_country_count ?? '—');

        // Country board
        const top5 = d.country_rank_top5 || [];
        document.getElementById('countryBoardGrid').innerHTML = top5.map(c => {
            const riskColor = c.risk_level === 'High'   ? 'var(--danger-text)'  :
                              c.risk_level === 'Medium' ? 'var(--warning-text)' : 'var(--text-muted)';
            return `<div class="card" style="padding:14px;">
                <div style="font-weight:700;font-size:13px;">${escapeHtml(c.country)}</div>
                <div style="font-size:11px;color:var(--text-muted);margin-top:4px;font-family:var(--font-mono);">${c.alert_count} Alerts</div>
                <div style="font-size:10px;margin-top:4px;color:${riskColor};font-weight:600;">${c.top_category || ''}</div>
            </div>`;
        }).join('');

        // Filter out GCS raw entries and deduplicate
        const seen = new Set();
        guardianTopAlerts = (d.alerts || d.critical_alerts_top5 || []).filter(a => {
            const src   = (a.source || '').toLowerCase();
            const title = (a.title  || '').trim();
            if (src === 'gcs_raw' || src === 'gcs_data') return false;
            if (/^US .* update:/i.test(title)) return false;
            const key = title.toLowerCase();
            if (seen.has(key)) return false;
            seen.add(key);
            return true;
        });

        const newsCountBadge = document.getElementById('newsAlertCount');
        if (newsCountBadge) {
            const nc = guardianTopAlerts.length;
            newsCountBadge.textContent  = nc + ' Alert' + (nc !== 1 ? 's' : '');
            newsCountBadge.style.background = nc > 0 ? 'var(--warning-bg)' : 'var(--idle-bg)';
            newsCountBadge.style.color      = nc > 0 ? 'var(--warning-text)' : 'var(--idle-text)';
        }

        if (_guardianCountryFilter) {
            _applyCountryFilter(_guardianCountryFilter);
        } else {
            renderGuardianAlerts(guardianTopAlerts);
        }
    } catch(e) {
        console.error('loadGuardianDashboard error:', e);
        document.getElementById('riskEmptyState').style.display = 'block';
    }
}

function renderGuardianAlerts(alerts, flash = false) {
    const tbody = document.getElementById('criticalAlertsTableBody');
    if (!tbody) return;
    tbody.innerHTML = alerts.map((a, idx) => {
        const score      = a._eff !== undefined ? a._eff : parseFloat(a.score || 0);
        const scoreBg    = score >= 70 ? 'var(--danger-bg)'   : score >= 40 ? 'var(--warning-bg)'  : 'var(--idle-bg)';
        const scoreColor = score >= 70 ? 'var(--danger-text)' : score >= 40 ? 'var(--warning-text)' : 'var(--idle-text)';
        const cls = flash && idx < 5 ? ' class="tune-live-flash"' : '';
        return `<tr${cls}>
            <td style="font-weight:700;white-space:nowrap;">${escapeHtml(a.country || '')}</td>
            <td style="color:var(--text-secondary);white-space:nowrap;">${escapeHtml(a.category || 'Unclassified')}</td>
            <td><span class="status-badge" style="background:${scoreBg};color:${scoreColor};">${Math.round(score)}</span></td>
            <td style="font-size:11px;max-width:200px;">
                ${a.url
                    ? `<a href="${escapeHtml(a.url)}" target="_blank"
                        style="color:var(--text-main);text-decoration:none;display:-webkit-box;
                        -webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;">
                        ${escapeHtml(a.title || '')}</a>`
                    : `<span style="display:-webkit-box;-webkit-line-clamp:2;
                        -webkit-box-orient:vertical;overflow:hidden;">
                        ${escapeHtml(a.title || '')}</span>`}
            </td>
        </tr>`;
    }).join('');
}
