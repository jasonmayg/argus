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

// ── URL ↔ tab map ────────────────────────────────────────
// Every SPA tab gets a real URL so users can bookmark / share /
// back-button to a specific view (and so navigating away to
// /data or /admin and coming back lands on the right tab instead
// of always resetting to Home). Server-side routes for these
// paths live in `routes/pipeline_routes.py` — all render the
// same dashboard.html; the actual tab activation happens here.
//
// `data` and `settings` deliberately have no path entries: the
// Data Source nav-item embeds /data via iframe, and Persona
// Settings is a modal — neither benefits from a top-level URL.
const PATH_TO_PAGE = {
    '/':                 'home',
    '/home':             'home',
    '/risk-monitor':     'risk',
    '/decision-support': 'actions',
    '/regulatory':       'news',
    '/cash-position':    'cash',
    '/chat':             'chat',
};
const PAGE_TO_PATH = {
    home:    '/',
    risk:    '/risk-monitor',
    actions: '/decision-support',
    news:    '/regulatory',
    cash:    '/cash-position',
    chat:    '/chat',
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

    // Keep URL in step with the active tab so reloads / back-forward
    // land where the user expects. Skip the push when the URL already
    // matches — this is the path on initial boot and on popstate
    // (browser already changed the URL by the time we re-render).
    const targetPath = PAGE_TO_PATH[pageId];
    if (targetPath && window.location.pathname !== targetPath) {
        history.pushState({ pageId }, '', targetPath);
    }
}

// ── URL → tab boot + back/forward handling ──────────────
// On a fresh page load (or hard refresh of /risk-monitor etc.)
// the server returned dashboard.html with `page-home.active`
// hard-coded in the HTML. Read the URL and activate the right
// tab. Skip when we're already on /home so we don't double-fire
// the Home loaders.
window.addEventListener('DOMContentLoaded', () => {
    const initialPage = PATH_TO_PAGE[window.location.pathname];
    if (initialPage && initialPage !== 'home') {
        const navEl = document.querySelector(
            `.nav-item[onclick*="showPage('${initialPage}'"]`
        );
        showPage(initialPage, navEl);
    }
});

// Browser back/forward — `popstate` fires after the URL has
// already changed, so just re-derive the active tab from it.
// showPage's URL-sync check will be a no-op (paths already match).
window.addEventListener('popstate', (e) => {
    const pageId =
        (e.state && e.state.pageId) ||
        PATH_TO_PAGE[window.location.pathname] ||
        'home';
    const navEl = document.querySelector(
        `.nav-item[onclick*="showPage('${pageId}'"]`
    );
    showPage(pageId, navEl);
});

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