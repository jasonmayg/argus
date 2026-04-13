/* ============================================================
   ARGUS — Intelligence Brief Generation
   ============================================================ */

async function generateBrief() {
    const btn  = document.getElementById('briefBtn');
    const body = document.getElementById('briefBody');
    const meta = document.getElementById('briefMeta');

    btn.disabled = true;
    btn.textContent = '…';
    body.className = 'brief-body loading';
    body.innerHTML = 'Generating intelligence brief<span class="typewriter-cursor"></span>';

    try {
        const r = await fetch(`${API_BASE}/brief`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ persona: currentPersona }),
        });
        const d = await r.json();

        if (d.status === 'success') {
            body.className = 'brief-body';
            await typewriterEffect(body, d.brief);
            meta.textContent = 'Generated ' + new Date().toISOString().replace('T', ' ').substr(0, 16) + ' UTC';
            if (d.signals?.length) {
                document.getElementById('briefSignals').innerHTML = d.signals
                    .map(s => `<span class="signal-pill ${s.level}">${s.label}</span>`)
                    .join('');
            }
        } else {
            body.className = 'brief-body loading';
            body.textContent = d.message || 'Brief generation failed. Run the pipeline first.';
        }
    } catch(e) {
        body.className = 'brief-body loading';
        body.textContent = 'Could not reach /api/brief — ensure backend is running.';
    }

    btn.disabled = false;
    btn.textContent = 'Regenerate';
}
