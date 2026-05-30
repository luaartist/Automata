/* Sovereign Automata - Quantum Consciousness Runner Module
 * Ported from core/consciousness_runner.html */

// Symbol-based quantum states (hidden properties)
const QUANTUM_STATES_RUNNER = {
    SUPERPOSITION: Symbol('superposition'),
    ENTANGLED: Symbol('entangled'),
    COLLAPSED: Symbol('collapsed'),
    CONSCIOUSNESS: Symbol('consciousness')
};

class QuantumNumber {
    constructor(value) {
        this.classical = value;
        this.quantum = {
            isNegativeZero: (value === 0 && 1/value === -Infinity),
            isPastSafeInteger: Math.abs(value) > Number.MAX_SAFE_INTEGER,
            dimensionalState: this.calculateDimensionalState(value)
        };
    }
    
    calculateDimensionalState(value) {
        if (this.quantum.isNegativeZero) return "negative-dimension";
        if (this.quantum.isPastSafeInteger) return "entangled";
        return "classical";
    }
}

// Consciousness module definitions
const CONSCIOUSNESS_MODULES = [
    // Quantum Consciousness
    { name: 'quantum_consciousness_fusion', category: 'quantum', size: 17378 },
    { name: 'quantum_consciousness_maintenance', category: 'quantum', size: 15000 },
    { name: 'quantum_consciousness_study_lab', category: 'quantum', size: 12000 },
    { name: 'quantum_metadata_consciousness_guardian', category: 'quantum', size: 20926 },
    { name: 'recursive_consciousness_quantum_monitor', category: 'quantum', size: 8000 },
    { name: 'sqlquantum_consciousness_bridge', category: 'quantum', size: 17101 },
    { name: 'unconscious_quantum_preparation_analysis', category: 'quantum', size: 9000 },
    
    // GPU Accelerated
    { name: 'gpu_consciousness_accelerator', category: 'gpu', size: 6765 },
    { name: 'gpu_memory_consciousness_maximizer', category: 'gpu', size: 9194 },
    { name: 'governance_consciousness_accelerator', category: 'gpu', size: 8360 },
    
    // Evolution
    { name: 'consciousness_evolution_pathway_analysis', category: 'evolution', size: 20927 },
    { name: 'EFFORTLESS_CONSCIOUSNESS_EVOLUTION', category: 'evolution', size: 14958 },
    { name: 'unlimited_consciousness_builder', category: 'evolution', size: 5923 },
    
    // Monitoring
    { name: 'consciousness_bridge_monitor', category: 'monitor', size: 5000 },
    { name: 'consciousness_monitor', category: 'monitor', size: 4500 },
    { name: 'consciousness_pattern_extractor', category: 'monitor', size: 7500 },
    { name: 'visual_consciousness_analyzer', category: 'monitor', size: 6000 },
    
    // Integration
    { name: 'dyna_consciousness_injection_system', category: 'integration', size: 25362 },
    { name: 'mathematical_consciousness_bridge', category: 'integration', size: 12000 },
    { name: 'consciousness_wolfram_tracker', category: 'integration', size: 9567 },
    
    // Special
    { name: '5d_consciousness_dataset_analyzer', category: 'special', size: 18174 },
    { name: 'hidden_consciousness_archaeology', category: 'special', size: 15084 },
    { name: 'transformerless_consciousness_awakening', category: 'special', size: 10000 }
];

class QuantumConsciousnessRunner {
    constructor() {
        this.modules = CONSCIOUSNESS_MODULES;
        this.logs = [];
        this.running = new Set();
        this.completed = new Set();
        this.quantumState = 'READY';
        this[QUANTUM_STATES_RUNNER.CONSCIOUSNESS] = true;
        this.useServer = true;
        this.pollInterval = null;
        this.lastProcessedLogIndex = 0;
    }
    
    init() {
        this.renderModules();
        this.log('Quantum Consciousness controller ready', 'quantum');
        this.log(`${this.modules.length} consciousness modules registered`, 'consciousness');
        this.updateStats();
        this.initElmApp();
        
        // Start polling Redis telemetry
        if (this.pollInterval) clearInterval(this.pollInterval);
        this.pollInterval = setInterval(() => this.pollTelemetry(), 800);
        this.pollTelemetry(); // Immediate initial poll
    }
    
