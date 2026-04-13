/* ============================================================
   ARGUS — Persona Selection Modal
   ============================================================ */

let currentPersona      = localStorage.getItem('argusPersona') || 'cash_manager';
let availablePersonas   = [];
let selectedPersonaKey  = localStorage.getItem('argusPersona') || 'cash_manager';

async function loadPersonas() {
    try {
        const r = await fetch(`${API_BASE}/personas`);
        const d = await r.json();
        availablePersonas = d.personas || [];

        const grid = document.getElementById('personaCardGrid');
        if (grid) {
            grid.innerHTML = availablePersonas.map(p => {
                const icon = p.icon || '👤';
                const isSelected = p.key === selectedPersonaKey;
                return `
                <div class="persona-card${isSelected ? ' selected' : ''}"
                     onclick="selectPersonaCard('${p.key}')"
                     data-key="${p.key}">
                    <div class="persona-card-icon">${icon}</div>
                    <div class="persona-card-label">${p.label}</div>
                    <div class="persona-card-summary">${p.summary || ''}</div>
                </div>`;
            }).join('');
        }

        selectPersonaCard(selectedPersonaKey);

        const active = availablePersonas.find(p => p.key === currentPersona);
        if (active) {
            const sidebarRole = document.getElementById('sidebarRoleName');
            const homeRole    = document.getElementById('homeRoleName');
            if (sidebarRole) sidebarRole.textContent = active.label;
            if (homeRole)    homeRole.textContent    = active.label;
        }
    } catch(e) {
        console.warn('Could not load personas:', e);
    }
}

function selectPersonaCard(key) {
    selectedPersonaKey = key;
    document.querySelectorAll('.persona-card').forEach(card => {
        card.classList.toggle('selected', card.dataset.key === key);
    });
    const p = availablePersonas.find(x => x.key === key);
    if (p) {
        const label  = document.getElementById('previewLabel');
        const copy   = document.getElementById('loginPersonaCopy');
        const iconEl = document.getElementById('previewIcon');
        if (label)  label.textContent  = p.label;
        if (copy)   copy.textContent   = p.summary || '';
        if (iconEl) iconEl.textContent = p.icon || '👤';
    }
}

function openPersonaLogin() {
    document.getElementById('personaLoginOverlay').classList.add('show');
    selectPersonaCard(currentPersona);
}

function closePersonaLogin() {
    document.getElementById('personaLoginOverlay').classList.remove('show');
}

function handlePersonaOverlayClick(e) {
    if (e.target === document.getElementById('personaLoginOverlay')) closePersonaLogin();
}

function savePersonaSelection() {
    currentPersona = selectedPersonaKey;
    localStorage.setItem('argusPersona', currentPersona);
    const active = availablePersonas.find(p => p.key === currentPersona);
    if (active) {
        const sidebarRole = document.getElementById('sidebarRoleName');
        const homeRole    = document.getElementById('homeRoleName');
        if (sidebarRole) sidebarRole.textContent = active.label;
        if (homeRole)    homeRole.textContent    = active.label;
    }
    closePersonaLogin();
}
