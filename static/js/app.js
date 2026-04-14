/* ============================================================
   ARGUS — Application Bootstrap
   Entry point: wires up all modules on DOMContentLoaded
   ============================================================ */

const API_BASE = '/api';

document.addEventListener('DOMContentLoaded', async () => {
    initSidebar();
    startClock();
    await loadPersonas();

    // Sync home role name with loaded persona
    const activeP = availablePersonas.find(p => p.key === currentPersona);
    if (activeP) {
        const homeRole = document.getElementById('homeRoleName');
        if (homeRole) homeRole.textContent = activeP.label;
    }

    // Keyboard shortcut: Ctrl+B toggles sidebar
    document.addEventListener('keydown', e => {
        if (e.ctrlKey && e.key.toLowerCase() === 'b') {
            e.preventDefault();
            toggleSidebar();
        }
    });

    // Init chat sessions
    if (!chatSessions.length) newChatSession();
    else { currentSessionId = chatSessions[0].id; renderChatHistory(); }

    // Load any existing pipeline results
    try { await loadResults(); } catch(e) { /* no results yet */ }

    // Populate home risk category cards
    loadRiskCategories();

    // Render geospatial risk map
    try { await loadRiskMap(); } catch(e) { /* map unavailable */ }
});
