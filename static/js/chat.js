/* ============================================================
   ARGUS — Guardian Chat
   ============================================================ */

let chatSessions     = JSON.parse(localStorage.getItem('argusChatSessions') || '[]');
let currentSessionId = null;

function saveSessions() {
    localStorage.setItem('argusChatSessions', JSON.stringify(chatSessions));
}

function newChatSession() {
    const id      = 'session_' + Date.now();
    const session = { id, title: 'New Conversation', createdAt: new Date().toISOString(), messages: [] };
    chatSessions.unshift(session);
    saveSessions();
    loadSession(id);
    renderChatHistory();
}

function loadSession(id) {
    currentSessionId = id;
    const session   = chatSessions.find(s => s.id === id);
    const container = document.getElementById('chatMessages');

    const greeting = `<div class="chat-message ai">
        <div class="chat-avatar ai">A</div>
        <div class="chat-bubble">
            Hello. I'm the ARGUS Guardian. I have access to your pipeline results, risk data, and market signals.<br><br>
            Ask me anything — current risk exposure, country analysis, pipeline results, or market conditions.
        </div>
    </div>`;

    if (!session || !session.messages.length) {
        container.innerHTML = greeting;
        return;
    }

    container.innerHTML = greeting + session.messages.map(m => `
        <div class="chat-message ${m.role}">
            <div class="chat-avatar ${m.role}">${m.role === 'ai' ? 'A' : 'U'}</div>
            <div class="chat-bubble">${escapeHtml(m.content)}</div>
        </div>`).join('');

    container.scrollTop = container.scrollHeight;
    renderChatHistory();
}

function renderChatHistory() {
    const list = document.getElementById('chatHistoryList');
    if (!chatSessions.length) {
        list.innerHTML = `<div style="padding:16px;text-align:center;font-size:11px;color:var(--text-muted);">
            No conversations yet.<br>Click + to start.</div>`;
        return;
    }
    list.innerHTML = chatSessions.map(s => `
        <div class="chat-history-item ${s.id === currentSessionId ? 'active' : ''}"
             onclick="loadSession('${s.id}')">
            <div class="chat-history-item-title">${escapeHtml(s.title)}</div>
            <div class="chat-history-item-meta">${s.createdAt.substr(0, 10)}</div>
        </div>`).join('');
}

function handleChatKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendChatMessage();
    }
}

function autoResize(el) {
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 120) + 'px';
}

async function sendChatMessage() {
    const input = document.getElementById('chatInput');
    const text  = input.value.trim();
    if (!text) return;

    if (!currentSessionId) newChatSession();

    input.value = '';
    autoResize(input);

    appendChatBubble('user', text);

    const session = chatSessions.find(s => s.id === currentSessionId);
    if (session) {
        session.messages.push({ role: 'user', content: text });
        if (session.title === 'New Conversation')
            session.title = text.substring(0, 40) + (text.length > 40 ? '…' : '');
        saveSessions();
        renderChatHistory();
    }

    const typingId = 'typing_' + Date.now();
    appendChatBubble('ai', '<span class="typewriter-cursor"></span>', typingId);

    try {
        const r = await fetch(`${API_BASE}/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: text, persona: currentPersona, session_id: currentSessionId }),
        });
        const d     = await r.json();
        const reply = d.reply || d.message || 'No response.';

        document.getElementById(typingId)?.remove();
        appendChatBubble('ai', reply);

        if (session) {
            session.messages.push({ role: 'ai', content: reply });
            saveSessions();
        }
    } catch(e) {
        document.getElementById(typingId)?.remove();
        appendChatBubble('ai', 'Connection error — ensure the backend /api/chat endpoint is running.');
    }
}

function appendChatBubble(role, content, id = '') {
    const container = document.getElementById('chatMessages');
    const div       = document.createElement('div');
    div.className   = `chat-message ${role}`;
    if (id) div.id  = id;
    div.innerHTML = `
        <div class="chat-avatar ${role}">${role === 'ai' ? 'A' : 'U'}</div>
        <div class="chat-bubble">${role === 'user' ? escapeHtml(content) : content}</div>`;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}