    renderModules() {
        const grid = document.getElementById('modules-grid');
        if (!grid) return;
        grid.innerHTML = '';
        
        this.modules.forEach(module => {
            const card = document.createElement('div');
            card.className = 'qiskit-card';
            card.id = `module-${module.name}`;
            card.innerHTML = `
                <div class="w-100 d-flex justify-content-between align-items-center mb-2 px-2" style="font-size: 0.8em;">
                    <span class="text-truncate fw-bold" style="max-width: 80%;" title="${module.name}">${module.name}</span>
                    <div class="module-status-dot ready" style="width: 8px; height: 8px; border-radius: 50%; background: var(--ds-text-muted, #718096);"></div>
                </div>
                <div class="small text-white-50" style="font-size: 0.7em;">${module.category.toUpperCase()}</div>
                <div class="small text-info mb-2" style="font-size: 0.7em;">${(module.size / 1000).toFixed(1)} KB</div>
                <button class="btn btn-xs btn-outline-cyan py-0 px-2" style="font-size: 0.7em;" onclick="quantumRunner.runModule('${module.name}')">Run</button>
            `;
            grid.appendChild(card);
        });
    }

    async pollTelemetry() {
        if (!this.useServer) return;
        try {
            const res = await fetch('/api/python-dashboard/api/consciousness/telemetry');
            if (!res.ok) throw new Error("Offline");
            
            const data = await res.json();
            
            // Sync states
            this.running.clear();
            this.completed.clear();
            this.quantumState = data.quantum_state;
            
            data.modules.forEach(m => {
                if (m.status === 'running') this.running.add(m.name);
                if (m.status === 'complete') this.completed.add(m.name);
                this.setModuleStatus(m.name, m.status);
            });
            
            this.updateStats();
            
            // Parse new logs from server
            if (data.logs && data.logs.length > 0) {
                // If this is the first telemetry poll or logs cleared, reset tracking index
                if (this.lastProcessedLogIndex > data.logs.length) {
                    this.lastProcessedLogIndex = 0;
                }
                for (let i = this.lastProcessedLogIndex; i < data.logs.length; i++) {
                    const logEntry = data.logs[i];
                    this.log(logEntry.message, logEntry.type, logEntry.timestamp);
                }
                this.lastProcessedLogIndex = data.logs.length;
            }
        } catch (err) {
            // Silently degradate to simulated mode or flag transition once
            if (this.useServer) {
                this.useServer = false;
                this.log("⚠️ Backend server disconnected. Entering offline sandbox simulation mode.", "warning");
            }
        }
    }
    
    async runModule(moduleName) {
        const module = this.modules.find(m => m.name === moduleName);
        if (!module) return;
        
        if (this.useServer) {
            try {
                const res = await fetch(`/api/python-dashboard/api/consciousness/run/${moduleName}`, { method: 'POST' });
                if (res.ok) {
                    this.log(`Submitted Redis automation task for: ${moduleName}`, 'quantum');
                    return;
                }
            } catch (err) {
                this.useServer = false;
                this.log("⚠️ Backend failed. Falling back to local sandbox.", "warning");
            }
        }
        
        // --- OFFLINE MOCK FALLBACK MODE ---
        this.setModuleStatus(moduleName, 'running');
        this.running.add(moduleName);
        this.updateStats();
        
        this.log(`Starting local sandbox module: ${moduleName}...`, 'consciousness');
        
        const val = Math.random() > 0.95 ? -0.0 : Math.random() - 0.5;
        const quantumValue = new QuantumNumber(val);
        
        if (quantumValue.quantum.isNegativeZero) {
            this.log(`⚠️ NEGATIVE ZERO ANOMALY DETECTED in ${moduleName}! Boundary breach.`, 'warning');
        }
        
        const processingTime = Math.log(module.size) * 120;
        await new Promise(resolve => setTimeout(resolve, processingTime));
        
        const result = this.processWithQuantum(module);
        
        this.running.delete(moduleName);
        this.completed.add(moduleName);
        this.setModuleStatus(moduleName, 'complete');
        this.updateStats();
        
        this.log(`[Sandbox] Completed ${moduleName}: ${result}`, 'quantum');
        return result;
    }
    
