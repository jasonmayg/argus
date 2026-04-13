/* ============================================================
   ARGUS — Pipeline Execution & Status Polling
   ============================================================ */

let isExecuting = false;

async function executePipeline() {
    if (isExecuting) return;
    isExecuting = true;

    const execBtn = document.getElementById('executeBtn');
    if (execBtn) execBtn.disabled = true;

    document.getElementById('pipelineCard').style.display = 'block';

    const badge = document.getElementById('pipelineStatusBadge');
    if (badge) { badge.className = 'status-badge status-running'; badge.textContent = 'Running'; }

    showAlert('Intelligence pipeline engaged — monitoring execution…');
    log('Pipeline initiated by operator.');

    try {
        const r = await fetch(`${API_BASE}/execute`, { method: 'POST' });
        if (r.ok) startStatusPolling();
        else throw new Error('Execute failed');
    } catch(e) {
        log('Pipeline start error: ' + e.message, 'error');
        resetPipelineUI();
    }
}

function startStatusPolling() {
    const itv = setInterval(async () => {
        try {
            const r = await fetch(`${API_BASE}/status`);
            const s = await r.json();
            updateStages(s.agents_status);

            if (!s.is_running) {
                clearInterval(itv);
                resetPipelineUI();
                document.getElementById('pipelineCard').style.display = 'none';
                await loadResults();
                showAlert('Operational cycle complete — generating intelligence brief…');
                log('Pipeline complete.', 'success');
                const lastRunKpi = document.getElementById('kpiLastRun');
                if (lastRunKpi) lastRunKpi.textContent = 'Just now';
                await generateBrief();
                loadRiskCategories();
            }
        } catch(e) {
            clearInterval(itv);
            resetPipelineUI();
        }
    }, 1500);
}

function resetPipelineUI() {
    isExecuting = false;
    const execBtn = document.getElementById('executeBtn');
    if (execBtn) execBtn.disabled = false;
    const badge = document.getElementById('pipelineStatusBadge');
    if (badge) { badge.className = 'status-badge status-idle'; badge.textContent = 'Idle'; }
}

function updateStages(stats) {
    const stages = ['stage1','stage2','stage3','stage4','stage5'];
    const keys   = ['news_gatherer','risk_classifier','database_manager','analyzer','reporter'];
    keys.forEach((k, i) => {
        const el = document.getElementById(stages[i]);
        if (!el) return;
        if (stats[k]?.status === 'running') el.className = 'stage active';
        else if (stats[k]?.status === 'success') el.className = 'stage done';
    });
}
