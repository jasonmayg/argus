/* ============================================================
   ARGUS — Today's Recommendations
   Calls POST /api/recommendations on page load and persona switch.
   Renders focus / where / actions into #recommendationsBody.
   ============================================================ */

let _recCurrentPersona = null;

// ── Main entry point ─────────────────────────────────────────
async function loadRecommendations(persona) {
    persona = persona || localStorage.getItem('argusPersona') || 'cash_manager';

    // Skip if same persona already loaded
    if (persona === _recCurrentPersona) return;
    _recCurrentPersona = persona;

    const body = document.getElementById('recommendationsBody');
    if (!body) return;

    // Show loading state
    body.innerHTML = `
        <div style="display:flex;align-items:center;gap:8px;padding:12px 0;
                    color:var(--text-muted);font-size:11px;font-family:var(--font-mono);">
            <span style="animation:spin 1s linear infinite;display:inline-block;">◌</span>
            Generating recommendations for ${_recLabel(persona)}…
        </div>`;

    try {
        const r = await fetch('/api/recommendations', {
            method:  'POST',
            headers: {'Content-Type': 'application/json'},
            body:    JSON.stringify({persona}),
        });
        const d = await r.json();

        if (d.status !== 'success') throw new Error(d.message || 'Failed');
        _renderRecommendations(d);

    } catch (e) {
        body.innerHTML = `
            <div style="color:var(--text-muted);font-size:11px;padding:8px 0;">
                Could not load recommendations. ${escapeHtml(e.message)}
            </div>`;
    }
}

// ── Render ────────────────────────────────────────────────────
function _renderRecommendations(d) {
    const body = document.getElementById('recommendationsBody');
    if (!body) return;

    const where   = (d.where   || []).slice(0, 3);
    const actions = (d.actions || []).slice(0, 3);
    const focus   = d.focus || '';

    const whereHtml = where.map(w => `
        <div style="display:flex;align-items:flex-start;gap:8px;padding:4px 0;">
            <span style="color:var(--primary);font-size:12px;flex-shrink:0;margin-top:1px;">→</span>
            <span style="font-size:12px;color:var(--text-secondary);">${escapeHtml(w)}</span>
        </div>`).join('');

    const actionsHtml = actions.map((a, i) => `
        <div style="display:flex;align-items:flex-start;gap:8px;padding:4px 0;">
            <span style="background:var(--primary);color:white;font-size:9px;font-weight:700;
                         border-radius:3px;padding:1px 5px;flex-shrink:0;margin-top:2px;
                         font-family:var(--font-mono);">${i + 1}</span>
            <span style="font-size:12px;color:var(--text-secondary);">${escapeHtml(a)}</span>
        </div>`).join('');

    body.innerHTML = `
        <div style="display:grid;grid-template-columns:2fr 1fr 1.4fr;gap:16px;align-items:start;">

            <!-- Focus -->
            <div>
                <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;
                            color:var(--text-muted);font-family:var(--font-mono);margin-bottom:6px;">
                    Focus Today
                </div>
                <div style="font-size:12px;color:var(--text-main);line-height:1.6;">
                    ${escapeHtml(focus)}
                </div>
            </div>

            <!-- Where to look -->
            <div>
                <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;
                            color:var(--text-muted);font-family:var(--font-mono);margin-bottom:6px;">
                    Where to Look
                </div>
                ${whereHtml || '<div style="font-size:11px;color:var(--text-muted);">—</div>'}
            </div>

            <!-- Actions -->
            <div>
                <div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;
                            color:var(--text-muted);font-family:var(--font-mono);margin-bottom:6px;">
                    Actions
                </div>
                ${actionsHtml || '<div style="font-size:11px;color:var(--text-muted);">—</div>'}
            </div>

        </div>
        <div style="margin-top:10px;padding-top:8px;border-top:1px solid var(--border);
                    font-size:9px;color:var(--text-muted);font-family:var(--font-mono);">
            Generated ${escapeHtml(d.generated_at || '')} · ${escapeHtml(d.persona_label || '')}
        </div>`;
}

// ── Helper ────────────────────────────────────────────────────
function _recLabel(persona) {
    const map = {
        cash_manager:        'Cash Manager',
        fx_manager:          'FX Manager',
        treasury_ops:        'Treasury Operations',
        treasury_technology: 'Treasury Technology',
        risk_manager:        'Risk Manager',
    };
    return map[persona] || persona.replace(/_/g, ' ');
}

// ── Spin animation (inject once) ─────────────────────────────
(function _injectSpinStyle() {
    if (document.getElementById('rec-spin-style')) return;
    const s = document.createElement('style');
    s.id = 'rec-spin-style';
    s.textContent = '@keyframes spin { to { transform: rotate(360deg); } }';
    document.head.appendChild(s);
})();
