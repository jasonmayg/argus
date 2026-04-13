/* ============================================================
   ARGUS — Utilities
   Shared helpers: escaping, alert banner, log console, clock
   ============================================================ */

function escapeHtml(t) {
    return t == null ? '' : String(t)
        .replace(/&/g,'&amp;').replace(/</g,'&lt;')
        .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function showAlert(msg) {
    const el = document.getElementById('alertBanner');
    el.textContent = msg;
    el.style.display = 'block';
    setTimeout(() => el.style.display = 'none', 5000);
}

function log(msg, type = '') {
    const container = document.getElementById('logsContainer');
    if (!container) return;
    const entry = document.createElement('div');
    entry.className = `log-entry${type ? ' log-' + type : ''}`;
    const ts = new Date().toISOString().replace('T', ' ').substr(0, 19);
    entry.textContent = `[${ts}] ${msg}`;
    container.appendChild(entry);
    container.scrollTop = container.scrollHeight;
}

function startClock() {
    function tick() {
        const now = new Date();
        const iso = now.toISOString().replace('T', ' ').substr(0, 19);
        const topClock = document.getElementById('topbarClock');
        const sidClock = document.getElementById('sidebarClock');
        if (topClock) topClock.textContent = iso + ' UTC';
        if (sidClock) sidClock.textContent = now.toUTCString().substr(17, 8) + ' UTC';
    }
    tick();
    setInterval(tick, 1000);
}

async function typewriterEffect(el, text) {
    el.innerHTML = '';
    const cursor = document.createElement('span');
    cursor.className = 'typewriter-cursor';
    el.appendChild(cursor);
    for (const char of text) {
        cursor.insertAdjacentText('beforebegin', char);
        await new Promise(r => setTimeout(r, 18));
    }
    cursor.remove();
}
