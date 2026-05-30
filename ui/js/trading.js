/* Coinbase Paper Trading Desk Simulator */

let waveCanvas, waveCtx, waveAnimId;

function initWaveformVisualizer() {
    waveCanvas = document.getElementById('ipfsWaveCanvas');
    waveCtx = waveCanvas.getContext('2d');
    
    function resizeCanvas() {
        if (waveCanvas && waveCanvas.parentElement) {
            waveCanvas.width = waveCanvas.parentElement.clientWidth;
            waveCanvas.height = waveCanvas.parentElement.clientHeight || 140;
        }
    }
    resizeCanvas();
    window.addEventListener('resize', resizeCanvas);
    
    let offset = 0;
    function drawWaves() {
        const w = waveCanvas.width;
        const h = waveCanvas.height;
        waveCtx.clearRect(0, 0, w, h);
        
        // Draw beautiful sine waves representing market/quantum eigenvalues
        const speeds = [0.015, 0.025, 0.035];
        const amplitudes = [22, 14, 28];
        const frequencies = [0.005, 0.009, 0.007];
        const colors = [
            'rgba(0, 198, 255, 0.35)', // Neon Cyan
            'rgba(155, 89, 182, 0.25)', // Soft purple
            'rgba(0, 114, 255, 0.20)'   // Soft blue
        ];
        
        for (let i = 0; i < 3; i++) {
            waveCtx.beginPath();
            waveCtx.strokeStyle = colors[i];
            waveCtx.lineWidth = i === 0 ? 2 : 1;
            
            offset += speeds[i] * 0.02;
            
            for (let x = 0; x < w; x++) {
                const y = h / 2 + Math.sin(x * frequencies[i] + offset) * amplitudes[i] * Math.cos(x * 0.002);
                if (x === 0) waveCtx.moveTo(x, y);
                else waveCtx.lineTo(x, y);
            }
            waveCtx.stroke();
        }
        waveAnimId = requestAnimationFrame(drawWaves);
    }
    
    if (waveAnimId) cancelAnimationFrame(waveAnimId);
    drawWaves();
    syncPortfolioData();
}

async function syncPortfolioData() {
    try {
        const resp = await fetch(`${API_8002}/api/portfolio`);
        const data = await resp.json();
        
        const balanceUsd = document.getElementById('trade-cash-balance');
        const netWorth = document.getElementById('trade-net-worth');
        const autoTradeToggle = document.getElementById('auto-trade-toggle');
        
        if (balanceUsd) balanceUsd.innerText = `$${data.balance_usd.toLocaleString([], {minimumFractionDigits: 2})}`;
        if (netWorth) netWorth.innerText = `$${data.net_worth.toLocaleString([], {minimumFractionDigits: 2})}`;
        if (autoTradeToggle) autoTradeToggle.checked = data.autonomous_mode;
        
        const tbody = document.getElementById('portfolio-table-body');
        if (tbody) {
            tbody.innerHTML = '';
            Object.entries(data.assets).forEach(([asset, info]) => {
                tbody.innerHTML += `
                    <tr>
                        <td class="fw-bold text-info">${asset}</td>
                        <td class="font-monospace">${info.qty.toFixed(4)}</td>
                        <td class="text-success font-monospace">$${info.price.toLocaleString([], {minimumFractionDigits:2})}</td>
                        <td class="text-warning fw-bold font-monospace">$${info.value_usd.toLocaleString([], {minimumFractionDigits:2})}</td>
                    </tr>
                `;
            });
        }
    } catch (e) {
        console.error('Failed to sync portfolio:', e);
    }
}

async function executeCoinbaseTrade(action) {
    const assetEl = document.getElementById('trade-asset-select');
    const amountEl = document.getElementById('trade-amount-usd');
    
    const asset = assetEl.value;
    const amountUsd = parseFloat(amountEl.value);
    
    try {
        const resp = await fetch(`${API_8002}/api/coinbase/trade`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action, asset, amount_usd: amountUsd })
        });
        const res = await resp.json();
        if (res.status === 'success') {
            alert(`Trade executed successfully! Bought $${amountUsd} of ${asset}.`);
            syncPortfolioData();
        }
    } catch (e) {
        alert('Trade failed: insufficient funds or port :8002 api down.');
    }
}

async function toggleAutoTrading(enabled) {
    try {
        await fetch(`${API_8002}/api/coinbase/toggle_auto`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ autonomous_mode: enabled })
        });
        syncPortfolioData();
    } catch (e) {
        console.error(e);
    }
}
