/**
 * Shared chatbot panel — include at bottom of each page's <body>.
 * Renders a floating button + slide-in panel with AI chat.
 */
(function () {
  const STORAGE_KEY = "expense_chat_history";

  // ── Inject styles ───────────────────────────────────────────────────────────
  const style = document.createElement("style");
  style.textContent = `
    /* Chat button */
    #chat-fab {
      position: fixed; bottom: 24px; right: 24px; z-index: 900;
      width: 52px; height: 52px; border-radius: 50%;
      background: #5b6af5; color: #fff; border: none;
      font-size: 1.4rem; cursor: pointer;
      box-shadow: 0 4px 18px rgba(91,106,245,0.45);
      display: flex; align-items: center; justify-content: center;
      transition: transform 0.2s, box-shadow 0.2s, background 0.2s;
    }
    #chat-fab:hover { background: #4a57e0; transform: scale(1.06); }
    #chat-fab.open { background: #1a1a2e; }

    /* Sidebar */
    #chat-panel {
      position: fixed; top: 0; right: 0; bottom: 0; z-index: 850;
      width: 380px; max-width: 100vw;
      background: #fff;
      box-shadow: -4px 0 32px rgba(0,0,0,0.13);
      display: flex; flex-direction: column;
      transform: translateX(100%);
      transition: transform 0.28s cubic-bezier(0.4,0,0.2,1);
    }
    #chat-panel.open { transform: translateX(0); }

    /* Header */
    #chat-header {
      background: #1a1a2e; color: #fff;
      padding: 14px 18px; display: flex; align-items: center; gap: 10px;
      flex-shrink: 0;
    }
    #chat-header-icon { font-size: 1.2rem; }
    #chat-header-title { font-size: 0.95rem; font-weight: 700; flex: 1; letter-spacing: -0.2px; }
    #chat-header-sub { font-size: 0.7rem; opacity: 0.5; margin-top: 1px; }
    #chat-clear-btn {
      background: rgba(255,255,255,0.1); border: none; color: rgba(255,255,255,0.6);
      padding: 5px 10px; border-radius: 7px; font-size: 0.72rem; font-weight: 600;
      cursor: pointer; transition: background 0.15s; white-space: nowrap;
    }
    #chat-clear-btn:hover { background: rgba(255,255,255,0.18); color: #fff; }

    /* Messages */
    #chat-messages {
      flex: 1; overflow-y: auto; padding: 16px 14px;
      display: flex; flex-direction: column; gap: 10px;
      scroll-behavior: smooth;
    }
    .chat-msg { display: flex; flex-direction: column; max-width: 88%; }
    .chat-msg.user { align-self: flex-end; align-items: flex-end; }
    .chat-msg.assistant { align-self: flex-start; align-items: flex-start; }
    .chat-bubble {
      padding: 9px 13px; border-radius: 14px;
      font-size: 0.87rem; line-height: 1.55; word-break: break-word;
    }
    .chat-msg.user .chat-bubble {
      background: #5b6af5; color: #fff; border-bottom-right-radius: 4px;
    }
    .chat-msg.assistant .chat-bubble {
      background: #f2f3ff; color: #1a1a2e; border-bottom-left-radius: 4px;
    }
    .chat-msg.system .chat-bubble {
      background: #f7f8fc; color: #888; font-size: 0.8rem;
      border-radius: 10px; font-style: italic;
    }

    /* Tool result pill */
    .chat-tool-pill {
      margin-top: 5px; padding: 6px 10px; border-radius: 8px;
      background: #f0fdf4; border: 1px solid #d1fae5;
      font-size: 0.76rem; color: #059669; font-weight: 600;
      display: flex; align-items: center; gap: 5px;
    }
    .chat-tool-pill.error { background: #fef2f2; border-color: #fecaca; color: #e53935; }

    /* Typing indicator */
    .chat-typing { display: flex; gap: 4px; padding: 10px 13px; align-items: center; }
    .chat-typing span {
      width: 7px; height: 7px; border-radius: 50%; background: #bbb;
      animation: typingDot 1.1s infinite ease-in-out;
    }
    .chat-typing span:nth-child(2) { animation-delay: 0.18s; }
    .chat-typing span:nth-child(3) { animation-delay: 0.36s; }
    @keyframes typingDot {
      0%, 80%, 100% { transform: scale(0.8); opacity: 0.5; }
      40% { transform: scale(1.1); opacity: 1; }
    }

    /* Input */
    #chat-input-area {
      padding: 12px 14px; border-top: 1px solid #f0f0f0;
      display: flex; gap: 8px; align-items: flex-end; flex-shrink: 0;
      background: #fff;
    }
    #chat-input {
      flex: 1; resize: none; border: 1.5px solid #e4e4e4; border-radius: 10px;
      padding: 9px 12px; font-size: 0.88rem; font-family: inherit;
      outline: none; background: #fafafa; color: #1a1a2e;
      max-height: 120px; overflow-y: auto; line-height: 1.45;
      transition: border-color 0.15s, background 0.15s;
    }
    #chat-input:focus { border-color: #5b6af5; background: #fff; }
    #chat-input::placeholder { color: #bbb; }
    #chat-send-btn {
      width: 38px; height: 38px; border-radius: 10px; flex-shrink: 0;
      background: #5b6af5; color: #fff; border: none; cursor: pointer;
      font-size: 1rem; display: flex; align-items: center; justify-content: center;
      transition: background 0.15s, transform 0.1s;
    }
    #chat-send-btn:hover { background: #4a57e0; }
    #chat-send-btn:active { transform: scale(0.95); }
    #chat-send-btn:disabled { background: #c5c9f5; cursor: not-allowed; }

    /* Suggestions */
    #chat-suggestions {
      padding: 0 14px 12px; display: flex; flex-wrap: wrap; gap: 6px; flex-shrink: 0;
    }
    .chat-suggestion {
      padding: 5px 11px; border-radius: 20px; border: 1.5px solid #e0e2ff;
      background: #f0f1ff; color: #5b6af5; font-size: 0.76rem; font-weight: 600;
      cursor: pointer; white-space: nowrap; transition: background 0.12s, border-color 0.12s;
    }
    .chat-suggestion:hover { background: #5b6af5; color: #fff; border-color: #5b6af5; }

    /* Backdrop on mobile */
    #chat-backdrop {
      display: none; position: fixed; inset: 0; z-index: 840;
      background: rgba(0,0,0,0.3);
    }
    #chat-backdrop.open { display: block; }

    @media (max-width: 480px) {
      #chat-panel { width: 100vw; }
    }
  `;
  document.head.appendChild(style);

  // ── HTML structure ──────────────────────────────────────────────────────────
  document.body.insertAdjacentHTML("beforeend", `
    <div id="chat-backdrop"></div>
    <button id="chat-fab" title="AI Assistant">💬</button>
    <div id="chat-panel">
      <div id="chat-header">
        <div id="chat-header-icon">✨</div>
        <div>
          <div id="chat-header-title">Finance Assistant</div>
          <div id="chat-header-sub">Add expenses · Analyze spending · Plan budgets</div>
        </div>
        <button id="chat-clear-btn" title="Clear conversation">Clear</button>
      </div>
      <div id="chat-messages"></div>
      <div id="chat-suggestions">
        <button class="chat-suggestion" data-msg="How am I doing this month?">📊 This month</button>
        <button class="chat-suggestion" data-msg="Help me set up a budget for this month">💰 Set budget</button>
        <button class="chat-suggestion" data-msg="Show my recent expenses">📋 Recent expenses</button>
        <button class="chat-suggestion" data-msg="What categories am I spending the most on?">🏷️ Top categories</button>
      </div>
      <div id="chat-input-area">
        <textarea id="chat-input" rows="1" placeholder="Add expenses, ask about spending…"></textarea>
        <button id="chat-send-btn" title="Send">➤</button>
      </div>
    </div>
  `);

  // ── State ───────────────────────────────────────────────────────────────────
  let isOpen = false;
  let isStreaming = false;
  let history = loadHistory();

  const panel = document.getElementById("chat-panel");
  const fab = document.getElementById("chat-fab");
  const backdrop = document.getElementById("chat-backdrop");
  const messages = document.getElementById("chat-messages");
  const input = document.getElementById("chat-input");
  const sendBtn = document.getElementById("chat-send-btn");

  // ── Toggle ──────────────────────────────────────────────────────────────────
  function togglePanel() {
    isOpen = !isOpen;
    panel.classList.toggle("open", isOpen);
    backdrop.classList.toggle("open", isOpen);
    fab.classList.toggle("open", isOpen);
    fab.textContent = isOpen ? "✕" : "💬";
    if (isOpen) {
      renderHistory();
      setTimeout(() => input.focus(), 300);
    }
  }

  fab.addEventListener("click", togglePanel);
  backdrop.addEventListener("click", togglePanel);
  document.getElementById("chat-clear-btn").addEventListener("click", () => {
    history = [];
    saveHistory();
    messages.innerHTML = "";
    appendSystemMsg("Conversation cleared. How can I help?");
  });

  // ── Suggestions ─────────────────────────────────────────────────────────────
  document.querySelectorAll(".chat-suggestion").forEach(btn => {
    btn.addEventListener("click", () => {
      const msg = btn.dataset.msg;
      if (!isOpen) togglePanel();
      setTimeout(() => sendMessage(msg), isOpen ? 0 : 320);
    });
  });

  // ── Input handling ──────────────────────────────────────────────────────────
  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 120) + "px";
  });
  input.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  });
  sendBtn.addEventListener("click", handleSend);

  function handleSend() {
    const text = input.value.trim();
    if (!text || isStreaming) return;
    input.value = "";
    input.style.height = "auto";
    sendMessage(text);
  }

  // ── History persistence ─────────────────────────────────────────────────────
  function loadHistory() {
    try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]"); } catch { return []; }
  }
  function saveHistory() {
    // Keep last 40 messages to avoid bloat
    if (history.length > 40) history = history.slice(-40);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(history));
  }

  function renderHistory() {
    messages.innerHTML = "";
    if (history.length === 0) {
      appendSystemMsg("Hi! I'm your finance assistant. Tell me about your expenses, ask how you're spending, or let me help plan your budget.");
      return;
    }
    for (const msg of history) {
      if (msg.role === "user") appendUserBubble(msg.content, false);
      else if (msg.role === "assistant") appendAssistantBubble(msg.content, false);
    }
    scrollBottom();
  }

  // ── Rendering helpers ───────────────────────────────────────────────────────
  function appendSystemMsg(text) {
    const div = document.createElement("div");
    div.className = "chat-msg system";
    div.innerHTML = `<div class="chat-bubble">${escHtml(text)}</div>`;
    messages.appendChild(div);
    scrollBottom();
  }

  function appendUserBubble(text, scroll = true) {
    const div = document.createElement("div");
    div.className = "chat-msg user";
    div.innerHTML = `<div class="chat-bubble">${escHtml(text)}</div>`;
    messages.appendChild(div);
    if (scroll) scrollBottom();
    return div;
  }

  function appendAssistantBubble(text, scroll = true) {
    const div = document.createElement("div");
    div.className = "chat-msg assistant";
    div.innerHTML = `<div class="chat-bubble">${renderMarkdown(text)}</div>`;
    messages.appendChild(div);
    if (scroll) scrollBottom();
    return div;
  }

  function appendTyping() {
    const div = document.createElement("div");
    div.className = "chat-msg assistant";
    div.id = "chat-typing-indicator";
    div.innerHTML = `<div class="chat-bubble chat-typing"><span></span><span></span><span></span></div>`;
    messages.appendChild(div);
    scrollBottom();
    return div;
  }

  function appendToolPill(name, result) {
    const isError = result && result.error;
    const label = formatToolResult(name, result);
    const pill = document.createElement("div");
    pill.className = "chat-tool-pill" + (isError ? " error" : "");
    pill.textContent = (isError ? "⚠ " : "✓ ") + label;
    messages.appendChild(pill);
    scrollBottom();
    // Notify page to refresh if expenses or budget changed
    if (!isError && ["add_expenses", "set_budget", "set_category_budget"].includes(name)) {
      window.dispatchEvent(new CustomEvent("chatDataChanged", { detail: { tool: name, result } }));
    }
  }

  function formatToolResult(name, result) {
    if (result.error) return result.error;
    if (name === "add_expenses") {
      const n = result.count || 0;
      return n === 1 ? `Added 1 expense` : `Added ${n} expenses`;
    }
    if (name === "set_budget") return `Budget set: $${result.total_amount} for ${result.year_month}`;
    if (name === "set_category_budget") return `Allocated $${result.amount} to ${result.category}`;
    if (name === "get_spending_summary") return `Fetched spending for ${result.year_month}`;
    if (name === "get_recent_expenses") return `Fetched ${result.count} expenses`;
    if (name === "list_categories") return `Loaded ${result.categories?.length || 0} categories`;
    if (name === "get_budget") return result.exists ? `Fetched budget for ${result.year_month}` : `No budget for ${result.year_month}`;
    return name;
  }

  function scrollBottom() {
    messages.scrollTop = messages.scrollHeight;
  }

  // Simple markdown: **bold**, *italic*, bullet lists, newlines
  function renderMarkdown(text) {
    if (!text) return "";
    let html = escHtml(text);
    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/\*(.+?)\*/g, "<em>$1</em>");
    // Lists: lines starting with - or *
    html = html.replace(/^[-•]\s+(.+)$/gm, "<li>$1</li>");
    html = html.replace(/(<li>.*<\/li>)/s, "<ul style='margin:5px 0 5px 16px;'>$1</ul>");
    html = html.replace(/\n/g, "<br>");
    return html;
  }

  function escHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // ── Send + stream ───────────────────────────────────────────────────────────
  async function sendMessage(text) {
    if (!isOpen) togglePanel();
    appendUserBubble(text);
    history.push({ role: "user", content: text });
    saveHistory();

    isStreaming = true;
    sendBtn.disabled = true;
    input.disabled = true;

    const typingEl = appendTyping();

    let assistantText = "";
    let assistantBubble = null;

    try {
      const res = await fetch("/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: history.slice(-20) }),
      });

      if (!res.ok) throw new Error(`Server error: ${res.status}`);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      typingEl.remove();

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop();

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          let payload;
          try { payload = JSON.parse(line.slice(6)); } catch { continue; }

          if (payload.type === "text") {
            assistantText += payload.content;
            if (!assistantBubble) {
              assistantBubble = appendAssistantBubble("", false);
            }
            assistantBubble.querySelector(".chat-bubble").innerHTML = renderMarkdown(assistantText);
            scrollBottom();
          } else if (payload.type === "tool_result") {
            appendToolPill(payload.name, payload.result);
          } else if (payload.type === "done") {
            break;
          }
        }
      }
    } catch (err) {
      typingEl.remove();
      appendSystemMsg("Sorry, something went wrong. Please try again.");
      console.error("Chat error:", err);
    }

    if (assistantText) {
      history.push({ role: "assistant", content: assistantText });
      saveHistory();
    }

    isStreaming = false;
    sendBtn.disabled = false;
    input.disabled = false;
    input.focus();
  }

  // ── Inject user info into nav ────────────────────────────────────────────────
  fetch('/me').then(r => r.json()).then(user => {
    const nav = document.querySelector('nav');
    if (!nav) return;
    const wrap = document.createElement('div');
    wrap.style.cssText = 'margin-left:auto;display:flex;align-items:center;gap:10px;flex-shrink:0;';
    wrap.innerHTML = `
      ${user.is_admin ? `<a href="/admin" style="color:#f59e0b;font-size:0.72rem;font-weight:800;text-decoration:none;border:1px solid rgba(245,158,11,0.35);padding:3px 8px;border-radius:6px;letter-spacing:0.3px;">ADMIN</a>` : ''}
      <span style="color:rgba(255,255,255,0.5);font-size:0.82rem;max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${(user.name || user.email || '').replace(/</g,'&lt;')}</span>
      <a href="/auth/logout" style="color:rgba(255,255,255,0.35);font-size:0.82rem;text-decoration:none;white-space:nowrap;border:1px solid rgba(255,255,255,0.1);padding:3px 10px;border-radius:6px;">Sign out</a>
    `;
    nav.appendChild(wrap);
  }).catch(() => {});

  // ── Init ────────────────────────────────────────────────────────────────────
  // No auto-open; wait for user to click FAB
})();
