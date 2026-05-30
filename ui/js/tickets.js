/* Forensic Audit Tickets Management */

async function loadTickets() {
    try {
        const resp = await fetch(`${API_BASE}/tickets`);
        const data = await resp.json();
        renderTicketsSummary(data.tickets || []);
        renderTicketsTable(data.tickets || []);
    } catch (e) {
        console.error('Failed to load tickets:', e);
    }
}

function renderTicketsSummary(tickets) {
    const container = document.getElementById('tickets-container');
    if (tickets.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <i class="fas fa-circle-info"></i>
                <p>No recent audit logs available.</p>
            </div>
        `;
        return;
    }
    
    let html = '';
    tickets.slice(0, 5).forEach(t => {
        html += `
            <div class="glass-panel p-2 mb-2" style="font-size: 0.85em; background: rgba(255,255,255,0.01);">
                <div class="d-flex justify-content-between mb-1">
                    <span class="text-info fw-bold">Session ${escapeHtml(t.id.slice(0,8))}</span>
                    <span class="badge bg-success">\ ${escapeHtml(t.status)}</span>
                </div>
                <div class="text-white-50" style="font-size: 0.9em; word-break: break-all;">
                    Scope: ${escapeHtml(t.path || '/root/workspace')}
                </div>
            </div>
        `;
    });
    container.innerHTML = html;
}

function renderTicketsTable(tickets) {
    const tbody = document.getElementById('results-table-body');
    
    tbody.innerHTML = '';
    tickets.forEach(t => {
        tbody.innerHTML += `
            <tr>
                <td class="font-monospace text-info">${escapeHtml(t.id)}</td>
                <td>${new Date().toLocaleTimeString()}</td>
                <td><span class="badge bg-success">${escapeHtml(t.status)}</span></td>
                <td class="font-monospace text-white-50" style="font-size: 0.85em;">${escapeHtml(t.path || '/root/workspace')}</td>
                <td>
                    <button class="btn btn-sm btn-outline-info" onclick="switchMainTab('#scanner-panel', 'btn-tab-scanner')">Inspect</button>
                </td>
            </tr>
        `;
    });
}
