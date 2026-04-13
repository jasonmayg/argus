/* ============================================================
   ARGUS — News / Regulatory Feed
   ============================================================ */

let selectedNewsIds = new Set();
let allNewsItems    = [];

function toggleNewsSelect(id, btn) {
    if (selectedNewsIds.has(id)) {
        selectedNewsIds.delete(id);
        btn.style.background = 'white';
        btn.style.color      = 'var(--text-muted)';
        btn.textContent      = '+';
    } else {
        selectedNewsIds.add(id);
        btn.style.background = 'var(--primary)';
        btn.style.color      = 'white';
        btn.textContent      = '✓';
    }
    updateNewsSelectionUI();
}

function updateNewsSelectionUI() {
    const count     = selectedNewsIds.size;
    const countEl   = document.getElementById('newsSelectedCount');
    const analyzeEl = document.getElementById('analyzeSelectedBtn');
    if (countEl)   { countEl.textContent = count + ' selected'; countEl.style.display = count ? '' : 'none'; }
    if (analyzeEl) { analyzeEl.style.display = count ? '' : 'none'; }
}

function closeNewsAnalysis() {
    document.getElementById('newsAnalysisPanel').style.display = 'none';
    selectedNewsIds.clear();
    updateNewsSelectionUI();
    document.querySelectorAll('.news-select-btn').forEach(btn => {
        btn.style.background = 'white';
        btn.style.color      = 'var(--text-muted)';
        btn.textContent      = '+';
    });
}

async function analyzeSelectedNews() {
    if (!selectedNewsIds.size) return;

    const panel = document.getElementById('newsAnalysisPanel');
    const body  = document.getElementById('newsAnalysisBody');
    panel.style.display = 'block';
    body.innerHTML = '<span style="color:var(--text-muted);font-style:italic;">Analysing selected news<span class="typewriter-cursor"></span></span>';
    panel.scrollIntoView({ behavior: 'smooth', block: 'start' });

    const selected = allNewsItems.filter(item => selectedNewsIds.has(String(item.id || item.title)));
    const headlines = selected.map((n, i) =>
        `${i + 1}. ${n.title} [${n.risk_category || n.risk_level || 'General'}]`
    ).join('\n');

    try {
        const r = await fetch(`${API_BASE}/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: `As a treasury risk analyst, analyse these ${selected.length} news items for me:\n\n${headlines}\n\nFor each item briefly explain:\n1. What is the hidden implication for treasury operations?\n2. What specific action do you recommend?\n\nThen provide an overall assessment of how these signals interact.`,
                persona: currentPersona,
                history: [],
            }),
        });
        const d = await r.json();
        const reply = d.reply || d.message || 'No response.';
        body.innerHTML = '';
        const cursor = document.createElement('span');
        cursor.className = 'typewriter-cursor';
        body.appendChild(cursor);
        for (const char of reply) {
            cursor.insertAdjacentText('beforebegin', char);
            await new Promise(res => setTimeout(res, 12));
        }
        cursor.remove();
    } catch(e) {
        body.innerHTML = `<span style="color:var(--danger-text);">Analysis failed: ${escapeHtml(e.message)}</span>`;
    }
}

async function loadNews() {
    const grid = document.getElementById('newsGrid');
    selectedNewsIds.clear();
    updateNewsSelectionUI();
    grid.innerHTML = `<div style="grid-column:1/-1;text-align:center;padding:48px;color:var(--text-muted);">
        <div style="font-size:24px;margin-bottom:12px;">◉</div>
        <div>Fetching news feed…</div>
    </div>`;

    try {
        const r = await fetch(`${API_BASE}/news`);
        const d = await r.json();
        allNewsItems = d.items || d.news || [];

        if (!allNewsItems.length) {
            grid.innerHTML = `<div style="grid-column:1/-1;text-align:center;padding:48px;color:var(--text-muted);">
                No news available. Run the pipeline first.</div>`;
            return;
        }

        const lastFetch = document.getElementById('newsLastFetch');
        if (lastFetch) lastFetch.textContent =
            'Updated ' + new Date().toISOString().replace('T', ' ').substr(0, 16) + ' UTC · ' + allNewsItems.length + ' articles';

        const levelColor = l => l === 'high' ? 'var(--danger-text)'  : l === 'medium' ? 'var(--warning-text)'  : 'var(--success-text)';
        const levelBg    = l => l === 'high' ? 'var(--danger-bg)'    : l === 'medium' ? 'var(--warning-bg)'    : 'var(--success-bg)';

        grid.innerHTML = allNewsItems.map(item => {
            const id  = String(item.id || item.title);
            const lvl = item.risk_level || 'low';
            const cat = item.risk_category || '';
            return `<div class="news-card" style="position:relative;">
                <button class="news-select-btn"
                    onclick="toggleNewsSelect('${escapeHtml(id)}', this)"
                    style="position:absolute;top:10px;right:10px;width:22px;height:22px;
                           border-radius:50%;border:1px solid var(--border);background:white;
                           color:var(--text-muted);font-size:14px;font-weight:700;
                           display:flex;align-items:center;justify-content:center;
                           cursor:pointer;padding:0;line-height:1;transition:all 0.15s;">+</button>
                <div class="news-card-meta" style="padding-right:28px;">
                    <span class="news-source">${escapeHtml(item.source || 'ARGUS DB')}</span>
                    <span style="font-size:10px;font-weight:700;padding:2px 7px;border-radius:10px;
                                 background:${levelBg(lvl)};color:${levelColor(lvl)};font-family:var(--font-mono);">${lvl}</span>
                </div>
                <div class="news-card-title" style="padding-right:8px;">${escapeHtml(item.title || '')}</div>
                ${cat ? `<div style="font-size:10px;color:var(--primary);font-family:var(--font-mono);margin:4px 0 6px;font-weight:600;">${escapeHtml(cat)}</div>` : ''}
                ${item.url ? `<a href="${escapeHtml(item.url)}" target="_blank"
                    style="display:inline-block;margin-top:6px;font-size:11px;color:var(--primary);text-decoration:none;font-family:var(--font-mono);">Read →</a>` : ''}
            </div>`;
        }).join('');
    } catch(e) {
        grid.innerHTML = `<div style="grid-column:1/-1;text-align:center;padding:48px;color:var(--text-muted);">
            Failed to load news: ${escapeHtml(e.message)}</div>`;
    }
}
