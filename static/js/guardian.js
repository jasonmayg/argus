/* ============================================================
   ARGUS — Guardian Dashboard (Risk Monitor page)
   ============================================================ */

let guardianTopAlerts      = [];
let guardianBaseAlerts     = [];   // baseline alerts before live re-weighting (used by the Weights Tuner)
let _guardianCountryFilter = null;   // country name string or null = show all
let _guardianCategoryFilter = null;  // ★ category filter from Home cards

// Called from the map when a country is clicked
function showCountryEvents(countryName) {
    const riskNav = document.querySelector('.nav-item[onclick*="risk"]');
    showPage('risk', riskNav);
    _guardianCountryFilter = countryName;
    setTimeout(() => _applyCountryFilter(countryName), 400);
}

function _applyCountryFilter(countryName) {
    const filtered = guardianTopAlerts.filter(
        a => (a.country || '').toLowerCase() === countryName.toLowerCase()
    );
    _renderAlertsWithFilter(filtered, countryName);
}

function _applyCategoryFilter(categoryName) {
    // Normalize: strip " Risk" suffix for broader matching
    const norm = categoryName.toLowerCase().replace(/ risk$/i, '').trim();

    // 1. Filter News Alerts
    const filtered = guardianTopAlerts.filter(a => {
        const cat = (a.category || '').toLowerCase();
        return cat.includes(norm) || norm.includes(cat.replace(/ risk$/i,'').trim());
    });
    _renderAlertsWithCategoryBanner(filtered, categoryName);

    // 2. Filter Data Alerts — highlight matching rows on the right side
    _highlightDataAlertsByCategory(norm, categoryName);
}

function _highlightDataAlertsByCategory(norm, categoryName) {
    const body = document.getElementById('dataAlertsBody');
    if (!body) return;

    // Find all data alert rows and highlight matching ones
    const rows = body.querySelectorAll('div[style*="border-left"]');
    let firstMatch = null;
    let matchCount = 0;

    rows.forEach(row => {
        const catTag = row.querySelector('span[style*="border-radius"]');
        const catText = (catTag?.textContent || row.textContent || '').toLowerCase();
        const matches = catText.includes(norm);

        if (matches) {
            row.style.outline      = '2px solid var(--primary)';
            row.style.outlineOffset = '-2px';
            if (!firstMatch) firstMatch = row;
            matchCount++;
        } else {
            row.style.opacity = '0.35';
            row.style.outline = '';
        }
    });

    // Scroll to first match in Data Alerts
    if (firstMatch) {
        firstMatch.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    // If no news alerts but data alerts exist, show a note in the banner
    if (matchCount > 0) {
        const banner = document.getElementById('newsAlertsFilterBanner');
        if (banner) {
            const note = banner.querySelector('.data-note');
            if (!note) {
                const span = document.createElement('span');
                span.className = 'data-note';
                span.style.cssText = 'margin-left:8px;color:#1D4ED8;font-size:10px;';
                span.textContent = `· ${matchCount} match${matchCount!==1?'es':''} in Data Alerts →`;
                banner.querySelector('span')?.appendChild(span);
            }
        }
    }

    // Reset highlights when filter is cleared (patch clearCountryFilter)
    window._lastDataHighlightNorm = norm;
}

function _renderAlertsWithCategoryBanner(alerts, categoryName) {
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
            `<span>Category: <strong>${escapeHtml(categoryName)}</strong> · ${alerts.length} alert${alerts.length !== 1 ? 's' : ''}</span>` +
            `<button onclick="clearCountryFilter()" style="font-size:10px;padding:1px 8px;border:1px solid #BFDBFE;` +
            `border-radius:3px;background:white;cursor:pointer;color:#1D4ED8;">✕ Show all</button>`;
    }
    renderGuardianAlerts(alerts);
}

function _renderAlertsWithFilter(alerts, countryName) {
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
    _guardianCountryFilter  = null;
    _guardianCategoryFilter = null;
    const banner = document.getElementById('newsAlertsFilterBanner');
    if (banner) banner.style.display = 'none';
    renderGuardianAlerts(guardianTopAlerts);

    // Reset Data Alerts highlights
    const body = document.getElementById('dataAlertsBody');
    if (body) {
        body.querySelectorAll('div[style*="border-left"]').forEach(row => {
            row.style.opacity = '';
            row.style.outline = '';
            row.style.outlineOffset = '';
        });
    }
    window._lastDataHighlightNorm = null;
}

