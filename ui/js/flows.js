/* Flow Management and Stream Logging */

let activeFlow = null;
let eventSource = null;
let flowDefinitionsByName = new Map();

async function loadFlows() {
    try {
        const resp = await fetch(`${API_BASE}/flows`);
        const data = await resp.json();
        setFlowDefinitions(data.flows || []);
        renderFlows(data.flows || []);
    } catch (e) {
        setFlowDefinitions([]);
        renderStaticFlowButtons();
    }
}

function renderStaticFlowButtons() {
    const container = document.getElementById('flow-buttons');
    const flowGroups = {
        'Lean DB': [
            { name: 'lean_sorry_scan', desc: 'Scan for sorry statements', icon: 'search' },
            { name: 'lean_float_scan', desc: 'Floating symbols query', icon: 'ghost' },
            { name: 'lean_file_interconnect', desc: 'File dependencies', icon: 'project-diagram' },
            { name: 'lean_db_health', desc: 'Database health', icon: 'heartbeat' },
            { name: 'quick_proof_report', desc: 'Proof status report', icon: 'file-alt' },
            { name: 'lean_proof_audit', desc: 'Proof map audit (summary)', icon: 'clipboard-list' }
        ],
        'TPH GPU': [
            { name: 'tph_inference', desc: 'TPH model inference', icon: 'brain' },
            { name: 'tph_status', desc: 'TPH server status', icon: 'server' }
        ],
        'System': [
            { name: 'env_check', desc: 'Python environment', icon: 'check-circle' },
            { name: 'gpu_check', desc: 'AMD GPU status', icon: 'microchip' }
        ]
    };
    
    let html = '<div>';
    for (const [groupName, flows] of Object.entries(flowGroups)) {
        html += `<div class="flow-group-title">${groupName}</div>`;
        flows.forEach(flow => {
            html += `
                <button class="flow-btn" onclick="runFlow('${flow.name}')" id="flow-${flow.name}">
                    <i class="fas fa-${flow.icon}"></i>
                    <span>${flow.desc}</span>
                </button>
            `;
        });
    }
    html += '</div>';
    container.innerHTML = html;
}

function setFlowDefinitions(flows) {
    flowDefinitionsByName = new Map((flows || []).map(flow => {
        const paramsSchema = typeof flow.params_schema === 'string'
            ? safeParseJson(flow.params_schema, {})
            : (flow.params_schema || {});
        return [flow.name, { ...flow, params_schema: paramsSchema }];
    }));
}

function safeParseJson(str, fallback) {
    try { return JSON.parse(str); } catch (e) { return fallback; }
}

function renderFlows(flows) {
    const container = document.getElementById('flow-buttons');
    if (flows.length === 0) {
        renderStaticFlowButtons();
        return;
    }
    
    let html = '';
    flows.forEach(flow => {
        html += `
            <button class="flow-btn" onclick="runFlow('${flow.name}')" id="flow-${flow.name}">
                <i class="fas fa-play"></i>
                <span>${escapeHtml(flow.name)}</span>
            </button>
        `;
    });
    container.innerHTML = html;
}

async function runFlow(flowName) {
    clearLog();
    const logPanel = document.getElementById('log-panel');
    if (logPanel) logPanel.innerHTML = `<div class="text-info"><i class="fas fa-spinner fa-spin"></i> Triggering flow "${escapeHtml(flowName)}"...</div>`;
    
    try {
        const resp = await fetch(`${API_BASE}/flows/run/${flowName}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({})
        });
        const data = await resp.json();
        
        if (data.session_id) {
            subscribeToLogs(data.session_id);
        } else {
            if (logPanel) logPanel.innerHTML = `<div class="text-danger">Failed to start: no session ID returned.</div>`;
        }
    } catch (e) {
        if (logPanel) logPanel.innerHTML = `<div class="text-danger">Error running flow: ${e.message}</div>`;
    }
}

function subscribeToLogs(sessionId) {
    if (eventSource) eventSource.close();
    
    const logPanel = document.getElementById('log-panel');
    if (logPanel) logPanel.innerHTML = `<div class="text-success"><i class="fas fa-satellite-dish"></i> Streaming live logs for session: ${sessionId}</div>`;
    
    eventSource = new EventSource(`${API_BASE}/flows/logs/${sessionId}`);
    
    eventSource.onmessage = (event) => {
        const data = JSON.parse(event.data);
        const line = document.createElement('div');
        line.style.marginBottom = '4px';
        
        if (data.type === 'stdout') {
            line.textContent = data.message;
        } else if (data.type === 'stderr') {
            line.style.color = '#ff8b80';
            line.textContent = data.message;
        } else if (data.type === 'status') {
            line.className = 'text-warning fw-bold';
            line.textContent = `[STATUS] ${data.message}`;
            if (data.message === 'COMPLETED' || data.message === 'FAILED') {
                eventSource.close();
                if (typeof loadTickets === 'function') loadTickets();
            }
        }
        if (logPanel) {
            logPanel.appendChild(line);
            logPanel.scrollTop = logPanel.scrollHeight;
        }
    };
    
    eventSource.onerror = () => {
        eventSource.close();
        const errLine = document.createElement('div');
        errLine.className = 'text-danger';
        errLine.textContent = '[Connection to log stream interrupted]';
        if (logPanel) logPanel.appendChild(errLine);
    };
}

function clearLog() {
    const logPanel = document.getElementById('log-panel');
    if (logPanel) {
        logPanel.innerHTML = `
            <div class="empty-state">
                <i class="fas fa-terminal"></i>
                <p>Log console cleared.</p>
            </div>
        `;
    }
}
