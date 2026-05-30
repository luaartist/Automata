/**
 * Sovereign Navigation Component
 * Fully modular runtime navigation header injection for future expansion.
 */
class SovereignNav extends HTMLElement {
    constructor() {
        super();
    }

    connectedCallback() {
        // Ensure stylesheet is loaded
        if (!document.querySelector('link[href*="sovereign-nav.css"]')) {
            const link = document.createElement('link');
            link.rel = 'stylesheet';
            link.href = '/ui/css/sovereign-nav.css';
            document.head.appendChild(link);
        }

        // Render header HTML
        this.innerHTML = `
            <div class="sovereign-nav-header">
                <div class="nav-brand">
                    <i class="fas fa-cubes-stacked brand-icon"></i>
                    <span class="brand-text">SOVEREIGN<span class="brand-subtext">AUTOMATA</span></span>
                </div>
                <div class="nav-links">
                    <a href="/ui/lean4a_forum.html" class="nav-link-btn" id="nav-btn-forum">
                        <i class="fas fa-route"></i> Unified Console
                    </a>
                    <a href="/ui/coherence_runner.html" class="nav-link-btn" id="nav-btn-coherence">
                        <i class="fas fa-network-wired"></i> Coherence Runner
                    </a>
                    <a href="/ui/paper_trader_simulation.html" class="nav-link-btn" id="nav-btn-trader">
                        <i class="fas fa-brain"></i> Sensory AI Aligner
                    </a>
                    <a href="/ui/consciousness_runner.html" class="nav-link-btn" id="nav-btn-consciousness">
                        <i class="fas fa-wave-square"></i> Automation Hub
                    </a>
                    <a href="/ui/tph_chattts_voice_hub.html" class="nav-link-btn" id="nav-btn-voice">
                        <i class="fas fa-microphone-lines"></i> ChatTTS Voice Hub
                    </a>
                </div>
                <div class="nav-telemetry">
                    <div class="telemetry-badge">
                        <span class="status-indicator-dot online"></span>
                        <span>MI300X ACTIVE</span>
                    </div>
                </div>
            </div>
        `;

        // Highlight active page
        const path = window.location.pathname;
        this.querySelectorAll(".nav-link-btn").forEach(btn => btn.classList.remove("active"));
        
        if (path.includes("lean4a_forum")) {
            const el = this.querySelector("#nav-btn-forum");
            if (el) el.classList.add("active");
        } else if (path.includes("coherence_runner")) {
            const el = this.querySelector("#nav-btn-coherence");
            if (el) el.classList.add("active");
        } else if (path.includes("paper_trader_simulation")) {
            const el = this.querySelector("#nav-btn-trader");
            if (el) el.classList.add("active");
        } else if (path.includes("consciousness_runner")) {
            const el = this.querySelector("#nav-btn-consciousness");
            if (el) el.classList.add("active");
        } else if (path.includes("tph_chattts_voice_hub")) {
            const el = this.querySelector("#nav-btn-voice");
            if (el) el.classList.add("active");
        }
    }
}

// Define the custom web component
if (!customElements.get('sovereign-nav')) {
    customElements.define('sovereign-nav', SovereignNav);
}
