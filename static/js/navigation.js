/* ============================================================
   ARGUS — Page Navigation
   ============================================================ */

const PAGE_TITLES = {
    home:     'Home',
    risk:     'Risk Monitor',
    chat:     'Guardian Chat',
    cash:     'Cash Position',
    news:     'Regulatory',
    actions:  'Actions',
    data:     'Data Source',
    settings: 'Persona Settings',
};

// ── Page load cache — track last load time per page ───────
const _pageLoadedAt = {};
const PAGE_CACHE_TTL = 5 * 60 * 1000; // 5 minutes in ms

function _shouldReload(pageId) {
    const last = _pageLoadedAt[pageId];
    if (!last) return true;
    return (Date.now() - last) > PAGE_CACHE_TTL;
}

function _markLoaded(pageId) {
    _pageLoadedAt[pageId] = Date.now();
}

// Call this after Daily Jobs completes to force a fresh load
function invalidatePageCache(pageId) {
    if (pageId) {
        delete _pageLoadedAt[pageId];
    } else {
        Object.keys(_pageLoadedAt).forEach(k => delete _pageLoadedAt[k]);
    }
}

function showPage(pageId, navEl) {
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));

    document.getElementById('page-' + pageId).classList.add('active');
    document.getElementById('topbarTitle').textContent = PAGE_TITLES[pageId] || pageId;

    if (navEl) navEl.classList.add('active');

    // Lazy-load page data — only reload if not cached or TTL expired
    if (pageId === 'risk') {
        if (_shouldReload('risk')) {
            loadGuardianDashboard();
            loadDataAlerts();
            fetch(`${API_BASE}/results`).then(r => r.json()).then(d => {
                if (d.status === 'success') renderRiskMatrix(d.stages.analysis?.analysis);
            }).catch(() => {});
            _markLoaded('risk');
        } else {
            // Still apply deep-link filters even if not reloading
            const catFilter = localStorage.getItem('argusRiskCategoryFilter');
            if (catFilter) {
                localStorage.removeItem('argusRiskCategoryFilter');
                setTimeout(() => _applyCategoryFilter(catFilter), 100);
            }
        }
    }
    if (pageId === 'news') {
        if (_shouldReload('news')) { loadRegulatory(); _markLoaded('news'); }
    }
    if (pageId === 'actions')  loadActions();
    if (pageId === 'chat')     renderChatHistory();
    if (pageId === 'cash') {
        if (_shouldReload('cash')) { renderCashPage(); _markLoaded('cash'); }
    }
    if (pageId === 'settings') loadPersonaSettings();
}

function toggleSection(id) {
    const el = document.getElementById(id);
    const isHidden = el.style.display === 'none';
    el.style.display = isHidden ? 'block' : 'none';
    const labelId = id === 'agentGrid' ? 'agentToggleLabel' : 'logsToggleLabel';
    const labelEl = document.getElementById(labelId);
    if (labelEl) labelEl.textContent = isHidden ? 'Hide ▴' : 'Show ▾';
}

function toggleAgentDetail(card) {
    const det = card.querySelector('.agent-detail');
    det.style.display = det.style.display === 'none' ? 'block' : 'none';
}

function toggleHomeLogs() {
    const inner  = document.getElementById('homeLogsInner');
    const toggle = document.getElementById('homeLogsToggle');
    if (!inner) return;
    const show = inner.style.display === 'none';
    inner.style.display = show ? 'block' : 'none';
    if (toggle) toggle.textContent = show ? 'Hide ▴' : 'Show ▾';
}