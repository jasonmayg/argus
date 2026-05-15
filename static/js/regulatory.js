/* ============================================================
   ARGUS — Regulatory Changes Tracker
   ============================================================ */

let regFilters = { country: '', category: '', severity: '', q: '' };
let _regSearchTimer = null;

function setRegFilter(type, val, btn) {
    regFilters[type] = val;
    document.querySelectorAll(`.reg-filter[data-filter-type="${type}"]`).forEach(b => {
        b.classList.toggle('active', b.dataset.filterVal === val);
    });
    loadRegulatory();
}

// Debounce keystrokes so we don't fire a Chroma query on every character.
// 250ms is short enough to feel responsive, long enough to coalesce typing.
function onRegSearchInput(val) {
    const status = document.getElementById('regSearchStatus');
    if (status) status.textContent = val.trim() ? '⌛ typing…' : '';
    clearTimeout(_regSearchTimer);
    _regSearchTimer = setTimeout(() => {
        regFilters.q = val.trim();
        loadRegulatory();
    }, 250);
}

// Translate the rag block from /api/regulatory into a one-line status the
// user can actually act on. Per CLAUDE.md: "Never just report a dead-end
// error — guide the user toward the next step."
function _ragStatusText(d) {
    if (!d || !regFilters.q) return '';
    const rag = d.rag || {};
    if (rag.used) {
        const parts = [`✦ semantic · ${d.total} hit${d.total === 1 ? '' : 's'}`];
        if (rag.retried_top_k) parts.push(`expanded pool to ${rag.retried_top_k}`);
        if (rag.top_distance != null) parts.push(`top ${rag.top_distance}`);
        return parts.join(' · ');
    }
    if (rag.fallback === 'disabled') {
        return '⚠ semantic ranking off (config) · using keyword match';
    }
    if (rag.fallback === 'sql_like') {
        return d.total
            ? `keyword match · ${d.total} hit${d.total === 1 ? '' : 's'} (no semantic match)`
            : 'no matches — try fewer filters or different wording';
    }
    return '';
}

