/* ============================================================
   ARGUS — Page Navigation
   ============================================================ */

const PAGE_TITLES = {
    home:     'Home',
    risk:     'Risk Monitor',
    chat:     'Guardian Chat',
    cash:     'Cash Position',
    news:     'Regulatory',
    data:     'Data Source',
    settings: 'Persona Settings',
};

function showPage(pageId, navEl) {
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));

    document.getElementById('page-' + pageId).classList.add('active');
    document.getElementById('topbarTitle').textContent = PAGE_TITLES[pageId] || pageId;

    if (navEl) navEl.classList.add('active');

    // Lazy-load page data
    if (pageId === 'risk') {
        loadGuardianDashboard();
        loadDataAlerts();
        fetch(`${API_BASE}/results`).then(r => r.json()).then(d => {
            if (d.status === 'success') renderRiskMatrix(d.stages.analysis?.analysis);
        }).catch(() => {});
    }
    if (pageId === 'news')     loadRegulatory();
    if (pageId === 'chat')     renderChatHistory();
    if (pageId === 'cash')     renderCashPage();
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
