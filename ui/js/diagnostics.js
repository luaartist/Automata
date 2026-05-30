/* System Diagnostics & Health Monitor */

async function checkHealth() {
    try {
        // Fetch Go_Red backend health
        const leanResp = await fetch(`${API_BASE}/health`);
        const leanData = await leanResp.json();
        updateBadge('badge-lean', leanData.checks.lean_db === 'ok', `Lean DB: ${leanData.checks.lean_db}`);
        updateBadge('badge-tph', leanData.checks.tph_server === 'ok', `TPH GPU: ${leanData.checks.tph_server}`);
        const errBanner = document.getElementById('api-error-banner');
        if (errBanner) errBanner.style.display = 'none';
    } catch (e) {
        updateBadge('badge-lean', false, 'Lean DB: Connection Error');
        updateBadge('badge-tph', false, 'TPH GPU: Connection Error');
        const errBanner = document.getElementById('api-error-banner');
        if (errBanner) errBanner.style.display = 'block';
    }

    try {
        // Fetch FastAPI port :8002 backend status
        const apiResp = await fetch(`${API_8002}/api/status`);
        const apiData = await apiResp.json();
        updateBadge('badge-python', apiData.status === 'ONLINE', `Python status: ONLINE`);
        updateBadge('badge-api-8002', true, `API :8002: ONLINE`);
        const gpuName = document.getElementById('gpu-telemetry-name');
        if (gpuName) gpuName.innerText = apiData.hardware.gpu;
    } catch (e) {
        updateBadge('badge-python', false, 'Python APIs: OFFLINE');
        updateBadge('badge-api-8002', false, 'API :8002: Connection Error');
    }
}

function updateBadge(id, online, text) {
    const badge = document.getElementById(id);
    badge.className = `badge-status ${online ? 'online' : 'offline'}`;
    badge.innerHTML = `
        <i class="fas fa-${online ? 'check-circle' : 'times-circle'}"></i>
        <span>${escapeHtml(text)}</span>
    `;
}

function escapeHtml(str) {
    return str.toString()
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}