async function loadGuardianDashboard() {
    // ★ Check for category filter from Home cards
    const catFilter = localStorage.getItem('argusRiskCategoryFilter');
    if (catFilter) {
        localStorage.removeItem('argusRiskCategoryFilter');
        _guardianCategoryFilter = catFilter;
    }

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
            const riskTip   = c.risk_level === 'High'
                ? 'High Risk (score 60–100): This jurisdiction has signals indicating significant probability of material impact.'
                : c.risk_level === 'Medium'
                ? 'Medium Risk (score 30–59): Moderate exposure detected for this jurisdiction. Monitor closely.'
                : 'Low Risk (score 0–29): Within acceptable bounds for this jurisdiction.';
            return `<div class="card" style="padding:14px;">
                <div style="font-weight:700;font-size:13px;">${escapeHtml(c.country)}</div>
                <div style="font-size:11px;color:var(--text-muted);margin-top:4px;font-family:var(--font-mono);">${c.alert_count} Alerts</div>
                <div style="font-size:10px;margin-top:4px;color:${riskColor};font-weight:600;cursor:default;"
                     title="${riskTip}">${c.top_category || ''} · ${c.risk_level || ''}</div>
            </div>`;
        }).join('');

        // Filter out GCS raw entries, deduplicate, and keep only last 7 days
        const seen       = new Set();
        const sevenDays  = Date.now() - 7 * 24 * 60 * 60 * 1000;
        guardianTopAlerts = (d.alerts || d.critical_alerts_top5 || []).filter(a => {
            const src   = (a.source || '').toLowerCase();
            const title = (a.title  || '').trim();
            if (src === 'gcs_raw' || src === 'gcs_data') return false;
            if (/^US .* update:/i.test(title)) return false;
            const key = title.toLowerCase();
            if (seen.has(key)) return false;
            seen.add(key);
            // Only show alerts from last 7 days
            const pubDate = a.published_at || a.raw_dt || '';
            if (pubDate) {
                const ts = new Date(pubDate).getTime();
                if (!isNaN(ts) && ts < sevenDays) return false;
            }
            return true;
        });

        const newsCountBadge = document.getElementById('newsAlertCount');
        if (newsCountBadge) {
            const nc = guardianTopAlerts.length;
            newsCountBadge.textContent  = nc + ' Alert' + (nc !== 1 ? 's' : '');
            newsCountBadge.style.background = nc > 0 ? 'var(--warning-bg)' : 'var(--idle-bg)';
            newsCountBadge.style.color      = nc > 0 ? 'var(--warning-text)' : 'var(--idle-text)';
        }

        // ★ Apply filter: category takes priority, then country, then show all
        if (_guardianCategoryFilter) {
            _applyCategoryFilter(_guardianCategoryFilter);
            _guardianCategoryFilter = null;
        } else if (_guardianCountryFilter) {
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
        const riskLvl    = score >= 70 ? 'High' : score >= 40 ? 'Medium' : 'Low';
        const scoreBg    = riskLvl === 'High'   ? 'var(--danger-bg)'   : riskLvl === 'Medium' ? 'var(--warning-bg)'  : 'var(--idle-bg)';
        const scoreColor = riskLvl === 'High'   ? 'var(--danger-text)' : riskLvl === 'Medium' ? 'var(--warning-text)' : 'var(--idle-text)';
        const riskTip    = riskLvl === 'High'
            ? `High Risk (score 60–100): ${escapeHtml(a.rationale || a.title || 'Significant probability of material treasury impact.')}`
            : riskLvl === 'Medium'
            ? `Medium Risk (score 30–59): ${escapeHtml(a.rationale || a.title || 'Moderate exposure detected. Monitor closely.')}`
            : `Low Risk (score 0–29): ${escapeHtml(a.rationale || a.title || 'Within acceptable bounds.')}`;
        const cls = flash && idx < 5 ? ' class="tune-live-flash"' : '';
        return `<tr${cls}>
            <td style="font-weight:700;white-space:nowrap;">${escapeHtml(a.country || '')}</td>
            <td style="color:var(--text-secondary);white-space:nowrap;">${escapeHtml(a.category || 'Unclassified')}</td>
            <td><span class="status-badge" style="background:${scoreBg};color:${scoreColor};"
                      title="${riskTip}">${Math.round(score)} · ${riskLvl}</span></td>
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