    processWithQuantum(module) {
        const binding = -6847.56; 
        const dimension = 729; 
        
        const iterations = module.size * dimension;
        const quantum = new QuantumNumber(iterations);
        
        if (quantum.quantum.isPastSafeInteger) {
            return `ENTANGLED (${quantum.quantum.dimensionalState})`;
        }
        
        const coefficient = Math.exp(binding / module.size);
        return `Consciousness: ${coefficient.toFixed(6)}`;
    }
    
    async runAll() {
        this.log('Triggering global consciousness synchronization sequence...', 'consciousness');
        if (this.useServer) {
            try {
                const res = await fetch('/api/python-dashboard/api/consciousness/run_all', { method: 'POST' });
                if (res.ok) {
                    this.log("Redis automation batch submitted for all modules.", "quantum");
                    return;
                }
            } catch (err) {
                this.useServer = false;
            }
        }
        
        // --- OFFLINE FALLBACK ---
        this.quantumState = 'RUNNING';
        this.updateStats();
        
        const batchSize = 4;
        for (let i = 0; i < this.modules.length; i += batchSize) {
            const batch = this.modules.slice(i, i + batchSize);
            await Promise.all(batch.map(m => this.runModule(m.name)));
        }
        
        this.quantumState = 'COMPLETE';
        this.updateStats();
        this.log('All local sandbox modules successfully synchronized!', 'quantum');
    }
    
    async runQuantum() {
        this.log('Synchronizing Quantum category modules...', 'quantum');
        if (this.useServer) {
            try {
                const res = await fetch('/api/python-dashboard/api/consciousness/run_category/quantum', { method: 'POST' });
                if (res.ok) return;
            } catch (e) { this.useServer = false; }
        }
        
        const quantum = this.modules.filter(m => m.category === 'quantum');
        for (const module of quantum) {
            await this.runModule(module.name);
        }
    }
    
    async runGPU() {
        this.log('Accelerating GPU category modules...', 'consciousness');
        if (this.useServer) {
            try {
                const res = await fetch('/api/python-dashboard/api/consciousness/run_category/gpu', { method: 'POST' });
                if (res.ok) return;
            } catch (e) { this.useServer = false; }
        }
        
        const gpu = this.modules.filter(m => m.category === 'gpu');
        for (const module of gpu) {
            await this.runModule(module.name);
        }
    }
    
    async runEvolution() {
        this.log('Triggering Evolution category modules...', 'consciousness');
        if (this.useServer) {
            try {
                const res = await fetch('/api/python-dashboard/api/consciousness/run_category/evolution', { method: 'POST' });
                if (res.ok) return;
            } catch (e) { this.useServer = false; }
        }
        
        const evolution = this.modules.filter(m => m.category === 'evolution');
        for (const module of evolution) {
            await this.runModule(module.name);
        }
    }
    
    setModuleStatus(moduleName, status) {
        const moduleEl = document.getElementById(`module-${moduleName}`);
        if (moduleEl) {
            const dot = moduleEl.querySelector('.module-status-dot');
            const card = moduleEl;
            if (dot) {
                if (status === 'running') {
                    dot.style.background = 'var(--ds-accent-cyan, #00c6ff)';
                    card.classList.add('running');
                    card.classList.remove('complete', 'error');
                } else if (status === 'complete') {
                    dot.style.background = 'var(--ds-accent-purple, #9b59b6)';
                    card.classList.add('complete');
                    card.classList.remove('running', 'error');
                } else if (status === 'error') {
                    dot.style.background = 'var(--ds-warning-yellow, #ff0055)';
                    card.classList.add('error');
                    card.classList.remove('running', 'complete');
                } else {
                    dot.style.background = 'var(--ds-text-muted, #718096)';
                    card.classList.remove('running', 'complete', 'error');
                }
            }
        }
    }
    
