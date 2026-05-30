/* Qwen Debug Assistant & SSE LLM Stream Engine
 * Aligned with proven implementation from lean4a_forum_old.html */

// Fallback for air-gapped environments if marked.js fails to load from CDN
if (typeof marked === 'undefined') {
    window.marked = {
        parse: function(text) {
            if (!text) return '';
            let html = text
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;');
            html = html.replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>');
            html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
            html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
            html = html.replace(/^\s*[-*]\s+(.+)$/gm, '<li>$1</li>');
            html = html.replace(/(<li>.*<\/li>)/s, '<ul>$1</ul>');
            html = html.replace(/\n/g, '<br>');
            return html;
        }
    };
}

let chatMessages = [];
let currentStreamReader = null;
let contextRefreshInterval = null;

function initLLMChat() {
    // Event listeners
    document.getElementById('send-btn').addEventListener('click', sendMessage);
    document.getElementById('stop-btn').addEventListener('click', stopGeneration);
    document.getElementById('clear-chat-btn').addEventListener('click', clearChat);
    document.getElementById('context-btn').addEventListener('click', toggleContextPanel);

    // Keyboard shortcuts
    document.getElementById('chat-input').addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        } else if (e.key === 'Escape') {
            stopGeneration();
        }
    });
}

async function loadModels() {
    const selector = document.getElementById('model-selector');
    if (!selector) return;
    try {
        const resp = await fetch(`${API_BASE}/api/llm/models`);
        const data = await resp.json();

        if (data.models && data.models.length > 0) {
            selector.innerHTML = data.models.map(model =>
                `<option value="${escapeHtml(model)}" ${model === data.default ? 'selected' : ''}>
                    ${escapeHtml(model)}
                </option>`
            ).join('');
        } else {
            // Fallback to default models
            selector.innerHTML = `
                <option value="qwen2.5-coder:32b" selected>qwen2.5-coder:32b</option>
                <option value="gemma31b-local:latest">gemma31b-local:latest</option>
                <option value="deepseek-r1:70b">deepseek-r1:70b</option>
                <option value="mistral:latest">mistral:latest</option>
            `;
        }
    } catch (e) {
        console.error('Failed to load models:', e);
        selector.innerHTML = '<option value="qwen2.5-coder:32b">qwen2.5-coder:32b (fallback)</option>';
    }
}