async function loadRegulatory() {
    const list = document.getElementById('regList');
    if (!list) return;
    list.innerHTML = `<div style="text-align:center;padding:32px;color:var(--text-muted);font-size:11px;">Loading…</div>`;

    const params = new URLSearchParams();
    if (regFilters.country)  params.set('country',  regFilters.country);
    if (regFilters.category) params.set('category', regFilters.category);
    if (regFilters.severity) params.set('severity', regFilters.severity);
    if (regFilters.q)        params.set('q',        regFilters.q);
    params.set('limit', '100');

    try {
        const [sr, r] = await Promise.all([
            fetch('/api/regulatory/summary'),
            fetch(`/api/regulatory?${params}`),
        ]);
        const [sd, d] = await Promise.all([sr.json(), r.json()]);
        if (sd.status === 'success') _renderRegFilters(sd);

        const now = new Date().toISOString().replace('T', ' ').substr(0, 16);
        const fetchEl  = document.getElementById('regLastFetch');
        const badgeEl  = document.getElementById('regTotalBadge');
        const statusEl = document.getElementById('regSearchStatus');
        if (fetchEl) fetchEl.textContent = 'Updated ' + now + ' UTC';
        if (badgeEl) badgeEl.textContent = (d.total || 0) + ' regulatory changes';
        if (statusEl) statusEl.textContent = _ragStatusText(d);

        if (!d.items?.length) {
            const emptyMsg = regFilters.q
                ? `<div style="font-size:13px;margin-bottom:8px;">No matches for "${escapeHtml(regFilters.q)}"</div>
                   <div style="font-size:11px;">Try fewer filters, broader wording, or clear the search box</div>`
                : `<div style="font-size:13px;margin-bottom:8px;">No regulatory changes found</div>
                   <div style="font-size:11px;">Run Daily Jobs or click ✦ Re-analyse</div>`;
            list.innerHTML = `<div style="text-align:center;padding:48px;color:var(--text-muted);">
                <div style="font-size:24px;margin-bottom:12px;">⚖</div>
                ${emptyMsg}
            </div>`;
            return;
        }

        const sevColor  = s => s === 'High' ? 'var(--danger-text)'  : s === 'Medium' ? 'var(--warning-text)'  : 'var(--text-muted)';
        const sevBg     = s => s === 'High' ? 'var(--danger-bg)'    : s === 'Medium' ? 'var(--warning-bg)'    : 'var(--idle-bg)';
        const sevBorder = s => s === 'High' ? 'var(--danger-text)'  : s === 'Medium' ? 'var(--warning-text)'  : 'var(--border)';

        list.innerHTML = d.items.map(item => {
            const hasBefore = item.change_before?.trim();
            const hasAfter  = item.change_after?.trim();
            const hasAction = item.action_required?.trim();
            const pubDate   = item.published_at ? item.published_at.substring(0, 10) : '';
            const effDate   = item.effective_date ? ` · Effective: ${item.effective_date}` : '';

            return `<div style="background:white;border:1px solid var(--border);
                        border-left:4px solid ${sevBorder(item.severity)};
                        border-radius:6px;padding:14px 16px;margin-bottom:10px;">
                <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px;">
                    <div style="flex:1;margin-right:12px;">
                        <div style="font-weight:700;font-size:13px;line-height:1.4;margin-bottom:4px;">${escapeHtml(item.title)}</div>
                        <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;">
                            <span style="font-size:10px;font-weight:700;padding:2px 8px;border-radius:10px;
                                background:${sevBg(item.severity)};color:${sevColor(item.severity)};">${escapeHtml(item.severity)}</span>
                            <span style="font-size:10px;padding:2px 8px;border-radius:10px;background:#EFF6FF;color:var(--primary);font-weight:600;">${escapeHtml(item.country)}</span>
                            <span style="font-size:10px;padding:2px 8px;border-radius:10px;background:#F8FAFC;color:var(--text-muted);font-family:var(--font-mono);">${escapeHtml(item.category)}</span>
                            ${pubDate ? `<span style="font-size:10px;color:var(--text-muted);font-family:var(--font-mono);">${pubDate}${effDate}</span>` : ''}
                        </div>
                    </div>
                    ${item.url ? `<a href="${escapeHtml(item.url)}" target="_blank"
                        style="font-size:10px;color:var(--primary);text-decoration:none;font-family:var(--font-mono);white-space:nowrap;flex-shrink:0;">Source →</a>` : ''}
                </div>
                ${(hasBefore || hasAfter) ? `
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px;">
                    ${hasBefore ? `<div style="background:#FEF9C3;border:1px solid #FDE047;border-radius:4px;padding:8px 10px;">
                        <div style="font-size:9px;font-weight:700;text-transform:uppercase;color:#92400E;margin-bottom:4px;font-family:var(--font-mono);">Before</div>
                        <div style="font-size:11px;color:#78350F;line-height:1.4;">${escapeHtml(item.change_before)}</div>
                    </div>` : '<div></div>'}
                    ${hasAfter ? `<div style="background:#DCFCE7;border:1px solid #86EFAC;border-radius:4px;padding:8px 10px;">
                        <div style="font-size:9px;font-weight:700;text-transform:uppercase;color:#166534;margin-bottom:4px;font-family:var(--font-mono);">After</div>
                        <div style="font-size:11px;color:#14532D;line-height:1.4;">${escapeHtml(item.change_after)}</div>
                    </div>` : '<div></div>'}
                </div>` : ''}
                ${item.treasury_impact ? `
                <div style="font-size:11px;color:var(--text-secondary);line-height:1.5;margin-bottom:6px;">
                    <span style="font-size:9px;font-weight:700;text-transform:uppercase;color:var(--text-muted);font-family:var(--font-mono);">Impact: </span>
                    ${escapeHtml(item.treasury_impact)}</div>` : ''}
                ${hasAction ? `
                <div style="background:#EFF6FF;border:1px solid #BFDBFE;border-radius:4px;padding:8px 10px;">
                    <div style="font-size:9px;font-weight:700;text-transform:uppercase;color:var(--primary);margin-bottom:3px;font-family:var(--font-mono);">▶ Action Required</div>
                    <div style="font-size:11px;color:var(--primary);line-height:1.4;">${escapeHtml(item.action_required)}</div>
                </div>` : ''}
            </div>`;
        }).join('');
    } catch(e) {
        list.innerHTML = `<div style="text-align:center;padding:32px;color:var(--text-muted);font-size:11px;">
            Failed to load: ${escapeHtml(e.message)}</div>`;
    }
}

function _renderRegFilters(summary) {
    const countryEl  = document.getElementById('regCountryFilters');
    const categoryEl = document.getElementById('regCategoryFilters');
    if (countryEl) countryEl.innerHTML = (summary.countries || []).map(c =>
        `<button class="reg-filter${regFilters.country === c.country ? ' active' : ''}"
            data-filter-type="country" data-filter-val="${escapeHtml(c.country)}"
            onclick="setRegFilter('country','${escapeHtml(c.country)}',this)">
            ${escapeHtml(c.country)} <span style="opacity:0.7">(${c.count})</span>
        </button>`).join('');
    if (categoryEl) categoryEl.innerHTML = (summary.categories || []).map(c =>
        `<button class="reg-filter${regFilters.category === c.category ? ' active' : ''}"
            data-filter-type="category" data-filter-val="${escapeHtml(c.category)}"
            onclick="setRegFilter('category','${escapeHtml(c.category)}',this)">
            ${escapeHtml(c.category)} <span style="opacity:0.7">(${c.count})</span>
        </button>`).join('');
}

async function refreshRegulatoryAI() {
    const btn = event.currentTarget;
    btn.textContent = '⟳ Analysing…';
    btn.disabled = true;
    try {
        const r = await fetch('/api/regulatory/refresh', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ force: false }),
        });
        const d = await r.json();
        btn.textContent = '✦ Re-analyse';
        btn.disabled    = false;
        if (d.status === 'success') {
            loadRegulatory();
            showAlert(`Analysis complete: ${d.result?.regulatory || 0} regulatory changes found`);
        }
    } catch(e) {
        btn.textContent = '✦ Re-analyse';
        btn.disabled    = false;
    }
}