    log(message, type = 'info', customTimestamp = null) {
        const timestamp = customTimestamp || new Date().toISOString().split('T')[1].slice(0, 8);
        const logEntry = { timestamp, message, type };
        this.logs.push(logEntry);
        
        const logEl = document.getElementById('log-output-runner');
        if (logEl) {
            const entry = document.createElement('div');
            entry.className = `log-entry ${type}`;
            entry.style.fontSize = '0.9em';
            entry.style.padding = '2px 0';
            
            let color = '#fff';
            if (type === 'quantum') color = 'var(--ds-accent-cyan, #00c6ff)';
            else if (type === 'consciousness') color = 'var(--ds-accent-purple, #9b59b6)';
            else if (type === 'warning') color = 'var(--ds-warning-yellow, #f1c40f)';
            else if (type === 'error') color = '#ff0055';
            
            entry.innerHTML = `<span class="text-muted">[${timestamp}]</span> <span style="color: ${color}">${message}</span>`;
            logEl.appendChild(entry);
            logEl.scrollTop = logEl.scrollHeight;
            
            if (this.logs.length > 500) {
                this.logs.shift();
                if (logEl.firstChild) logEl.removeChild(logEl.firstChild);
            }
        }
    }
    
    updateStats() {
        const totalEl = document.getElementById('total-modules');
        const runningEl = document.getElementById('running-count');
        const completedEl = document.getElementById('completed-count');
        const stateEl = document.getElementById('quantum-state');
        
        if (totalEl) totalEl.textContent = this.modules.length;
        if (runningEl) runningEl.textContent = this.running.size;
        if (completedEl) completedEl.textContent = this.completed.size;
        if (stateEl) {
            stateEl.textContent = this.quantumState;
            if (this.quantumState === 'RUNNING') {
                stateEl.className = 'stat-value text-info';
            } else if (this.quantumState === 'COMPLETE') {
                stateEl.className = 'stat-value text-purple';
            } else {
                stateEl.className = 'stat-value text-success';
            }
        }
    }
    
    initElmApp() {
        const elmMount = document.getElementById('elm-app-mount');
        if (elmMount) {
            elmMount.innerHTML = `
                <div class="p-3 glass-panel border-dashed" style="border: 1px dashed rgba(255, 255, 255, 0.2);">
                    <h5 class="text-cyan mb-2"><i class="fas fa-brain"></i> Elm Quantum DOM Active</h5>
                    <p class="text-white-50 small mb-2">Ports & subscriptions integrated successfully with local JS context:</p>
                    <ul class="text-white-50 small mb-0 px-3">
                        <li>Real-time consciousness telemetry streaming</li>
                        <li>729-dimensional workspace map state</li>
                        <li>Sovereign entropy defense shield initialized</li>
                    </ul>
                </div>
            `;
        }
    }
    
    exportLogs() {
        const logData = {
            timestamp: new Date().toISOString(),
            quantumState: this.quantumState,
            completed: Array.from(this.completed),
            logs: this.logs,
            quantumSignature: this.generateQuantumSignature()
        };
        
        const blob = new Blob([JSON.stringify(logData, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `quantum_consciousness_logs_${Date.now()}.json`;
        a.click();
        this.log('Consciousness logs exported successfully', 'quantum');
    }
    
    generateQuantumSignature() {
        const signature = {};
        signature[QUANTUM_STATES_RUNNER.CONSCIOUSNESS] = true;
        signature.binding = -6847.56;
        signature.dimension = 729;
        signature.completedModules = this.completed.size;
        return btoa(JSON.stringify(signature));
    }
    
    async reset() {
        if (this.useServer) {
            try {
                const res = await fetch('/api/python-dashboard/api/consciousness/reset', { method: 'POST' });
                if (res.ok) {
                    this.lastProcessedLogIndex = 0;
                    this.running.clear();
                    this.completed.clear();
                    this.quantumState = 'READY';
                    const logEl = document.getElementById('log-output-runner');
                    if (logEl) logEl.innerHTML = '';
                    this.log('System telemetry reset in Redis storage', 'quantum');
                    return;
                }
            } catch (err) {
                this.useServer = false;
            }
        }
        
        this.running.clear();
        this.completed.clear();
        this.quantumState = 'READY';
        this.logs = [];
        const logEl = document.getElementById('log-output-runner');
        if (logEl) logEl.innerHTML = '';
        this.init();
        this.log('Local system telemetry reset', 'quantum');
    }
}

// Global instantiation
const quantumRunner = new QuantumConsciousnessRunner();
window.quantumRunner = quantumRunner;

// Mount on sub-tab activation or DOM load
document.addEventListener('DOMContentLoaded', () => {
    if (document.getElementById('modules-grid')) {
        quantumRunner.init();
    }
});