async function sendMessage() {
    const input = document.getElementById('chat-input');
    const message = input.value.trim();
    if (!message) return;

    // Add user message
    addChatMessage('user', message);
    chatMessages.push({ role: 'user', content: message });
    input.value = '';

    // Show typing indicator
    const typingIndicator = addTypingIndicator();

    // Disable send button, show stop button
    document.getElementById('send-btn').style.display = 'none';
    document.getElementById('stop-btn').style.display = 'block';

    try {
        const model = document.getElementById('model-selector').value;
        const injectContext = document.getElementById('inject-context-checkbox').checked;

        const resp = await fetch(`${API_BASE}/api/llm/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
            body: JSON.stringify({
                model: model,
                messages: chatMessages,
                inject_context: injectContext
            })
        });

        if (!resp.ok) {
            throw new Error(`HTTP ${resp.status}: ${resp.statusText}`);
        }

        // Remove typing indicator
        typingIndicator.remove();

        // Start streaming response
        const reader = resp.body.getReader();
        currentStreamReader = reader;
        const decoder = new TextDecoder();
        let buffer = '';
        let assistantMessage = '';
        let assistantBubble = null;

        while (true) {
            const { value, done } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });

            // Parse SSE frames
            const frames = buffer.split('\n\n');
            buffer = frames.pop();

            for (const frame of frames) {
                let eventType = 'message', dataStr = '';
                for (const line of frame.split('\n')) {
                    if (line.startsWith('event: ')) eventType = line.slice(7).trim();
                    if (line.startsWith('data: ')) dataStr = line.slice(6);
                }
                if (!dataStr) continue;

                try {
                    const data = JSON.parse(dataStr);

                    if (eventType === 'token') {
                        assistantMessage += data.content || '';
                        if (!assistantBubble) {
                            assistantBubble = addChatMessage('assistant', assistantMessage);
                        } else {
                            updateChatMessage(assistantBubble, assistantMessage);
                        }
                        scrollChatToBottom();
                    } else if (eventType === 'done') {
                        chatMessages.push({ role: 'assistant', content: assistantMessage });

                        // Check for flow triggers
                        checkForFlowTriggers(assistantBubble, assistantMessage);
                        break;
                    } else if (eventType === 'error') {
                        addChatMessage('error', `Error: ${data.message || 'Unknown error'}`);
                        break;
                    }
                } catch (e) {
                    console.error('Failed to parse SSE data:', e);
                }
            }
        }
    } catch (e) {
        typingIndicator.remove();
        addChatMessage('error', `Failed to send message: ${escapeHtml(e.message)}`);
    } finally {
        currentStreamReader = null;
        document.getElementById('send-btn').style.display = 'block';
        document.getElementById('stop-btn').style.display = 'none';
    }
}

function stopGeneration() {
    if (currentStreamReader) {
        currentStreamReader.cancel();
        currentStreamReader = null;
        addChatMessage('error', 'Generation stopped by user');
        document.getElementById('send-btn').style.display = 'block';
        document.getElementById('stop-btn').style.display = 'none';
    }
}

function addChatMessage(role, content) {
    const container = document.getElementById('chat-messages');

    // Remove empty state
    const emptyState = container.querySelector('.empty-state');
    if (emptyState) emptyState.remove();

    const messageDiv = document.createElement('div');
    messageDiv.className = `chat-message ${role}`;

    const bubble = document.createElement('div');
    bubble.className = 'message-bubble';

    if (role === 'user' || role === 'error') {
        bubble.textContent = content;
    } else if (role === 'assistant') {
        // Render markdown using marked.js
        bubble.innerHTML = marked.parse(content);
    }

    messageDiv.appendChild(bubble);
    container.appendChild(messageDiv);
    scrollChatToBottom();

    return bubble;
}

function updateChatMessage(bubble, content) {
    bubble.innerHTML = marked.parse(content);
}

function addTypingIndicator() {
    const container = document.getElementById('chat-messages');
    const messageDiv = document.createElement('div');
    messageDiv.className = 'chat-message assistant';
    messageDiv.innerHTML = `
        <div class="message-bubble">
            <div class="typing-indicator">
                <div class="typing-dot"></div>
                <div class="typing-dot"></div>
                <div class="typing-dot"></div>
            </div>
        </div>
    `;
    container.appendChild(messageDiv);
    scrollChatToBottom();
    return messageDiv;
}

function scrollChatToBottom() {
    const container = document.getElementById('chat-messages');
    container.scrollTop = container.scrollHeight;
}

function clearChat() {
    chatMessages = [];
    const container = document.getElementById('chat-messages');
    container.innerHTML = `
        <div class="empty-state">
            <i class="fas fa-comments"></i>
            <p>Start a conversation with the debug agent...</p>
        </div>
    `;
}

function checkForFlowTriggers(bubble, content) {
    if (!bubble) return;
    // Parse content for "FLOW: flow_name" pattern
    const flowMatch = content.match(/FLOW:\s*(\w+)/);
    if (flowMatch) {
        const flowName = flowMatch[1];
        const button = document.createElement('button');
        button.className = 'flow-trigger-btn';
        button.innerHTML = `<i class="fas fa-play"></i> Run ${escapeHtml(flowName)}`;
        button.addEventListener('click', () => {
            // Switch to flows tab and run the flow
            document.getElementById('flows-tab').click();
            setTimeout(() => runFlow(flowName), 100);
        });
        bubble.parentElement.appendChild(button);
    }
}

async function toggleContextPanel() {
    const panel = document.getElementById('context-panel-container');
    const btn = document.getElementById('context-btn');

    if (panel.style.display === 'none' || panel.style.display === '') {
        panel.style.display = 'block';
        btn.innerHTML = '<i class="fas fa-cog fa-spin"></i> Loading...';
        await loadContext();
        btn.innerHTML = '<i class="fas fa-cog"></i> Context';
        startContextRefresh();
    } else {
        panel.style.display = 'none';
        stopContextRefresh();
    }
}

async function loadContext() {
    // Support both old (context-content) and new (active-context-display) element IDs
    const content = document.getElementById('context-content') || document.getElementById('active-context-display');
    if (!content) return;
    try {
        const resp = await fetch(`${API_BASE}/api/llm/context`);
        const data = await resp.json();
        const formatted = JSON.stringify(data, null, 2);
        if (typeof syntaxHighlightJson === 'function') {
            content.innerHTML = `<pre>${syntaxHighlightJson(formatted)}</pre>`;
        } else {
            content.textContent = formatted;
        }
    } catch (e) {
        content.textContent = `Error loading context: ${e.message}`;
    }
}

function startContextRefresh() {
    if (contextRefreshInterval) return;
    contextRefreshInterval = setInterval(() => {
        if (document.getElementById('context-panel-container').style.display !== 'none') {
            loadContext();
        }
    }, 30000);
}

function stopContextRefresh() {
    if (contextRefreshInterval) {
        clearInterval(contextRefreshInterval);
        contextRefreshInterval = null;
    }
}

// JSON syntax highlighting (matches lean4a_forum_old.html)
function syntaxHighlightJson(json) {
    json = json.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return json.replace(/("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g, function (match) {
        let cls = 'json-number';
        if (/^"/.test(match)) {
            if (/:$/.test(match)) {
                cls = 'json-key';
            } else {
                cls = 'json-string';
            }
        } else if (/true|false/.test(match)) {
            cls = 'json-boolean';
        } else if (/null/.test(match)) {
            cls = 'json-null';
        }
        return '<span class="' + cls + '">' + match + '</span>';
    });
}
