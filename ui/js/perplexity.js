/* Perplexity Templates & Cognee Memory Graph Interface */

const perplexityTemplates = {
    quantum: `Perplexity Deep Research Request: Write a comprehensive, mathematically rigorous research brief analyzing the 27-dimensional SO(27) and SU(27) Lie algebra groups. Explain how the topological phase projection vector maps to a 133-qubit heavy-hex lattice topology (like IBM Heron V2). Compute the expectation value shifts under dephasing noise (T1=100µs, T2=80µs) and verify if the Yang-Mills gauge symmetry constraints remain unbroken under dynamic flux fluctuations.`,
    
    speechmatics: `Perplexity Deep Research Request: Analyze the speechmatics real-time transcription and translation middleware for edge-deployable AI agents. We are developing an A-JEPA (Joint Embedding Predictive Architecture) world-model system that transcribes ancient financial and market trade structures recorded in Cuneiform and Pre-Cuneiform dialects. Propose a benchmark framework for training semantic listening layers on unknown, non-standard audio phonemes using AMD MI300X ROCm GPU pipelines.`,
    
    cygwin: `Perplexity Deep Research Request: Outline the architectural requirements to build a fully reproducible Cygwin compilation pipeline inside a WINE compatibility container on Ubuntu 24.04. Detail how to map MSBuild, WINEPREFIX configuration directories, and custom registry shims (cygwin1.dll) to allow building large C++ codebases seamlessly without natively installing Windows. Highlight hash registry verification strategies and how to detect dangling compiler orphans.`
};

function loadPerplexityTemplate() {
    const selector = document.getElementById('perplexity-template-select');
    const textarea = document.getElementById('perplexity-prompt-textarea');
    if (selector && textarea) {
        textarea.value = perplexityTemplates[selector.value] || '';
    }
}

function copyPerplexityPrompt() {
    const txt = document.getElementById('perplexity-prompt-textarea');
    if (txt) {
        txt.select();
        navigator.clipboard.writeText(txt.value);
    }
}

async function exportPerplexityQueryFile() {
    const promptEl = document.getElementById('perplexity-prompt-textarea');
    const selectEl = document.getElementById('perplexity-template-select');
    
    const promptContent = promptEl.value;
    const queryName = selectEl.value;
    
    // Save to knowledge store using Cognee remember route
    try {
        await fetch(`${API_8002}/api/cognee/remember`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ text: `Perplexity query exported for template: ${queryName}. Prompt text: ${promptContent.slice(0, 100)}...` })
        });
    } catch (e) {
        showPerplexityStatus("Memory write failed. Clipboard remains active.");
    }
}

function showPerplexityStatus(msg) {
    const status = document.getElementById('perplexity-status-msg');
    if (status) {
        status.innerText = msg;
        setTimeout(() => { status.innerText = ''; }, 3000);
    }
}

async function saveCogneeFact() {
    const factEl = document.getElementById('cognee-remember-text');
    const fact = factEl.value.trim();
    
    try {
        const resp = await fetch(`${API_8002}/api/cognee/remember`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ text: fact })
        });
        const data = await resp.json();
        if (data.status === 'success') {
            factEl.value = '';
        }
    } catch (e) {
        alert("Cognee remember failed. Port :8002 api down.");
    }
}

async function queryCogneeGraph() {
    const queryEl = document.getElementById('cognee-recall-query');
    const query = queryEl.value.trim();
    
    const timeline = document.getElementById('cognee-timeline-container');
    if (timeline) {
        timeline.innerHTML = '<div class="text-info"><i class="fas fa-spinner fa-spin"></i> Recalling matching nodes...</div>';
    }
    
    try {
        const resp = await fetch(`${API_8002}/api/cognee/recall`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ query: query })
        });
        const data = await resp.json();
        
        if (timeline) {
            if (data.status === 'success' && data.results.length > 0) {
                timeline.innerHTML = '';
                data.results.forEach((r, idx) => {
                    timeline.innerHTML += `
                        <div class="glass-panel p-2 mb-2" style="font-size: 0.9em; background: rgba(0,0,0,0.25);">
                            <div class="d-flex justify-content-between mb-1">
                                <span class="text-info">Memory Node #${idx+1} (${r.kind})</span>
                                <span class="text-success font-monospace" style="font-size:0.8em;">Score: ${r.score || '1.0'}</span>
                            </div>
                            <div class="text-light">${escapeHtml(r.text)}</div>
                        </div>
                    `;
                });
            } else {
                timeline.innerHTML = '<div class="text-muted text-center py-2">No matching graph nodes found.</div>';
            }
        }
    } catch (e) {
        if (timeline) timeline.innerHTML = '<div class="text-danger text-center py-2">Cognee recall failed. Port :8002 API down.</div>';
    }
}
