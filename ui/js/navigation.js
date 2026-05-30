/* Modular Application Navigation & Global Configuration */

const API_BASE = window.location.origin === "null" || window.location.origin === "file://" || !window.location.origin 
    ? "http://localhost:8443" 
    : window.location.origin;

// Custom local endpoint for Trading/Quantum/Cognee on port 8002 proxied via port 8443 Go Orchestrator
const API_8002 = window.location.origin === "null" || window.location.origin === "file://" || !window.location.origin 
    ? "http://localhost:8443/api/python-dashboard" 
    : (window.location.origin + "/api/python-dashboard");

// Navigation Switches
function switchMainTab(targetPaneId, sidebarBtnId) {
    document.querySelectorAll(".tab-pane").forEach(pane => {
        pane.classList.remove("show", "active");
    });
    const targetPane = document.querySelector(targetPaneId);
    if (targetPane) {
        targetPane.classList.add("show", "active");
    }
    document.querySelectorAll(".nav-tab-btn").forEach(btn => {
        btn.classList.remove("active");
    });
    if (sidebarBtnId) {
        const sidebarBtn = document.getElementById(sidebarBtnId);
        if (sidebarBtn) sidebarBtn.classList.add("active");
    }

    // Special initialization on tab transitions if needed
    if (targetPaneId === "#trading-panel" && typeof initWaveformVisualizer === "function") {
        initWaveformVisualizer();
    } else if (targetPaneId === "#quantum-sdk-panel") {
        if (typeof initBlochSphere === "function") initBlochSphere();
        if (typeof syncQuantumJobs === "function") syncQuantumJobs();
    } else if (targetPaneId === "#quantum-runner-panel" && window.quantumRunner) {
        window.quantumRunner.init();
    }
}

function switchSubTab(targetSectionId, subBtn) {
    document.querySelectorAll(".flow-sub-section").forEach(sec => {
        sec.classList.add("d-none");
    });
    const targetSec = document.querySelector(targetSectionId);
    if (targetSec) {
        targetSec.classList.remove("d-none");
    }
    document.querySelectorAll(".nav-sub-item-btn").forEach(btn => {
        btn.classList.remove("active");
    });
    if (subBtn) {
        subBtn.classList.add("active");
    }
}

// Global System Bootstrapper
document.addEventListener("DOMContentLoaded", () => {
    if (typeof checkHealth === "function") {
        checkHealth();
        setInterval(checkHealth, 15000);
    }
    if (typeof loadFlows === "function") loadFlows();
    if (typeof loadTickets === "function") loadTickets();
    if (typeof loadPerplexityTemplate === "function") loadPerplexityTemplate();
    if (typeof initLLMChat === "function") initLLMChat();
});
