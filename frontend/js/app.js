'use strict';

(() => {
  /* =====================================================================
     Helpers
     ===================================================================== */
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = Render.esc;
  const LS = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch (_) { return d; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (_) { /* ignore */ } },
  };

  const ICONS = {
    plus: '<path d="M12 5v14M5 12h14"/>',
    cap: '<path d="M22 9 12 4 2 9l10 5 10-5z"/><path d="M6 11v5c0 1.5 3 3 6 3s6-1.5 6-3v-5"/>',
    sidebar: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
    settings: '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
    chevron: '<path d="m6 9 6 6 6-6"/>',
    arrowDown: '<path d="M12 5v14M5 12l7 7 7-7"/>',
    arrowUp: '<path d="M12 19V5M5 12l7-7 7 7"/>',
    stop: '<rect x="6.5" y="6.5" width="11" height="11" rx="2" fill="currentColor"/>',
    copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    more: '<circle cx="5" cy="12" r="1.4" fill="currentColor"/><circle cx="12" cy="12" r="1.4" fill="currentColor"/><circle cx="19" cy="12" r="1.4" fill="currentColor"/>',
    pin: '<path d="M12 17v5M9 3h6l-1 7 3 3H7l3-3z"/>',
    archive: '<rect x="3" y="4" width="18" height="4" rx="1"/><path d="M5 8v11a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8M10 12h4"/>',
    trash: '<path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14"/>',
    edit: '<path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
    download: '<path d="M12 3v12M7 10l5 5 5-5M5 21h14"/>',
    x: '<path d="M18 6 6 18M6 6l12 12"/>',
    command: '<path d="M9 6a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3z"/>',
    back: '<path d="M19 12H5M12 19l-7-7 7-7"/>',
    restore: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5"/>',
    refresh: '<path d="M21 12a9 9 0 1 1-3-6.7L21 8M21 3v5h-5"/>',
    thumbUp: '<path d="M7 11v9H4v-9zM7 11l4-8a2 2 0 0 1 2 2v4h5.5a2 2 0 0 1 2 2.3l-1 6A2 2 0 0 1 17.5 20H7"/>',
    thumbDown: '<path d="M7 13V4H4v9zM7 13l4 8a2 2 0 0 0 2-2v-4h5.5a2 2 0 0 0 2-2.3l-1-6A2 2 0 0 0 17.5 4H7"/>',
    speaker: '<path d="M11 5 6 9H3v6h3l5 4z"/><path d="M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13"/>',
    globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
    code: '<path d="m8 8-5 4 5 4M16 8l5 4-5 4M14 5l-4 14"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    brain: '<path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-4 10.5c.7.7 1 1.5 1 2.5h6c0-1 .3-1.8 1-2.5A6 6 0 0 0 12 3z"/>',
    chart: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    plug: '<path d="M9 2v6M15 2v6M7 8h10v4a5 5 0 0 1-10 0zM12 17v5"/>',
    mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
    folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    bot: '<rect x="4" y="8" width="16" height="11" rx="3"/><path d="M12 4v4M9 13h.01M15 13h.01M9 16.5h6"/>',
    book: '<path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2zM4 19V5M19 17H6"/>',
    panel: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/>',
    compass: '<circle cx="12" cy="12" r="9"/><path d="m15.5 8.5-2 5-5 2 2-5z"/>',
    paperclip: '<path d="m21 12-8.5 8.5a5 5 0 0 1-7-7L14 5a3.3 3.3 0 0 1 4.7 4.7l-8.5 8.5a1.7 1.7 0 0 1-2.4-2.4L15 8"/>',
  };
  const icon = (n, size) => `<svg class="i" viewBox="0 0 24 24"${size ? ` style="width:${size}px;height:${size}px"` : ''}>${ICONS[n]}</svg>`;

  function toast(msg, ms = 2200) {
    const t = document.createElement('div');
    t.className = 'toast';
    t.textContent = msg;
    $('#toasts').appendChild(t);
    setTimeout(() => t.remove(), ms);
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
    } catch (_) {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
    }
  }

  function download(filename, text, mime = 'text/plain') {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], { type: mime }));
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }

  function fmtTime(ts) {
    const d = new Date(ts * 1000);
    return d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  }
  const fmtSize = (b) => (b ? (b / 1e9 >= 1 ? (b / 1e9).toFixed(1) + ' GB' : Math.round(b / 1e6) + ' MB') : '');

  function confirmDialog(title, message, okLabel = 'Delete') {
    return new Promise((resolve) => {
      const dlg = $('#dlg-confirm');
      $('#confirm-title').textContent = title;
      $('#confirm-msg').textContent = message;
      $('#confirm-ok').textContent = okLabel;
      const done = (v) => { dlg.close(); resolve(v); };
      $('#confirm-ok').onclick = () => done(true);
      $('#confirm-cancel').onclick = () => done(false);
      dlg.oncancel = () => resolve(false);
      dlg.showModal();
    });
  }

  /* =====================================================================
     State
     ===================================================================== */
  const S = {
    settings: {},
    chats: [],
    current: null, // chat object or null (unsaved new chat)
    messages: [], // [{role, content}] used for the token counter
    models: [],
    modelsOk: true,
    model: '',
    streaming: false,
    abort: null,
    archivedView: false,
    search: '',
    stick: true,
    pending: [], // attachments waiting to be sent
    next: { assistant: null, project: null }, // assistant / project the next new chat starts with
    lib: { assistants: [], projects: [] },
    servers: [], // MCP servers (for the + menu switches)
  };

  /* =====================================================================
     Theme / font
     ===================================================================== */
  const mq = matchMedia('(prefers-color-scheme: dark)');
  function applyTheme() {
    const pref = S.settings.theme || LS.get('theme', 'system');
    const dark = pref === 'dark' || (pref === 'system' && mq.matches);
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
    Render.setMermaidTheme(dark);
    Render.renderBlocks(document, true); // re-draw charts/graphs in the new colors
  }
  mq.addEventListener('change', applyTheme);
  function applyFont() {
    const px = S.settings.font_size || 16;
    document.documentElement.style.setProperty('--fs', px + 'px');
  }
  function applyAppearance() {
    LS.set('theme', S.settings.theme || 'system');
    LS.set('font_size', String(S.settings.font_size || 16));
    applyTheme();
    applyFont();
    const name = (S.settings.display_name || '').trim();
    $('#avatar').textContent = (name[0] || 'U').toUpperCase();
    $('#sb-user').textContent = name || 'Settings';
    updateGreeting();
  }

  /* =====================================================================
     Sidebar
     ===================================================================== */
  const isMobile = () => matchMedia('(max-width: 768px)').matches;

  function setSidebar(open) {
    if (isMobile()) {
      document.body.classList.toggle('sidebar-open', open);
    } else {
      document.documentElement.classList.remove('pre-collapsed');
      document.body.classList.toggle('sidebar-collapsed', !open);
      LS.set('sb', open ? 'open' : 'collapsed');
    }
  }
  function toggleSidebar() {
    const open = isMobile()
      ? !document.body.classList.contains('sidebar-open')
      : document.body.classList.contains('sidebar-collapsed') || document.documentElement.classList.contains('pre-collapsed');
    setSidebar(open);
  }

  function groupLabel(ts) {
    const sod = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
    const diff = Math.round((sod(new Date()) - sod(new Date(ts * 1000))) / 86400000);
    if (diff <= 0) return 'Today';
    if (diff === 1) return 'Yesterday';
    if (diff <= 7) return 'Previous 7 days';
    if (diff <= 30) return 'Previous 30 days';
    return 'Older';
  }

  async function loadChats() {
    S.related = [];
    try {
      const q = S.search ? `&q=${encodeURIComponent(S.search)}` : '';
      S.chats = await API.get(`/api/chats?archived=${S.archivedView}${q}`);
    } catch (e) {
      S.chats = [];
    }
    renderSidebar();
    if (S.search && S.search.length >= 3 && !S.archivedView) {
      const asked = S.search;
      try {
        const d = await API.get(`/api/search/chats?q=${encodeURIComponent(asked)}`);
        if (S.search === asked) {
          const have = new Set(S.chats.map((c) => c.id));
          S.related = (d.results || []).filter((r) => !have.has(r.chat_id));
          renderSidebar();
        }
      } catch (_) { /* optional: word search above still works */ }
    }
  }

  function chatItemHTML(c) {
    return (
      `<div class="chat-item${S.current && S.current.id === c.id ? ' active' : ''}" data-id="${c.id}" role="button" tabindex="0">` +
      (c.pinned ? `<span class="pin">${icon('pin')}</span>` : '') +
      `<span class="title">${esc(c.title)}</span>` +
      `<button class="more" aria-label="Chat actions" title="Actions">${icon('more', 16)}</button></div>`
    );
  }

  function renderSidebar() {
    const list = $('#chat-list');
    let html = '';
    if (S.archivedView) {
      html += `<div class="list-head"><button class="icon-btn" id="arch-back" title="Back" aria-label="Back">${icon('back')}</button> Archived chats</div>`;
    }
    if (!S.chats.length) {
      html += `<div class="list-empty${S.search && (S.related || []).length ? ' hidden' : ''}">${S.search ? 'No chats match your search.' : S.archivedView ? 'No archived chats.' : 'No chats yet.'}</div>`;
    } else if (S.archivedView || S.search) {
      html += S.chats.map(chatItemHTML).join('');
    } else {
      const pinned = S.chats.filter((c) => c.pinned);
      const rest = S.chats.filter((c) => !c.pinned);
      if (pinned.length) html += `<div class="group-label">Pinned</div>` + pinned.map(chatItemHTML).join('');
      let last = '';
      for (const c of rest) {
        const g = groupLabel(c.updated_at);
        if (g !== last) { html += `<div class="group-label">${g}</div>`; last = g; }
        html += chatItemHTML(c);
      }
    }
    if (S.search && (S.related || []).length) {
      html += '<div class="group-label">Related by meaning</div>' + S.related.map((r) =>
        `<div class="chat-item related" data-id="${r.chat_id}" role="button" tabindex="0" title="${esc(r.snippet)}">` +
        `<span class="title">${esc(r.title)}<small class="rel-snip">${esc(r.snippet.slice(0, 70))}</small></span></div>`).join('');
    }
    list.innerHTML = html;
    $('#btn-archived').classList.toggle('hidden', S.archivedView);
  }

  $('#chat-list').addEventListener('click', (e) => {
    if (e.target.closest('#arch-back')) { S.archivedView = false; loadChats(); return; }
    const item = e.target.closest('.chat-item');
    if (!item || item.querySelector('input.rename')) return;
    if (item.classList.contains('related')) { openChat(item.dataset.id); if (isMobile()) setSidebar(false); return; }
    const chat = S.chats.find((c) => c.id === item.dataset.id);
    if (!chat) return;
    if (e.target.closest('.more')) { openChatMenu(chat, item, e.target.closest('.more')); return; }
    openChat(chat.id);
    if (isMobile()) setSidebar(false);
  });
  $('#chat-list').addEventListener('keydown', (e) => {
    if ((e.key === 'Enter' || e.key === ' ') && e.target.classList.contains('chat-item')) { e.preventDefault(); e.target.click(); }
  });

  let searchTimer;
  $('#search').addEventListener('input', (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { S.search = e.target.value.trim(); loadChats(); }, 180);
  });
  $('#btn-archived').onclick = () => { S.archivedView = true; S.search = ''; $('#search').value = ''; loadChats(); };

  /* ---- chat context menu ---- */
  function closeMenus() {
    $('#ctx-menu').classList.add('hidden');
    $('#model-menu').classList.add('hidden');
    $('#plus-menu').classList.add('hidden');
    $$('.chat-item.menu-open').forEach((n) => n.classList.remove('menu-open'));
  }

  function openChatMenu(chat, item, anchor) {
    const menu = $('#ctx-menu');
    const entries = S.archivedView
      ? [['restore', 'Restore', 'restore'], ['export', 'Export as Markdown', 'download'], ['delete', 'Delete', 'trash', true]]
      : [
          ['pin', chat.pinned ? 'Unpin' : 'Pin', 'pin'],
          ['rename', 'Rename', 'edit'],
          ['export', 'Export as Markdown', 'download'],
          ['archive', 'Archive', 'archive'],
          ['delete', 'Delete', 'trash', true],
        ];
    menu.innerHTML = entries
      .map(([act, label, ic, danger]) => `<button class="menu-item${danger ? ' danger' : ''}" data-act="${act}">${icon(ic, 16)}${label}</button>`)
      .join('');
    closeMenus();
    item.classList.add('menu-open');
    menu.classList.remove('hidden');
    const r = anchor.getBoundingClientRect();
    const mh = menu.offsetHeight;
    menu.style.left = Math.min(r.right + 4, innerWidth - menu.offsetWidth - 8) + 'px';
    menu.style.top = Math.max(8, Math.min(r.top, innerHeight - mh - 8)) + 'px';
    menu.onclick = async (e) => {
      const b = e.target.closest('[data-act]');
      if (!b) return;
      closeMenus();
      await chatAction(b.dataset.act, chat, item);
    };
  }

  async function chatAction(act, chat, item) {
    try {
      if (act === 'pin') { await API.patch(`/api/chats/${chat.id}`, { pinned: !chat.pinned }); }
      else if (act === 'archive') {
        await API.patch(`/api/chats/${chat.id}`, { archived: true });
        if (S.current && S.current.id === chat.id) newChat();
        toast('Chat archived');
      } else if (act === 'restore') { await API.patch(`/api/chats/${chat.id}`, { archived: false }); toast('Chat restored'); }
      else if (act === 'export') { location.href = `/api/chats/${chat.id}/export?format=md`; return; }
      else if (act === 'delete') {
        if (!(await confirmDialog('Delete chat?', `“${chat.title}” will be permanently deleted.`))) return;
        await API.del(`/api/chats/${chat.id}`);
        if (S.current && S.current.id === chat.id) newChat();
      } else if (act === 'rename') { startRename(chat, item); return; }
      await loadChats();
    } catch (e) { toast(e.message); }
  }

  function startRename(chat, item) {
    const title = item.querySelector('.title');
    const input = document.createElement('input');
    input.className = 'rename';
    input.value = chat.title;
    title.replaceWith(input);
    input.focus();
    input.select();
    let finished = false;
    const finish = async (save) => {
      if (finished) return;
      finished = true;
      const v = input.value.trim();
      if (save && v && v !== chat.title) {
        try {
          const updated = await API.patch(`/api/chats/${chat.id}`, { title: v });
          if (S.current && S.current.id === chat.id) { S.current = { ...S.current, ...updated }; updateDocTitle(); }
        } catch (e) { toast(e.message); }
      }
      loadChats();
    };
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') finish(true);
      if (e.key === 'Escape') finish(false);
      e.stopPropagation();
    });
    input.addEventListener('blur', () => finish(true));
    input.addEventListener('click', (e) => e.stopPropagation());
  }

  document.addEventListener('click', (e) => {
    if (!e.target.closest('#ctx-menu, #plus-menu, #btn-plus, .chat-item .more, .model-picker')) closeMenus();
  });

  /* =====================================================================
     Models
     ===================================================================== */
  async function loadModels() {
    $('#model-label').textContent = 'Loading models…';
    let info;
    try { info = await API.get('/api/models'); } catch (e) { info = { ok: false, models: [], error: 'Backend not reachable.', hint: e.message }; }
    S.models = info.models || [];
    S.modelsOk = !!info.ok;
    const banner = $('#banner');
    if (!info.ok) {
      banner.innerHTML = `<div class="grow"><strong>${esc(info.error || 'Model server unavailable.')}</strong><br><span class="muted">${inlineCode(info.hint || '')}</span></div><button class="btn" id="banner-retry">Retry</button>`;
      banner.classList.remove('hidden');
    } else if (!S.models.length) {
      banner.innerHTML = `<div class="grow"><strong>No models installed yet.</strong><br><span class="muted">${inlineCode('Download one with `ollama pull llama3.2`, then press Retry.')}</span></div><button class="btn" id="banner-retry">Retry</button>`;
      banner.classList.remove('hidden');
    } else {
      banner.classList.add('hidden');
    }
    const names = S.models.map((m) => m.name);
    if (!S.model || (names.length && !names.includes(S.model) && !(S.current && S.current.model === S.model))) {
      S.model = names.includes(S.settings.default_model) ? S.settings.default_model : names[0] || '';
    }
    renderModelButton();
  }
  const inlineCode = (s) => esc(s).replace(/`([^`]+)`/g, '<code>$1</code>');
  $('#banner').addEventListener('click', (e) => { if (e.target.id === 'banner-retry') loadModels(); });

  function renderModelButton() {
    const missing = S.model && S.modelsOk && S.models.length && !S.models.some((m) => m.name === S.model);
    $('#model-label').textContent = S.model ? S.model + (missing ? ' (not installed)' : '') : S.modelsOk ? 'No models' : 'Not connected';
  }

  $('#model-btn').onclick = (e) => {
    e.stopPropagation();
    const menu = $('#model-menu');
    if (!menu.classList.contains('hidden')) { menu.classList.add('hidden'); return; }
    closeMenus();
    let html = '';
    if (!S.models.length) {
      html = `<div class="menu-note">${S.modelsOk ? inlineCode('No models found. Install one with `ollama pull llama3.2`.') : 'Model server not reachable — see the banner above.'}</div>`;
    } else {
      html = S.models
        .map((m) => `<button class="menu-item${m.name === S.model ? ' sel' : ''}" data-model="${esc(m.name)}" role="option"><span class="tick">${m.name === S.model ? icon('check', 14) : ''}</span>${esc(m.name)}<span class="meta">${esc([m.params, fmtSize(m.size)].filter(Boolean).join(' · '))}</span></button>`)
        .join('');
    }
    html += `<div class="menu-sep"></div><button class="menu-item" data-refresh>${icon('restore', 16)} Refresh list</button>`;
    menu.innerHTML = html;
    menu.classList.remove('hidden');
  };
  $('#model-menu').addEventListener('click', async (e) => {
    const item = e.target.closest('[data-model]');
    if (e.target.closest('[data-refresh]')) { await loadModels(); closeMenus(); return; }
    if (!item) return;
    S.model = item.dataset.model;
    renderModelButton();
    closeMenus();
    if (S.current) {
      S.current.model = S.model;
      API.patch(`/api/chats/${S.current.id}`, { model: S.model }).catch(() => {});
    }
  });

  /* =====================================================================
     Welcome screen / composer placement
     ===================================================================== */
  const CARDS = [
    { t: 'Explain a concept', p: 'Explain how transformers work in machine learning, as if I’m a curious beginner. Include a simple analogy.' },
    { t: 'Write some code', p: 'Write a Python script that renames all files in a folder with a date prefix. Include a dry-run option and explain how it works.' },
    { t: 'Brainstorm ideas', p: 'Brainstorm 10 creative names with taglines for a small coffee shop that also sells used books.' },
    { t: 'Draft a message', p: 'Draft a polite but firm email to my landlord asking them to fix a leaking tap that has been dripping for two weeks.' },
  ];
  $('#cards').innerHTML = CARDS.map((c, i) => `<button class="card" data-i="${i}"><b>${esc(c.t)}</b><span>${esc(c.p)}</span></button>`).join('');
  $('#cards').addEventListener('click', (e) => {
    const b = e.target.closest('.card');
    if (b) send(CARDS[+b.dataset.i].p);
  });

  function updateGreeting() {
    const h = new Date().getHours();
    const part = h < 5 ? 'Still up' : h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening';
    const name = (S.settings.display_name || '').trim();
    $('#greeting').textContent = part === 'Still up' ? (name ? `Still up, ${name}?` : 'Still up?') : name ? `${part}, ${name}` : `${part}. How can I help?`;
  }

  function setEmpty(empty) {
    $('#main').classList.toggle('empty', empty);
    const wrap = $('#composer-wrap');
    (empty ? $('#welcome-slot') : $('#dock-slot')).appendChild(wrap);
    if (empty) { $('#thread').innerHTML = ''; updateGreeting(); }
  }

  /* =====================================================================
     Composer
     ===================================================================== */
  const input = $('#input');
  function autosize() {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 220) + 'px';
  }
  function updateCounter() {
    const ctx = S.settings.num_ctx || 8192;
    const used = S.messages.reduce((a, m) => a + Math.ceil(m.content.length / 3.5), 0) + Math.ceil(input.value.length / 3.5);
    const ratio = used / ctx;
    const c = $('#counter');
    c.textContent = ratio > 0.7 ? `~${used.toLocaleString()} / ${ctx.toLocaleString()} tokens` : '';
    c.classList.toggle('warn', ratio > 0.95);
  }
  const hasReadyAttachments = () => S.pending.some((p) => p.status === 'ready');
  function updateSendButton() {
    const b = $('#send');
    b.classList.toggle('stop', S.streaming);
    b.innerHTML = icon(S.streaming ? 'stop' : 'arrowUp');
    b.title = S.streaming ? 'Stop generating' : 'Send';
    b.setAttribute('aria-label', b.title);
    b.disabled = !S.streaming && !input.value.trim() && !hasReadyAttachments();
  }
  input.addEventListener('input', () => { autosize(); updateCounter(); updateSendButton(); });
  input.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' || e.isComposing) return;
    const sendOnEnter = S.settings.send_on_enter !== false;
    if (sendOnEnter ? !e.shiftKey : e.ctrlKey || e.metaKey) { e.preventDefault(); submit(); }
  });

  /* ---- "+" menu switches: Web search / Thinking (persisted as settings) ---- */
  function toggleSetting(key, dflt) {
    const cur = S.settings[key] === undefined ? dflt : S.settings[key];
    updateSetting(key, !cur);
  }
  $('#send').onclick = () => (S.streaming ? S.abort && S.abort.abort() : submit());
  function submit() {
    if (S.streaming) return;
    if (S.pending.some((p) => p.status === 'uploading')) { toast('Wait for the upload to finish'); return; }
    const text = input.value.trim();
    const files = S.pending.filter((p) => p.status === 'ready').map(({ id, name, mime, size, kind }) => ({ id, name, mime, size, kind }));
    if (!text && !files.length) return;
    input.value = '';
    autosize();
    clearPending();
    updateSendButton();
    send(text, { files });
  }

  /* =====================================================================
     Attachments: upload, chips, drag & drop, paste, "+" menu
     ===================================================================== */
  let pendingSeq = 0;
  const KIND_LABEL = { pdf: 'PDF', docx: 'DOC', sheet: 'CSV', text: 'TXT', image: 'IMG', file: 'FILE' };
  function guessKind(name) {
    const ext = (name.split('.').pop() || '').toLowerCase();
    if (/^(png|jpe?g|gif|webp|bmp)$/.test(ext)) return 'image';
    if (ext === 'pdf') return 'pdf';
    if (ext === 'docx') return 'docx';
    if (/^(csv|tsv|xlsx|xlsm|xls)$/.test(ext)) return 'sheet';
    if (/^(txt|md|json|ya?ml|xml|html?|css|js|ts|tsx|jsx|py|java|c|h|cpp|cs|go|rs|rb|php|sh|sql|log|toml|ini)$/.test(ext)) return 'text';
    return 'file';
  }

  function renderAttachRow() {
    const row = $('#attach-row');
    row.classList.toggle('hidden', !S.pending.length);
    row.innerHTML = S.pending
      .map(
        (p, i) =>
          `<div class="att-chip ${p.status}" title="${esc(p.error || p.name)}">` +
          (p.preview ? `<img src="${p.preview}" alt="">` : `<span class="ext">${KIND_LABEL[p.kind] || 'FILE'}</span>`) +
          `<span class="att-name">${esc(p.name)}</span>` +
          (p.status === 'uploading' ? '<span class="spin"></span>' : '') +
          `<button class="att-x" data-i="${i}" aria-label="Remove ${esc(p.name)}">${icon('x', 12)}</button></div>`
      )
      .join('');
  }
  $('#attach-row').addEventListener('click', (e) => {
    const b = e.target.closest('.att-x');
    if (!b) return;
    const [p] = S.pending.splice(+b.dataset.i, 1);
    if (p && p.preview) URL.revokeObjectURL(p.preview);
    renderAttachRow();
    updateSendButton();
  });
  function clearPending() {
    S.pending.forEach((p) => p.preview && URL.revokeObjectURL(p.preview));
    S.pending = [];
    renderAttachRow();
  }

  async function addFiles(files) {
    for (const f of files) {
      if (f.size > 250 * 1024 * 1024) { toast(`${f.name} is larger than 250 MB`); continue; }
      const item = {
        tid: ++pendingSeq, name: f.name || 'file', size: f.size, kind: guessKind(f.name || ''),
        preview: f.type.startsWith('image/') && /^image\/(png|jpe?g|gif|webp)$/.test(f.type) ? URL.createObjectURL(f) : null,
        status: 'uploading',
      };
      S.pending.push(item);
      renderAttachRow();
      updateSendButton();
      try {
        const fd = new FormData();
        fd.append('file', f, item.name);
        const r = await fetch('/api/files', { method: 'POST', body: fd });
        if (!r.ok) throw new Error(((await r.json().catch(() => ({}))).detail) || r.statusText);
        Object.assign(item, await r.json(), { status: 'ready' });
      } catch (err) {
        item.status = 'error';
        item.error = err.message;
        toast(`Couldn’t upload ${item.name}: ${err.message}`, 4000);
      }
      renderAttachRow();
      updateSendButton();
    }
  }
  $('#file-input').addEventListener('change', (e) => { addFiles([...e.target.files]); e.target.value = ''; });
  input.addEventListener('paste', (e) => {
    const files = [...((e.clipboardData && e.clipboardData.files) || [])];
    if (!files.length) return;
    e.preventDefault();
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
    addFiles(files.map((f) => (/^image\.\w+$/i.test(f.name) ? new File([f], `screenshot-${stamp}.${f.name.split('.').pop()}`, { type: f.type }) : f)));
  });

  let dragDepth = 0;
  const dragHasFiles = (e) => [...((e.dataTransfer && e.dataTransfer.types) || [])].includes('Files');
  window.addEventListener('dragenter', (e) => { if (!dragHasFiles(e)) return; e.preventDefault(); dragDepth++; $('#drop-overlay').classList.remove('hidden'); });
  window.addEventListener('dragover', (e) => { if (dragHasFiles(e)) e.preventDefault(); });
  window.addEventListener('dragleave', (e) => {
    if (!dragHasFiles(e)) return;
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) $('#drop-overlay').classList.add('hidden');
  });
  window.addEventListener('drop', (e) => {
    if (!dragHasFiles(e)) return;
    e.preventDefault();
    dragDepth = 0;
    $('#drop-overlay').classList.add('hidden');
    addFiles([...e.dataTransfer.files]);
  });

  function openPlusMenu() {
    const menu = $('#plus-menu');
    const row = (key, ic, label, on, sub) =>
      `<button class="menu-item" role="menuitemcheckbox" aria-checked="${on}" data-toggle="${key}">${icon(ic, 16)}<span class="mi-label">${label}${sub ? `<small>${sub}</small>` : ''}</span>` +
      `<span class="mswitch${on ? ' on' : ''}" aria-hidden="true"></span></button>`;
    menu.innerHTML =
      `<button class="menu-item" data-attach>${icon('paperclip', 16)} Attach files</button><div class="menu-sep"></div>` +
      row('use_web', 'globe', 'Web search', !!S.settings.use_web) +
      row('use_research', 'compass', 'Deep research', !!S.settings.use_research, 'Plan, search, read, report — next message only') +
      row('thinking', 'brain', 'Thinking', S.settings.thinking !== false) +
      (S.servers.length ? '<div class="menu-sep"></div>' : '') +
      S.servers
        .map((sv) => {
          const sub = sv.status === 'error' ? 'Not connected' : sv.status === 'connecting' ? 'Connecting…' : sv.status === 'ready' ? `${sv.tools.length} tools` : '';
          return `<button class="menu-item" role="menuitemcheckbox" aria-checked="${sv.enabled}" data-server="${esc(sv.name)}">${icon('plug', 16)}<span class="mi-label">${esc(sv.name)}${sub ? `<small>${sub}</small>` : ''}</span><span class="mswitch${sv.enabled ? ' on' : ''}" aria-hidden="true"></span></button>`;
        })
        .join('');
    menu.classList.remove('hidden');
    const r = $('#btn-plus').getBoundingClientRect();
    menu.style.left = Math.max(8, Math.min(r.left, innerWidth - menu.offsetWidth - 8)) + 'px';
    menu.style.top = Math.max(8, r.top - menu.offsetHeight - 8) + 'px';
  }
  /* ---- dictation: record in the browser, transcribe locally, put the text in the message box (still editable) ---- */
  const mic = { rec: null, chunks: [], stream: null, timer: null };
  $('#btn-mic').innerHTML = icon('mic', 16);
  async function stopMic(send) {
    if (!mic.rec) return;
    const rec = mic.rec;
    mic.rec = null;
    clearTimeout(mic.timer);
    $('#btn-mic').classList.remove('recording');
    $('#btn-mic').setAttribute('aria-pressed', 'false');
    if (!send) { rec.onstop = () => mic.stream && mic.stream.getTracks().forEach((t) => t.stop()); rec.stop(); return; }
    rec.onstop = async () => {
      mic.stream.getTracks().forEach((t) => t.stop());
      const blob = new Blob(mic.chunks, { type: rec.mimeType || 'audio/webm' });
      if (blob.size < 1500) { toast('That recording was empty.'); return; }
      toast('Transcribing…');
      $('#btn-mic').disabled = true;
      try {
        const fd = new FormData();
        fd.append('file', blob, 'dictation.webm');
        const res = await fetch('/api/transcribe', { method: 'POST', body: fd });
        const d = await res.json();
        if (!res.ok) throw new Error(d.detail || 'Transcription failed');
        const box = $('#input');
        if (d.text) {
          box.value = (box.value ? box.value.replace(/\s*$/, ' ') : '') + d.text;
          box.dispatchEvent(new Event('input', { bubbles: true }));
          box.focus();
        }
        toast(d.text ? `Transcribed (${d.language}, ${d.device}, ${(d.ms / 1000).toFixed(1)}s) — edit it, then send.` : 'No speech was detected.');
      } catch (err) { toast(err.message); }
      $('#btn-mic').disabled = false;
    };
    rec.stop();
  }
  $('#btn-mic').onclick = async () => {
    if (mic.rec) { stopMic(true); return; }
    if (!navigator.mediaDevices || !window.MediaRecorder) { toast('This browser can’t record audio.'); return; }
    try {
      mic.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (_) { toast('Microphone access was blocked.'); return; }
    mic.chunks = [];
    mic.rec = new MediaRecorder(mic.stream);
    mic.rec.ondataavailable = (e) => { if (e.data.size) mic.chunks.push(e.data); };
    mic.rec.start();
    $('#btn-mic').classList.add('recording');
    $('#btn-mic').setAttribute('aria-pressed', 'true');
    toast('Recording… click the microphone again to stop.');
    mic.timer = setTimeout(() => stopMic(true), 5 * 60 * 1000);
  };
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && mic.rec) stopMic(false); });
  $('#btn-plus').innerHTML = icon('plus', 16);
  $('#btn-plus').onclick = (e) => {
    e.stopPropagation();
    const m = $('#plus-menu');
    if (!m.classList.contains('hidden')) { m.classList.add('hidden'); return; }
    closeMenus();
    openPlusMenu();
  };
  $('#plus-menu').addEventListener('click', (e) => {
    if (e.target.closest('[data-attach]')) { closeMenus(); $('#file-input').click(); return; }
    const sv = e.target.closest('[data-server]');
    if (sv) {
      const cur = S.servers.find((x) => x.name === sv.dataset.server);
      API.post(`/api/connections/servers/${encodeURIComponent(sv.dataset.server)}/enabled`, { enabled: !(cur && cur.enabled) })
        .then((d) => { S.servers = d.servers; openPlusMenu(); })
        .catch((err) => toast(err.message));
      return;
    }
    const t = e.target.closest('[data-toggle]');
    if (!t) return;
    toggleSetting(t.dataset.toggle, t.dataset.toggle === 'thinking');
    openPlusMenu();
  });

  function attachmentsHTML(atts) {
    if (!atts || !atts.length) return '';
    return (
      '<div class="msg-atts">' +
      atts
        .map((a) =>
          a.kind === 'image'
            ? `<a href="/api/files/${esc(a.id)}" target="_blank" rel="noopener"><img class="att-img" src="/api/files/${esc(a.id)}" alt="${esc(a.name)}"></a>`
            : `<a class="att-chip" href="/api/files/${esc(a.id)}" download title="${esc(a.name)}"><span class="ext">${KIND_LABEL[a.kind] || 'FILE'}</span><span class="att-name">${esc(a.name)}</span></a>`
        )
        .join('') +
      '</div>'
    );
  }

  /* =====================================================================
     Tool activity blocks + sources
     ===================================================================== */
  const hostOf = (u) => { try { return new URL(u).hostname.replace(/^www\./, ''); } catch (_) { return u || ''; } };
  const safeUrl = (u) => (/^https?:\/\//i.test(u || '') ? u : '#');
  const faviconOf = (u) => { try { return new URL(u).origin + '/favicon.ico'; } catch (_) { return ''; } };
  const isRunning = (t) => t.status === 'running' || t.status === 'confirm';
  const TOOL_ICON = { web_search: 'search', fetch_page: 'globe', read_file: 'file', create_chart: 'chart', create_dashboard: 'chart', create_map: 'compass', skill: 'compass', entity_card: 'search', research_plan: 'compass', build_n8n_workflow: 'plug', convert_document: 'file', save_memory: 'book', run_python: 'code', current_datetime: 'clock', search_chat: 'search' };
  /** Charts that were drawn successfully are shown as charts, not as tool rows. */
  const isDrawnChart = (t) => (t.name === 'create_chart' || t.name === 'create_dashboard') && t.ok !== false && !isRunning(t) && !!(t.detail && t.detail.spec);
  /** Maps are drawn by MapView, not listed as tool rows. */
  /** A skill's card (questions or result) is drawn by SkillView, not listed as a tool row. */
  const isSkill = (t) => t.name === 'skill' && !isRunning(t) && !!(t.detail && t.detail.status);
  const isMap = (t) => t.name === 'create_map' && t.ok !== false && !isRunning(t) && !!(t.detail && t.detail.map);

  /** Entity cards are drawn as cards (like charts), not as tool rows. */
  const isEntityCard = (t) => t.name === 'entity_card' && t.ok !== false && !isRunning(t) && !!(t.detail && (t.detail.card || t.detail.compare));

  function toolTitle(t) {
    const a = t.args || {};
    const run = isRunning(t);
    switch (t.name) {
      case 'web_search': return run ? `Searching the web: ${a.query || ''}…` : `Searched the web: ${a.query || ''}`;
      case 'fetch_page': return run ? `Reading page: ${hostOf(a.url)}…` : t.ok === false ? `Couldn’t read ${hostOf(a.url)}` : `Read page: ${hostOf(a.url)}`;
      case 'read_file': {
        const n = (t.detail && t.detail.name) || 'file';
        return run ? 'Reading attached file…' : t.ok === false ? `Couldn’t read ${n}` : `Read file: ${n}`;
      }
      case 'run_python':
        if (t.status === 'confirm') return 'Waiting for your OK to run code';
        return run ? 'Running code…' : t.declined ? 'Code run declined' : t.ok === false ? 'Code failed' : 'Ran code';
      case 'convert_document': return run ? 'Converting document…' : t.ok === false ? 'Couldn’t convert the document' : `Converted ${(t.detail && t.detail.from) || 'document'}`;
      case 'build_n8n_workflow': return t.declined ? 'n8n workflow not created' : run ? 'Creating n8n workflow…' : t.ok === false ? 'Couldn’t create the n8n workflow' : 'Created a draft n8n workflow';
      case 'entity_card': return run ? `Looking up ${(a.query || '').split(' | ').join(' and ')}…` : t.ok === false ? 'No card found' : `Found: ${t.summary || a.query || ''}`;
      case 'create_dashboard': return run ? 'Building dashboard…' : t.ok === false ? 'Couldn’t build the dashboard' : 'Built a dashboard';
      case 'research_plan': return run ? 'Planning the research…' : `Research plan: ${((t.detail && t.detail.questions) || []).length} sub-questions`;
      case 'save_memory': return run ? 'Saving to memory…' : t.ok === false ? 'Couldn’t save to memory' : 'Saved to memory';
      case 'skill': return run ? `Working on ${(t.args && t.args.title) || 'your request'}…` : (t.summary || 'Skill');
      case 'create_map': return run ? 'Locating places…' : t.ok === false ? 'Couldn’t draw the map' : 'Drew a map';
      case 'create_chart': return run ? 'Drawing chart…' : t.ok === false ? 'Couldn’t draw the chart' : 'Drew a chart';
      case 'current_datetime': return 'Checked the date and time';
      case 'search_chat': return run ? 'Searching earlier messages…' : 'Looked through earlier messages';
      default:
        if (t.name.startsWith('mcp__')) {
          const [, server, tool] = t.name.match(/^mcp__([^_]+(?:_[^_]+)*)__(.+)$/) || [];
          const sv = (t.detail && t.detail.server) || server || 'tool';
          const tl = (t.detail && t.detail.tool) || tool || t.name;
          if (t.status === 'confirm') return `Waiting for your OK: ${tl}`;
          return t.declined ? `Declined: ${tl}` : run ? `Using ${tl} (${sv})…` : t.ok === false ? `${tl} failed (${sv})` : `Used ${tl} (${sv})`;
        }
        return run ? `Running ${t.name}…` : `Used ${t.name}`;
    }
  }

  function toolDetailHTML(t) {
    const d = t.detail || {};
    const a = t.args || {};
    const pre = (cls, text) => `<pre class="${cls}">${esc(text)}</pre>`;
    if (isRunning(t) && t.name !== 'run_python') return '<div class="muted small">Working…</div>';
    switch (t.name) {
      case 'web_search':
        return (
          '<ol class="res">' +
          ((d.results || [])
            .map((r) => `<li><a href="${esc(safeUrl(r.url))}" target="_blank" rel="noopener noreferrer">${esc(r.title || r.url)}</a><div class="muted small">${esc(hostOf(r.url))}${r.n ? ` · source [${r.n}]` : ''}</div>${r.snippet ? `<div class="snip">${esc(r.snippet)}</div>` : ''}</li>`)
            .join('') || '<li class="muted">No results</li>') +
          '</ol>'
        );
      case 'fetch_page':
        return t.ok === false
          ? `<div class="muted small">${esc(t.summary || 'Failed')}</div>`
          : `<div class="d-line"><a href="${esc(safeUrl(d.url))}" target="_blank" rel="noopener noreferrer">${esc(d.title || d.url || '')}</a> <span class="muted small">· ${esc(hostOf(d.url))} · ${(d.chars || 0).toLocaleString()} characters${d.n ? ` · source [${d.n}]` : ''} · ${d.page_date ? `page date: ${esc(d.page_date)}` : 'no date on page'}</span></div><div class="snip">${esc(d.excerpt || '')}…</div>`;
      case 'read_file':
        return t.ok === false ? `<div class="muted small">${esc(t.summary || 'Failed')}</div>` : `<div class="d-line"><b>${esc(d.name || '')}</b> <span class="muted small">· ${(d.chars || 0).toLocaleString()} characters</span></div><div class="snip">${esc(d.excerpt || '')}…</div>`;
      case 'run_python':
        return (
          `<div class="d-label">Code</div>${pre('tool-code', a.code || '')}` +
          (d.stdout && d.stdout.trim() ? `<div class="d-label">Output</div>${pre('tool-out', d.stdout)}` : '') +
          (d.stderr && d.stderr.trim() ? `<div class="d-label err">Errors</div>${pre('tool-out err', d.stderr)}` : '') +
          (t.declined ? '<div class="muted small">You declined to run this code.</div>' : '')
        );
      case 'create_chart':
      case 'create_dashboard':
        return `<div class="muted small">${esc(t.summary || 'Failed')}</div>`;
      case 'save_memory':
        return `<div class="muted small">${esc(d.text || t.summary || '')}</div>`;
      case 'convert_document':
        return `<div class="muted small">${esc(t.summary || '')}</div>`;
      case 'build_n8n_workflow':
        return t.ok === false
          ? `<div class="muted small">${esc(t.summary || 'Failed')}</div>`
          : `<div class="d-line"><a href="${esc(safeUrl(d.url))}" target="_blank" rel="noopener noreferrer">Open the draft in n8n</a></div><div class="muted small">Attach your accounts there, check each node and switch it on. Then add the MCP server in Settings → Connections (expected URL: ${esc(d.mcp_url || '')}).</div>`;
      case 'research_plan':
        return '<ol class="res">' + ((d.questions || []).map((x) => `<li><b>${esc(x.q)}</b><div class="muted small">searches: ${esc((x.queries || []).join(' · '))}</div></li>`).join('')) + '</ol>';
      case 'search_chat':
        return (
          ((d.urls || []).length ? `<div class="d-label">Links found</div><ol class="res">${d.urls.map((u) => `<li><a href="${esc(safeUrl(u))}" target="_blank" rel="noopener noreferrer">${esc(u)}</a></li>`).join('')}</ol>` : '') +
          (d.hits || []).map((h) => `<div class="d-item"><div class="muted small">message ${h.index} (${esc(h.role)})</div><div class="snip">${esc(h.snippet)}</div></div>`).join('') || '<div class="muted small">Nothing found.</div>'
        );
      default:
        if (t.name.startsWith('mcp__')) {
          return (Object.keys(a).length ? `<div class="d-label">Arguments</div>${pre('tool-code', JSON.stringify(a, null, 2))}` : '') +
            (d.text ? `<div class="d-label">Result</div>${pre('tool-out', d.text)}` : `<div class="muted small">${esc(t.summary || '')}</div>`);
        }
        return `<div class="muted small">${esc(d.text || t.summary || '')}</div>`;
    }
  }

  function fileLinksHTML(files) {
    if (!files || !files.length) return '';
    const imgs = files.filter((f) => /^image\/(png|jpeg|gif|webp)$/.test(f.mime));
    const rest = files.filter((f) => !imgs.includes(f));
    return (
      imgs.map((f) => `<a href="${esc(encodeURI(f.url))}" target="_blank" rel="noopener"><img class="gen-img" src="${esc(encodeURI(f.url))}" alt="${esc(f.name)}"></a>`).join('') +
      (rest.length ? `<div class="file-links">${rest.map((f) => `<a class="att-chip" href="${esc(encodeURI(f.url))}" download><span class="ext">FILE</span><span class="att-name">${esc(f.name)}</span></a>`).join('')}</div>` : '')
    );
  }

  /** Consecutive fetch_page calls are shown as one "Read N pages" block. */
  function groupTools(list) {
    const groups = [];
    for (const t of list) {
      const last = groups[groups.length - 1];
      if (t.name === 'fetch_page' && last && last.items[0].name === 'fetch_page') last.items.push(t);
      else groups.push({ items: [t] });
    }
    return groups;
  }

  const STATE_LABEL = { completed: 'Completed', partial: 'Completed with errors', failed: 'Failed', unexecuted: 'Not executed' };
  function execSummaryHTML(v) {
    const list = (v.tools || []).filter((t) => !isDrawnChart(t) && !isEntityCard(t) && !isMap(t) && !isSkill(t));
    if (list.length < 2 && !['failed', 'partial', 'unexecuted'].includes(v.taskState)) return '';
    const fin = list.filter((t) => !isRunning(t));
    const bad = fin.filter((t) => t.ok === false).length;
    const run = list.length - fin.length;
    const bits = [];
    if (run) bits.push(`${run} running`);
    if (fin.length - bad) bits.push(`${fin.length - bad} succeeded`);
    if (bad) bits.push(`${bad} failed`);
    const label = STATE_LABEL[v.taskState];
    return `<div class="exec-summary ${v.taskState || (run ? 'running' : '')}">${label ? `<span class="state-badge">${label}</span>` : ''}<span>${bits.join(' · ') || 'No actions were run'}</span></div>`;
  }

  /** Charts the model asked for are drawn here by the app (once per chart, never rebuilt by tool updates). */
  function renderCharts(v) {
    const box = $('.charts', v.el);
    for (const t of v.tools || []) {
      if (isEntityCard(t) && !box.querySelector(`[data-tid="${CSS.escape(String(t.id))}"]`)) {
        const holder = document.createElement('div');
        holder.dataset.tid = String(t.id);
        box.insertBefore(holder, box.firstChild); // the card goes above the written answer and any charts
        if (typeof EntityCard !== 'undefined') EntityCard.render(holder, t.detail);
        continue;
      }
      if (isSkill(t) && !box.querySelector(`[data-tid="${CSS.escape(String(t.id))}"]`)) {
        const holder = document.createElement('div');
        holder.dataset.tid = String(t.id);
        box.insertBefore(holder, box.firstChild); // the card sits above the written reply
        if (typeof SkillView !== 'undefined') SkillView.render(holder, t.detail, { chatId: S.current && S.current.id });
        continue;
      }
      if (isMap(t) && !box.querySelector(`[data-tid="${CSS.escape(String(t.id))}"]`)) {
        const holder = document.createElement('div');
        holder.dataset.tid = String(t.id);
        box.appendChild(holder);
        if (typeof MapView !== 'undefined') MapView.render(holder, t.detail.map);
        continue;
      }
      if (!isDrawnChart(t) || box.querySelector(`[data-tid="${CSS.escape(String(t.id))}"]`)) continue;
      const block = document.createElement('div');
      block.dataset.tid = String(t.id);
      const isDash = t.detail.spec && t.detail.spec.kind === 'dashboard';
      block.className = isDash ? 'dash-block' : 'viz-block';
      box.appendChild(block);
      if (typeof Viz !== 'undefined') {
        if (isDash) Viz.renderDashboard(block, t.detail.spec);
        else Viz.renderSpec(block, t.detail.spec);
      }
    }
  }

  function renderTools(v) {
    renderCharts(v);
    const box = $('.tools', v.el);
    const list = (v.tools || []).filter((t) => !isDrawnChart(t) && !isEntityCard(t) && !isMap(t) && !isSkill(t));
    if (!list.length && !v.taskState) { box.innerHTML = ''; return; }
    box.innerHTML = execSummaryHTML(v) + groupTools(list)
      .map((g) => {
        const items = g.items;
        const key = items[0].id;
        const running = items.some(isRunning);
        const failed = !running && items.every((t) => t.ok === false);
        const state = running ? 'running' : failed ? 'error' : 'done';
        let title;
        if (items.length > 1) {
          const okN = items.filter((t) => t.ok !== false).length;
          title = running ? `Reading ${items.length} pages…` : `Read ${okN} page${okN === 1 ? '' : 's'}`;
        } else title = toolTitle(items[0]);
        const detail = items.map((t) => (items.length > 1 ? `<div class="d-item">${toolDetailHTML(t)}</div>` : toolDetailHTML(t))).join('');
        const confirm = items
          .filter((t) => t.status === 'confirm')
          .map(
            (t) =>
              `<div class="confirm-card"><div><b>${esc((t.confirm && t.confirm.title) || 'Run this Python code on your computer?')}</b></div>` +
              `<pre class="tool-code">${esc((t.confirm && t.confirm.text) || (t.args && t.args.code) || '')}</pre>` +
              `<div class="confirm-row"><button class="btn primary" data-act="allow-code" data-tid="${esc(t.id)}">${t.confirm && t.confirm.kind === 'tool' ? 'Allow' : 'Run it'}</button>` +
              (t.confirm && t.confirm.always ? `<button class="btn" data-act="always-tool" data-tid="${esc(t.id)}">Always allow this tool</button>` : '') +
              `<button class="btn" data-act="deny-code" data-tid="${esc(t.id)}">${t.confirm && t.confirm.kind === 'tool' ? 'Don’t allow' : 'Don’t run'}</button>` +
              (t.confirm && t.confirm.kind === 'tool' ? '' : `<span class="muted small">No network access · runs in this chat’s own folder</span>`) + `</div></div>`
          )
          .join('');
        const stateIcon = running ? '<span class="spin"></span>' : failed ? `<span class="tool-bad">${icon('x', 14)}</span>` : `<span class="tool-ok">${icon('check', 14)}</span>`;
        return (
          `<div class="tool-block ${state}"><details data-key="${esc(key)}"${v.open.has(key) ? ' open' : ''}>` +
          `<summary><span class="tool-ic">${icon(TOOL_ICON[items[0].name] || 'code', 15)}</span><span class="tool-title">${esc(title)}</span><span class="tool-state">${stateIcon}</span></summary>` +
          `<div class="tool-detail">${detail}</div></details>${confirm}${items.map((t) => fileLinksHTML(t.files)).join('')}</div>`
        );
      })
      .join('');
  }

  function verifyHtml(vf) {
    if (!vf || !vf.checked) return '';
    const bad = vf.unsupported || [];
    const ok = vf.checked - bad.length;
    const head = bad.length
      ? `${vf.fixed ? '✎ Corrected' : '⚠'} ${bad.length} of ${vf.checked} checked statements ${vf.fixed ? 'were not found in the sources and were removed or softened' : 'are not confirmed by the sources'}`
      : `✓ ${ok} of ${vf.checked} checked statements found in the sources`;
    return `<details class="verify ${bad.length ? 'warn' : 'good'}"><summary>${head}</summary>` +
      (bad.length ? '<ul>' + bad.map((b) => `<li>${esc(b.text)}${b.missing && b.missing.length ? ` <span class="muted">(not in sources: ${esc(b.missing.join(', '))})</span>` : ''}</li>`).join('') + '</ul>' : '') +
      '<p class="muted small">Checked by the app against the text the tools returned: numbers, names and dates. It cannot prove a source itself is right.</p></details>';
  }

  function renderSources(v) {
    const box = $('.sources', v.el);
    const src = v.sources || [];
    box.classList.toggle('hidden', !src.length);
    if (!src.length) { box.innerHTML = ''; return; }
    box.innerHTML =
      '<div class="sources-title">Sources</div><div class="source-list">' +
      src
        .map(
          (s) =>
            `<a class="source" href="${esc(safeUrl(s.url))}" target="_blank" rel="noopener noreferrer" title="${esc(s.title)}">` +
            `<span class="num">${s.n}</span><img class="fav" src="${esc(faviconOf(s.url))}" alt="" loading="lazy" referrerpolicy="no-referrer">` +
            `<span class="src-text"><span class="src-domain">${esc(s.domain || hostOf(s.url))}</span><span class="src-title">${esc(s.title)}</span></span></a>`
        )
        .join('') +
      '</div>' + verifyHtml(v.verification);
    $$('img.fav', box).forEach((img) => img.addEventListener('error', () => { img.style.display = 'none'; }));
  }

  async function answerConfirm(msg, tid, approve, always) {
    const v = msg._view;
    const t = v && v.tools.find((x) => x.id === tid);
    if (!t || !t.cid) return;
    try {
      await API.post(`/api/confirm/${t.cid}`, { approve, always: !!always });
      t.status = 'running';
      renderTools(v);
    } catch (e) { toast('That request already expired'); }
  }

  /* =====================================================================
     Thread rendering
     ===================================================================== */
  const scroller = $('#scroller');
  const nearBottom = () => scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 90;
  scroller.addEventListener('scroll', () => {
    S.stick = nearBottom();
    $('#scroll-bottom').classList.toggle('hidden', S.stick);
  });
  function scrollToBottom(force) {
    if (force || S.stick) scroller.scrollTop = scroller.scrollHeight;
  }
  $('#scroll-bottom').innerHTML = icon('arrowDown');
  $('#scroll-bottom').onclick = () => { S.stick = true; scroller.scrollTo({ top: scroller.scrollHeight, behavior: 'smooth' }); };

  function navHTML(m) {
    const n = m.siblings ? m.siblings.length : 1;
    if (n < 2) return '';
    const i = m.sib_index || 0;
    return (
      `<span class="branch-nav"><button class="nav-btn" data-act="prev" aria-label="Previous version"${i === 0 ? ' disabled' : ''}>‹</button>` +
      `<span>${i + 1} / ${n}</span>` +
      `<button class="nav-btn" data-act="next" aria-label="Next version"${i === n - 1 ? ' disabled' : ''}>›</button></span>`
    );
  }

  function actionsHTML(m) {
    const when = m.created_at ? fmtTime(m.created_at) : '';
    const tip = m.tokens_out ? `${m.tokens_in || 0} tokens in · ${m.tokens_out} tokens out` : '';
    const time = `<span class="time" title="${esc(tip)}">${esc(when)}${m.role === 'assistant' && m.model ? ' · ' + esc(m.model) : ''}</span>`;
    const b = (act, ic, title, on) =>
      `<button class="icon-btn${on ? ' on' : ''}" data-act="${act}" title="${title}" aria-label="${title}">${icon(ic, 16)}</button>`;
    if (m.role === 'user') {
      return `<div class="msg-actions">${time}${navHTML(m)}${b('edit', 'edit', 'Edit and resend')}${b('copy', 'copy', 'Copy')}</div>`;
    }
    const cut = m.last && (m.status === 'stopped' || m.status === 'length');
    return (
      `<div class="msg-actions">${navHTML(m)}${b('copy', 'copy', 'Copy')}${b('regen', 'refresh', 'Regenerate')}` +
      `<button class="icon-btn mini" data-act="regen-model" title="Regenerate with another model" aria-label="Regenerate with another model">${icon('chevron', 12)}</button>` +
      `${b('up', 'thumbUp', 'Good response', m.feedback === 'up')}${b('down', 'thumbDown', 'Bad response', m.feedback === 'down')}` +
      `${b('tts', 'speaker', 'Read aloud')}` +
      (cut ? `<button class="btn-mini" data-act="continue">Continue</button>` : '') +
      `${time}</div>`
    );
  }

  function updateCutNote(el) {
    const note = $('.cut-note', el);
    if (!note) return;
    const st = el._m.status;
    note.textContent = st === 'stopped' ? 'Response stopped.' : st === 'length' ? 'Response cut off — the output limit was reached.' : '';
    note.classList.toggle('hidden', !(st === 'stopped' || st === 'length'));
  }

  /** Rebuild a message's action row (and cut-off note) from its metadata. */
  function refreshActions(el) {
    const tmp = document.createElement('div');
    tmp.innerHTML = actionsHTML(el._m);
    $('.msg-actions', el).replaceWith(tmp.firstElementChild);
    updateCutNote(el);
  }

  function addUserMsg(m) {
    const d = document.createElement('div');
    d.className = 'msg user';
    d._m = { ...m, role: 'user' };
    const atts = m.attachments || [];
    const hideBubble = atts.length && m.content === '(see attached files)';
    d.innerHTML = attachmentsHTML(atts) + `<div class="bubble${hideBubble ? ' hidden' : ''}"></div>${actionsHTML(d._m)}`;
    $('.bubble', d).textContent = m.content;
    d._raw = m.content;
    if (m.id) d.dataset.id = m.id;
    $('#thread').appendChild(d);
    return d;
  }

  /** An assistant message "view": the DOM node plus streaming bookkeeping. */
  function addAssistantMsg(m) {
    const d = document.createElement('div');
    d.className = 'msg assistant';
    d._m = m ? { ...m, role: 'assistant' } : { role: 'assistant', created_at: Date.now() / 1000, status: 'streaming', model: S.model };
    d.innerHTML =
      `<div class="think-block hidden"><details><summary><span class="think-label">Thinking…</span></summary><div class="think-body md"></div></details></div>` +
      `<div class="progress hidden"></div><div class="charts"></div><div class="tools"></div><div class="md answer"></div><div class="sources hidden"></div><div class="msg-error hidden"></div><div class="cut-note hidden"></div>` + actionsHTML(d._m);
    if (m && m.id) d.dataset.id = m.id;
    $('#thread').appendChild(d);
    const v = { el: d, text: (m && m.content) || '', streaming: false, thinkStart: 0, thinkEnd: 0, thinkMs: (m && m.think_ms) || 0, lastPaint: 0, raf: 0, id: m && m.id };
    d._view = v;
    v.tools = ((m && m.meta && m.meta.tools) || []).map((x) => ({ ...x }));
    v.sources = (m && m.meta && m.meta.sources) || [];
    v.taskState = (m && m.meta && m.meta.state) || null;
    v.verification = (m && m.meta && m.meta.verification) || null;
    v.open = new Set();
    $('.tools', d).addEventListener('toggle', (e) => {
      const det = e.target;
      if (det.tagName === 'DETAILS') { if (det.open) v.open.add(det.dataset.key); else v.open.delete(det.dataset.key); }
    }, true);
    renderTools(v);
    renderSources(v);
    updateCutNote(d);
    return v;
  }

  const RESEARCH_STAGES = ['Research planning', 'Source discovery', 'Investigation', 'Synthesis', 'Final report'];
  function renderProgress(v, ev) {
    const box = $('.progress', v.el);
    if (!ev) { box.classList.add('hidden'); box.innerHTML = ''; return; }
    box.innerHTML =
      '<div class="prog-steps">' +
      RESEARCH_STAGES.map((l, i) => `<span class="prog-step ${i + 1 < ev.stage ? 'done' : i + 1 === ev.stage ? 'on' : ''}">${i + 1}. ${l}</span>`).join('') +
      `</div><div class="prog-detail">${esc(ev.detail || '')}</div>`;
    box.classList.remove('hidden');
  }

  function memoryToast(ev) {
    const t = (ev.text || '').slice(0, 80);
    const msg = {
      saved: `Saved to memory: “${t}”`,
      duplicate: 'Already in memory',
      secret: 'Not saved — memory never stores passwords or keys',
      full: 'Memory is full — delete some entries in Library',
      empty: 'Nothing to remember',
      forgotten: `Forgot ${ev.count} memory item${ev.count === 1 ? '' : 's'}`,
      not_found: 'I couldn’t find a matching memory to forget',
    }[ev.status];
    if (msg) toast(msg, 3500);
  }

  function thinkLabel(v, open) {
    const secs = v.thinkMs ? v.thinkMs / 1000 : ((v.thinkEnd || performance.now()) - v.thinkStart) / 1000;
    const s = Math.max(1, Math.round(secs));
    return open ? `Thinking… ${s}s` : `Thought for ${s}s`;
  }

  function paint(v) {
    v.lastPaint = performance.now();
    const parts = Render.splitThinking(v.text);
    const tb = $('.think-block', v.el);
    if (parts.thinking !== null) {
      const details = $('details', tb);
      const firstTime = tb.classList.contains('hidden');
      tb.classList.remove('hidden');
      if (v.streaming && !v.thinkStart) v.thinkStart = performance.now();
      if (!parts.open && v.thinkStart && !v.thinkEnd) { v.thinkEnd = performance.now(); details.open = false; }
      if (firstTime && v.streaming && parts.open) details.open = true;
      tb.classList.toggle('live', v.streaming && parts.open);
      $('.think-label', tb).textContent = v.thinkStart || v.thinkMs ? thinkLabel(v, parts.open && v.streaming) : 'Thought process';
      $('.think-body', tb).innerHTML = Render.markdown(parts.thinking);
    } else {
      tb.classList.add('hidden');
    }
    const ans = $('.answer', v.el);
    ans.innerHTML = parts.answer ? Render.markdown(parts.answer, { sources: v.sources }) : '';
    v.el.classList.toggle('streaming', v.streaming);
    v.el._raw = parts.answer || (parts.thinking === null ? v.text : '');
    scrollToBottom();
  }

  function schedulePaint(v) {
    if (v.raf) return;
    const wait = Math.max(0, 50 - (performance.now() - v.lastPaint));
    v.raf = setTimeout(() => requestAnimationFrame(() => { v.raf = 0; paint(v); }), wait);
  }

  function showError(v, ev) {
    const box = $('.msg-error', v.el);
    box.innerHTML =
      `<div class="grow"><strong>${esc(ev.message || 'Something went wrong.')}</strong>` +
      (ev.hint ? `<p>${inlineCode(ev.hint)}</p>` : '') +
      `</div>` + (ev.retry_ok ? `<button class="btn" data-act="retry">Retry</button>` : '');
    box.classList.remove('hidden');
    if (['unreachable', 'model_missing'].includes(ev.code)) loadModels();
  }

  function renderThread(chat) {
    $('#thread').innerHTML = '';
    const msgs = chat.messages;
    msgs.forEach((m, i) => {
      const meta = { ...m, last: i === msgs.length - 1 };
      if (m.role === 'user') {
        addUserMsg(meta);
      } else {
        const v = addAssistantMsg(meta);
        paint(v);
        Render.renderBlocks(v.el);
        if (m.status === 'error') showError(v, { message: 'This response ended with an error.', retry_ok: false });
      }
    });
    const last = msgs[msgs.length - 1];
    if (last && last.role === 'user') {
      const v = addAssistantMsg(null);
      showError(v, { message: 'No response was generated for this message.', hint: '', retry_ok: true });
    }
  }

  const findMsgEl = (id) => $(`#thread .msg[data-id="${id}"]`);
  const domMessages = () => $$('#thread .msg').map((el) => ({ role: el._m.role, content: el._raw || '' }));
  function busy() {
    if (S.streaming) { toast('Wait for the current response to finish'); return true; }
    return false;
  }

  /** Re-fetch the selected branch from the server and redraw (picks up sibling info and ids). */
  async function reloadChat(id) {
    if (!S.current || S.current.id !== id || S.streaming) return;
    try {
      const chat = await API.get(`/api/chats/${id}`);
      if (!S.current || S.current.id !== id || S.streaming) return;
      S.current = { ...S.current, ...chat };
      S.messages = chat.messages.map((m) => ({ role: m.role, content: m.content }));
      renderThread(chat);
      scrollToBottom();
      Render.renderBlocks($('#thread')).then(() => scrollToBottom());
      updateCounter();
    } catch (_) { /* keep what is on screen */ }
  }

  /** Switch to another version (sibling) of a message; the thread below follows that branch. */
  async function switchBranch(messageId) {
    if (busy() || !S.current || !messageId) return;
    stopSpeech();
    try {
      const chat = await API.put(`/api/chats/${S.current.id}/leaf`, { message_id: messageId });
      S.current = { ...S.current, ...chat };
      S.messages = chat.messages.map((m) => ({ role: m.role, content: m.content }));
      renderThread(chat);
      const el = findMsgEl(messageId);
      if (el) el.scrollIntoView({ block: 'nearest' });
      Render.renderBlocks($('#thread'));
      updateCounter();
    } catch (e) { toast(e.message); }
  }

  /* ---- edit & resend ---- */
  function startEdit(msg) {
    if (msg.classList.contains('editing') || !msg._m.id) return;
    msg.classList.add('editing');
    const box = document.createElement('div');
    box.className = 'edit-box';
    box.innerHTML =
      '<textarea rows="1" aria-label="Edit message"></textarea>' +
      '<div class="edit-actions"><button class="btn" data-x="cancel">Cancel</button><button class="btn primary" data-x="save">Save &amp; send</button></div>';
    const ta = $('textarea', box);
    ta.value = msg._raw;
    msg.insertBefore(box, $('.msg-actions', msg));
    const fit = () => { ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, 300) + 'px'; };
    fit();
    ta.focus();
    ta.setSelectionRange(ta.value.length, ta.value.length);
    const close = () => { box.remove(); msg.classList.remove('editing'); };
    const save = () => {
      const text = ta.value.trim();
      if (!text) return;
      if (text === msg._raw) { close(); return; }
      close();
      send(text, { editId: msg._m.id });
    };
    ta.addEventListener('input', fit);
    ta.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { e.preventDefault(); close(); }
      else if (e.key === 'Enter' && !e.isComposing) {
        const sendOnEnter = S.settings.send_on_enter !== false;
        if (sendOnEnter ? !e.shiftKey : e.ctrlKey || e.metaKey) { e.preventDefault(); save(); }
      }
    });
    box.addEventListener('click', (e) => {
      if (e.target.dataset.x === 'cancel') close();
      if (e.target.dataset.x === 'save') save();
    });
  }

  /* ---- model chooser for "regenerate with…" ---- */
  function pickModelFor(anchor, cb) {
    const menu = $('#ctx-menu');
    closeMenus();
    menu.innerHTML =
      `<div class="menu-note">Regenerate with…</div>` +
      (S.models.length
        ? S.models.map((m) => `<button class="menu-item" data-model="${esc(m.name)}">${esc(m.name)}<span class="meta">${esc(m.params || '')}</span></button>`).join('')
        : '<div class="menu-note">No models available.</div>');
    menu.classList.remove('hidden');
    const r = anchor.getBoundingClientRect();
    menu.style.left = Math.max(8, Math.min(r.left, innerWidth - menu.offsetWidth - 8)) + 'px';
    const below = r.bottom + 4;
    menu.style.top = (below + menu.offsetHeight > innerHeight - 8 ? Math.max(8, r.top - menu.offsetHeight - 4) : below) + 'px';
    menu.onclick = (e) => {
      const b = e.target.closest('[data-model]');
      if (!b) return;
      closeMenus();
      cb(b.dataset.model);
    };
  }

  /* ---- read aloud (browser speechSynthesis) ---- */
  let ttsMsg = null;
  let ttsToken = 0;
  function stopSpeech() {
    ttsToken++;
    if ('speechSynthesis' in window) speechSynthesis.cancel();
    if (ttsMsg) {
      const b = $('[data-act="tts"]', ttsMsg);
      if (b) { b.innerHTML = icon('speaker', 16); b.classList.remove('speaking'); b.title = 'Read aloud'; }
    }
    ttsMsg = null;
  }
  function toggleSpeech(msg) {
    if (!('speechSynthesis' in window)) { toast('Speech synthesis isn’t supported in this browser'); return; }
    const same = ttsMsg === msg;
    stopSpeech();
    if (same) return;
    const clone = $('.answer', msg).cloneNode(true);
    clone.querySelectorAll('.code-block, .mermaid-block, .katex-mathml').forEach((n) => n.remove());
    const text = clone.textContent.replace(/\s+/g, ' ').trim();
    if (!text) { toast('Nothing to read'); return; }
    const parts = [];
    let cur = '';
    for (const s of text.match(/[^.!?。]+[.!?。]*\s*/g) || [text]) {
      if (cur && (cur + s).length > 220) { parts.push(cur); cur = s; } else cur += s;
    }
    if (cur) parts.push(cur);
    const token = ++ttsToken;
    ttsMsg = msg;
    const b = $('[data-act="tts"]', msg);
    b.innerHTML = icon('stop', 16);
    b.classList.add('speaking');
    b.title = 'Stop reading';
    parts.forEach((p, i) => {
      const u = new SpeechSynthesisUtterance(p);
      if (i === parts.length - 1) u.onend = u.onerror = () => { if (token === ttsToken) stopSpeech(); };
      speechSynthesis.speak(u);
    });
  }

  /* ---- thumbs up / down with optional note ---- */
  async function setFeedback(msg, val) {
    const m = msg._m;
    if (!m || !m.id) return;
    const next = m.feedback === val ? null : val;
    try { await API.put(`/api/messages/${m.id}/feedback`, { value: next, note: '' }); }
    catch (e) { toast(e.message); return; }
    m.feedback = next;
    $('.feedback-note', msg)?.remove();
    refreshActions(msg);
    if (next === 'down') feedbackNote(msg);
    else if (next === 'up') toast('Thanks for the feedback');
  }
  function feedbackNote(msg) {
    const box = document.createElement('div');
    box.className = 'feedback-note';
    box.innerHTML = '<input type="text" maxlength="500" placeholder="What went wrong? (optional)"><button class="btn primary">Send</button><button class="btn">Skip</button>';
    msg.appendChild(box);
    const input = $('input', box);
    input.focus();
    const done = async (withNote) => {
      const note = withNote ? input.value.trim() : '';
      box.remove();
      if (!note) return;
      try { await API.put(`/api/messages/${msg._m.id}/feedback`, { value: 'down', note }); toast('Thanks — feedback saved'); }
      catch (e) { toast(e.message); }
    };
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') done(true);
      if (e.key === 'Escape') done(false);
    });
    $$('button', box)[0].onclick = () => done(true);
    $$('button', box)[1].onclick = () => done(false);
  }

  /* ---- thread click delegation ---- */
  $('#thread').addEventListener('click', async (e) => {
    if (Viz.handleClick(e)) return;
    const btn = e.target.closest('[data-act]');
    if (!btn) return;
    const act = btn.dataset.act;
    const msg = btn.closest('.msg');
    const m = msg && msg._m;
    const needsId = ['edit', 'regen', 'regen-model', 'continue', 'up', 'down'].includes(act);
    if (needsId && !(m && m.id)) { toast('Not saved yet — try again in a moment'); return; }
    switch (act) {
      case 'copy':
        await copyText(msg._raw || '');
        btn.innerHTML = icon('check', 16);
        setTimeout(() => (btn.innerHTML = icon('copy', 16)), 1400);
        break;
      case 'copy-code':
        await copyText($('code', btn.closest('.code-block')).textContent);
        btn.textContent = 'Copied!';
        setTimeout(() => (btn.textContent = 'Copy'), 1400);
        break;
      case 'open-panel': {
        const block = btn.closest('.code-block');
        if (window.Panel) Panel.openCode($('code', block).textContent, block.dataset.lang || '', msg);
        break;
      }
      case 'download-code': {
        const block = btn.closest('.code-block');
        download(`snippet.${block.dataset.ext || 'txt'}`, $('code', block).textContent);
        break;
      }
      case 'retry':
        if (busy()) return;
        msg.remove();
        send(null, { retry: true });
        break;
      case 'edit': if (!busy()) startEdit(msg); break;
      case 'regen': if (!busy()) send(null, { regenId: m.id }); break;
      case 'regen-model':
        e.stopPropagation(); // keep the popup open (document click closes menus)
        if (!busy()) pickModelFor(btn, (name) => send(null, { regenId: m.id, model: name }));
        break;
      case 'continue': if (!busy()) send(null, { continueId: m.id }); break;
      case 'up':
      case 'down': setFeedback(msg, act); break;
      case 'tts': toggleSpeech(msg); break;
      case 'allow-code':
      case 'deny-code': answerConfirm(msg, btn.dataset.tid, act === 'allow-code'); break;
      case 'always-tool': answerConfirm(msg, btn.dataset.tid, true, true); break;
      case 'prev':
      case 'next': switchBranch(m.siblings[m.sib_index + (act === 'next' ? 1 : -1)]); break;
      default: break;
    }
  });

  /* =====================================================================
     Chat open / new
     ===================================================================== */
  function updateDocTitle() {
    document.title = S.current && S.current.title && S.current.title !== 'New chat' ? `${S.current.title} · Local Chat` : 'Local Chat';
  }

  function newChat(opts) {
    stopSpeech();
    if (S.streaming && S.abort) S.abort.abort();
    S.current = null;
    S.next = { assistant: (opts && opts.assistant) || null, project: (opts && opts.project) || null };
    const asst = S.lib.assistants.find((a) => a.id === S.next.assistant);
    S.messages = [];
    history.replaceState(null, '', location.pathname);
    const names = S.models.map((m) => m.name);
    S.model = names.includes(S.settings.default_model) ? S.settings.default_model : S.model || names[0] || '';
    if (asst && asst.model && names.includes(asst.model)) S.model = asst.model;
    renderModelButton();
    updateChip();
    setEmpty(true);
    updateDocTitle();
    updateCounter();
    renderSidebar();
    input.focus();
  }

  async function openChat(id) {
    if (S.current && S.current.id === id) return;
    stopSpeech();
    if (S.streaming && S.abort) S.abort.abort();
    let chat;
    try { chat = await API.get(`/api/chats/${id}`); } catch (e) { toast('Chat not found'); newChat(); return; }
    S.current = chat;
    S.messages = chat.messages.map((m) => ({ role: m.role, content: m.content }));
    if (chat.model) S.model = chat.model;
    renderModelButton();
    updateChip();
    history.replaceState(null, '', `#/chat/${id}`);
    setEmpty(false);
    renderThread(chat);
    S.stick = true;
    scrollToBottom(true);
    Render.renderBlocks($('#thread')).then(() => scrollToBottom());
    updateDocTitle();
    updateCounter();
    renderSidebar();
  }

  /* =====================================================================
     Sending / streaming
     ===================================================================== */
  /**
   * Send / edit / regenerate / continue / retry.
   * opts: { retry, editId, regenId, continueId, model }
   */
  async function send(text, opts = {}) {
    if (S.streaming) return;
    const model = opts.model || S.model;
    if (!model) {
      toast(S.modelsOk ? 'No model selected' : 'Model server not reachable');
      loadModels();
      return;
    }
    if ('Notification' in window && Notification.permission === 'default') Notification.requestPermission().catch(() => {});

    try {
      if (!S.current) {
        S.current = await API.post('/api/chats', { model, assistant_id: S.next.assistant, project_id: S.next.project });
        S.next = { assistant: null, project: null };
        history.replaceState(null, '', `#/chat/${S.current.id}`);
        updateChip();
      }
    } catch (e) { toast(e.message); return; }

    stopSpeech();
    setEmpty(false);
    const mode = opts.editId ? 'edit' : opts.regenId ? 'regenerate' : opts.continueId ? 'continue' : opts.retry ? 'retry' : 'send';

    // Only the newest reply may offer "Continue".
    $$('#thread .msg.assistant').forEach((el) => {
      if (el._m && el._m.last) { el._m.last = false; refreshActions(el); }
    });
    // Edit / regenerate branch off an earlier point: drop that message and everything after it from view.
    const targetEl = opts.editId ? findMsgEl(opts.editId) : opts.regenId ? findMsgEl(opts.regenId) : null;
    const keepAtts = opts.editId && targetEl ? targetEl._m.attachments || [] : [];
    if (targetEl) {
      while (targetEl.nextSibling) targetEl.nextSibling.remove();
      targetEl.remove();
    }
    S.messages = domMessages();

    let userEl = null;
    if (mode === 'send' || mode === 'edit') {
      userEl = addUserMsg({
        content: text || '(see attached files)',
        created_at: Date.now() / 1000,
        attachments: mode === 'edit' ? keepAtts : opts.files || [],
      });
      S.messages.push({ role: 'user', content: text });
    }
    let v;
    let prevStatus = 'done';
    if (mode === 'continue') {
      v = findMsgEl(opts.continueId)._view;
      prevStatus = v.el._m.status;
      v.el._m.status = 'streaming';
    } else {
      v = addAssistantMsg(null);
      v.el._m.model = model;
    }
    v.streaming = true;
    $('.msg-error', v.el).classList.add('hidden');
    paint(v);
    S.streaming = true;
    S.abort = new AbortController();
    S.stick = true;
    scrollToBottom(true);
    updateSendButton();
    updateCounter();

    const research = !!S.settings.use_research && mode !== 'continue';
    if (research) updateSetting('use_research', false); // one message only
    const chatId = S.current.id;
    const timer = setInterval(() => { if (v.thinkStart && !v.thinkEnd) paint(v); }, 1000);
    let doneEv = null;
    let aborted = false;
    const body = {
      content: text,
      model,
      retry: mode === 'retry',
      edit_id: opts.editId,
      regenerate_id: opts.regenId,
      continue_id: opts.continueId,
      think: S.settings.thinking !== false,
      web: !!S.settings.use_web || research,
      research,
      attachments: mode === 'send' && opts.files && opts.files.length ? opts.files.map((f) => f.id) : undefined,
      skill_run: opts.skillRun || undefined,
      skill_answers: opts.skillAnswers || undefined,
    };
    document.dispatchEvent(new CustomEvent('skills-sent'));

    try {
      await API.stream(`/api/chats/${chatId}/messages`, body, S.abort.signal, (ev) => {
        switch (ev.type) {
          case 'start':
            if (userEl && ev.user_id) {
              userEl._m.id = ev.user_id;
              userEl.dataset.id = ev.user_id;
            }
            if (S.current && S.current.id === chatId) {
              S.current.title = ev.title;
              S.current.title_state = ev.title_state;
              S.current.model = ev.model;
              updateDocTitle();
            }
            loadChats();
            break;
          case 'memory':
            memoryToast(ev);
            if (ev.status === 'saved' || ev.status === 'forgotten') document.dispatchEvent(new CustomEvent('memory-changed'));
            break;
          case 'skill_progress':
            if (typeof SkillView !== 'undefined') SkillView.progress($('.progress', v.el), ev);
            scrollToBottom();
            break;
          case 'progress':
            renderProgress(v, ev);
            scrollToBottom();
            break;
          case 'notice': {
            const n = document.createElement('div');
            n.className = 'notice';
            n.textContent = ev.text;
            v.el.before(n);
            break;
          }
          case 'delta':
            v.text += ev.text;
            schedulePaint(v);
            break;
          case 'error':
            showError(v, ev);
            break;
          case 'done':
            doneEv = ev;
            break;
          case 'tool_start':
            v.tools.push({ id: ev.id, name: ev.name, args: ev.args, status: 'running' });
            renderTools(v);
            scrollToBottom();
            break;
          case 'confirm': {
            const t = v.tools.find((x) => x.id === ev.id);
            if (t) { t.status = 'confirm'; t.cid = ev.cid; t.args = { ...t.args, code: ev.code }; t.confirm = { kind: ev.kind, title: ev.title, text: ev.code, always: ev.always }; }
            renderTools(v);
            scrollToBottom(true);
            break;
          }
          case 'tool_result': {
            if (ev.hidden) { v.tools = v.tools.filter((x) => x.id !== ev.id); renderTools(v); break; } // lookup found nothing worth a card
            const t = v.tools.find((x) => x.id === ev.id);
            if (t) Object.assign(t, { status: ev.ok ? 'done' : 'error', ok: ev.ok, summary: ev.summary, detail: ev.detail, files: ev.files, declined: ev.declined });
            renderTools(v);
            scrollToBottom();
            break;
          }
          case 'truncate':
            v.text = v.text.slice(0, ev.length);
            schedulePaint(v);
            break;
          case 'state':
            v.taskState = ev.state;
            renderTools(v);
            break;
          case 'verification':
            v.verification = ev;
            renderSources(v);
            break;
          case 'sources':
            v.sources = ev.sources;
            renderSources(v);
            schedulePaint(v);
            break;
          default:
            break;
        }
      });
    } catch (err) {
      if (err.name === 'AbortError') aborted = true;
      else showError(v, { message: 'Lost connection to the local server.', hint: err.message, retry_ok: !v.text.trim() });
    } finally {
      clearInterval(timer);
      clearTimeout(v.raf);
      v.raf = 0;
      v.streaming = false;
      renderProgress(v, null);
      if (typeof SkillView !== 'undefined') SkillView.progress($('.progress', v.el), null);
      v.tools.forEach((x) => { if (isRunning(x)) Object.assign(x, { status: 'error', ok: false, summary: 'Interrupted' }); });
      renderTools(v);
      if (v.thinkStart && !v.thinkEnd) v.thinkEnd = performance.now();
      if (doneEv && doneEv.think_ms && mode !== 'continue') v.thinkMs = doneEv.think_ms;
      paint(v);
      Render.renderBlocks(v.el);
      if (doneEv && mode !== 'continue' && window.Panel) Panel.autoOpen(v.el);

      const hasError = !$('.msg-error', v.el).classList.contains('hidden');
      if (!v.text.trim() && !hasError && !v.tools.length) {
        v.el.remove();
      } else {
        const m = v.el._m;
        if (doneEv) {
          Object.assign(m, { id: doneEv.id, status: doneEv.status || 'done', tokens_in: doneEv.tokens_in, tokens_out: doneEv.tokens_out, model, last: true });
          v.id = doneEv.id;
          if (doneEv.id) v.el.dataset.id = doneEv.id;
        } else if (mode === 'continue') {
          m.status = prevStatus;
          m.last = true;
        } else if (v.text.trim()) {
          Object.assign(m, { status: aborted ? 'stopped' : 'error', last: true });
        }
        refreshActions(v.el);
        if (!v.text.trim()) v.el.classList.add('errored');
      }

      S.messages = domMessages();
      S.streaming = false;
      S.abort = null;
      updateSendButton();
      updateCounter();

      // Refresh from the server to pick up message ids and "‹ 2 / 3 ›" navigation.
      if (aborted) setTimeout(() => reloadChat(chatId), 600);
      else if (doneEv && mode !== 'send' && mode !== 'retry') reloadChat(chatId);

      if (document.hidden && 'Notification' in window && Notification.permission === 'granted' && doneEv) {
        try { new Notification('Response ready', { body: (S.current && S.current.title) || 'Your local model finished.' }); } catch (_) { /* ignore */ }
      }
      if (doneEv && mode === 'send' && S.current && S.current.id === chatId && S.current.title_state === 1) generateTitle(chatId);
    }
  }

  async function generateTitle(chatId) {
    try {
      const r = await API.post(`/api/chats/${chatId}/title`);
      if (S.current && S.current.id === chatId) { S.current.title = r.title; S.current.title_state = r.title_state; updateDocTitle(); }
      loadChats();
    } catch (_) { /* provisional title stays */ }
  }

  /* =====================================================================
     Actions shared by shortcuts and the command palette
     ===================================================================== */
  async function copyLastResponse() {
    const els = $$('#thread .msg.assistant');
    const last = [...els].reverse().find((el) => el._raw);
    if (!last) { toast('Nothing to copy yet'); return; }
    await copyText(last._raw);
    toast('Copied last response');
  }
  const openShortcuts = () => $('#dlg-shortcuts').showModal();
  const focusComposer = () => input.focus();
  function toggleThemeQuick() {
    const dark = document.documentElement.dataset.theme === 'dark';
    updateSetting('theme', dark ? 'light' : 'dark');
  }

  document.addEventListener('keydown', (e) => {
    const mod = e.ctrlKey || e.metaKey;
    const k = e.key.toLowerCase();
    if (mod && !e.shiftKey && !e.altKey && k === 'k') { e.preventDefault(); openPalette(); }
    else if (mod && e.shiftKey && k === 'o') { e.preventDefault(); newChat(); }
    else if (mod && e.shiftKey && k === 's') { e.preventDefault(); toggleSidebar(); }
    else if (e.shiftKey && !mod && e.key === 'Escape') { e.preventDefault(); focusComposer(); }
    else if (mod && e.shiftKey && k === 'c') {
      if (!String(getSelection())) { e.preventDefault(); copyLastResponse(); }
    } else if (mod && !e.shiftKey && (k === '/' || k === '?')) { e.preventDefault(); openShortcuts(); }
  });

  /* ---- command palette ---- */
  let palItems = [];
  let palSel = 0;
  function paletteEntries(q) {
    const actions = [
      { label: 'New chat', hint: 'Ctrl+Shift+O', run: newChat },
      { label: 'Toggle sidebar', hint: 'Ctrl+Shift+S', run: toggleSidebar },
      { label: 'Open settings', run: () => openSettings() },
      { label: 'Toggle light / dark theme', run: toggleThemeQuick },
      { label: 'Focus message box', hint: 'Shift+Esc', run: focusComposer },
      { label: 'Copy last response', hint: 'Ctrl+Shift+C', run: copyLastResponse },
      { label: 'Keyboard shortcuts', hint: 'Ctrl+/', run: openShortcuts },
      { label: 'Open Library (memory, assistants, projects)', run: () => window.Library && Library.open() },
      { label: 'Toggle side panel', run: () => window.Panel && Panel.toggle() },
      { label: 'View archived chats', run: () => { S.archivedView = true; setSidebar(true); loadChats(); } },
    ];
    const ql = q.toLowerCase();
    const a = actions.filter((x) => x.label.toLowerCase().includes(ql)).map((x) => ({ ...x, group: 'Actions' }));
    const base = S.archivedView ? [] : S.chats;
    const chats = base.filter((c) => c.title.toLowerCase().includes(ql)).slice(0, 12).map((c) => ({ label: c.title, group: 'Chats', run: () => openChat(c.id) }));
    return [...a, ...chats];
  }
  function renderPalette() {
    let html = '';
    let group = '';
    palItems.forEach((it, i) => {
      if (it.group !== group) { group = it.group; html += `<div class="pal-label">${group}</div>`; }
      html += `<button class="menu-item${i === palSel ? ' sel' : ''}" data-i="${i}" role="option">${esc(it.label)}${it.hint ? `<span class="meta">${esc(it.hint)}</span>` : ''}</button>`;
    });
    $('#pal-list').innerHTML = html || '<div class="menu-note">No results</div>';
    $('#pal-list .sel')?.scrollIntoView({ block: 'nearest' });
  }
  async function openPalette() {
    if (S.archivedView) { S.archivedView = false; await loadChats(); }
    const dlg = $('#dlg-palette');
    if (dlg.open) return;
    $('#pal-input').value = '';
    palSel = 0;
    palItems = paletteEntries('');
    renderPalette();
    dlg.showModal();
    $('#pal-input').focus();
  }
  function runPalette(i) {
    const it = palItems[i];
    if (!it) return;
    $('#dlg-palette').close();
    it.run();
  }
  $('#pal-input').addEventListener('input', (e) => { palSel = 0; palItems = paletteEntries(e.target.value); renderPalette(); });
  $('#pal-input').addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); palSel = Math.min(palSel + 1, palItems.length - 1); renderPalette(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); palSel = Math.max(palSel - 1, 0); renderPalette(); }
    else if (e.key === 'Enter') { e.preventDefault(); runPalette(palSel); }
  });
  $('#pal-list').addEventListener('click', (e) => { const b = e.target.closest('[data-i]'); if (b) runPalette(+b.dataset.i); });
  $('#dlg-palette').addEventListener('click', (e) => { if (e.target === e.currentTarget) e.currentTarget.close(); });

  /* =====================================================================
     Settings dialog
     ===================================================================== */
  const LANGS = ['Auto', 'English', 'Spanish', 'French', 'German', 'Italian', 'Portuguese', 'Hindi', 'Japanese', 'Korean', 'Chinese'];
  const TABS = {
    general: {
      label: 'General',
      fields: [
        { k: 'theme', t: 'select', l: 'Theme', o: [['system', 'System'], ['light', 'Light'], ['dark', 'Dark']] },
        { k: 'font_size', t: 'range', l: 'Font size', min: 13, max: 20, step: 1, unit: 'px' },
        { k: 'display_name', t: 'text', l: 'Your name', h: 'Used for the greeting.' },
        { k: 'language', t: 'select', l: 'Response language', h: 'Auto replies in the language you write in.', o: LANGS.map((x) => [x, x]) },
        { k: 'send_on_enter', t: 'toggle', l: 'Send with Enter', h: 'When off, Ctrl/⌘+Enter sends and Enter adds a new line.' },
      ],
    },
    models: {
      label: 'Models',
      fields: [
        { k: 'provider', t: 'select', l: 'Provider', h: 'Ollama, or any OpenAI-compatible server (LM Studio, llama.cpp server…).', o: [['ollama', 'Ollama'], ['openai', 'OpenAI-compatible']] },
        { k: 'ollama_url', t: 'text', l: 'Ollama URL', h: 'Default http://localhost:11434', show: (s) => s.provider === 'ollama' },
        { k: 'openai_url', t: 'text', l: 'Base URL', h: 'LM Studio: http://localhost:1234/v1 · llama.cpp: http://localhost:8080/v1', show: (s) => s.provider === 'openai' },
        { k: 'openai_key', t: 'password', l: 'API key (optional)', show: (s) => s.provider === 'openai' },
        { k: 'default_model', t: 'model', l: 'Default model', h: 'Used for new chats.' },
        { k: 'temperature', t: 'range', l: 'Temperature', min: 0, max: 2, step: 0.05 },
        { k: 'top_p', t: 'range', l: 'Top P', min: 0, max: 1, step: 0.05 },
        { k: 'num_ctx', t: 'number', l: 'Context length (tokens)', h: 'Larger values use more RAM/VRAM.', min: 512, step: 512 },
        { k: 'max_tokens', t: 'number', l: 'Max output tokens', h: '0 = no limit.', min: 0, step: 64 },
        { k: 'keep_alive', t: 'text', l: 'Keep model loaded', h: 'Ollama only, e.g. 5m, 1h, -1 (forever).', show: (s) => s.provider === 'ollama' },
        { t: 'test' },
      ],
    },
    tools: {
      label: 'Tools',
      fields: [
        { k: 'tool_web_search', t: 'toggle', l: 'Web search', h: 'Also needs the Web search switch in the + menu to be on.' },
        { k: 'tool_fetch_page', t: 'toggle', l: 'Read web pages' },
        { k: 'tool_read_file', t: 'toggle', l: 'Read attached files', h: 'Scanned PDFs and images are read with local OCR.' },
        { k: 'skills_enabled', t: 'toggle', l: 'Skills (/planning, /compare, /analyze, /create, /learn)', h: 'Type / in the message box. Each command understands the task, asks only what is missing, uses the tools and shows a rich result. Preferences you give as feedback are saved under Library.' },
        { k: 'ground_first', t: 'toggle', l: 'Search before answering', h: 'For factual and time-sensitive questions (Web search on) the app searches and reads the best page first, instead of leaving it to the model.' },
        { k: 'agent_engine', t: 'select', l: 'Chart / dashboard builder', h: 'The same flow, either as the built-in loop or as a LangGraph graph (needs langgraph installed). Results are identical; this is for comparing them.', o: [['builtin', 'Built-in'], ['langgraph', 'LangGraph']] },
        { k: 'vector_backend', t: 'select', l: 'Book search store (/learn)', h: 'SQLite is built in and exact. Chroma is a vector database (pip install chromadb); if it cannot start, SQLite is used.', o: [['sqlite', 'SQLite (built in)'], ['chroma', 'Chroma']] },
        { k: 'verify_answers', t: 'select', l: 'Check answers against sources', h: 'Compares numbers, names and dates in the answer with the text the tools returned. “Flag” marks unsupported statements; “Fix” also rewrites them out.', o: [['fix', 'Flag and fix'], ['flag', 'Flag only'], ['off', 'Off']] },
        { k: 'js_render', t: 'select', l: 'Read JavaScript / blocked pages', h: 'Uses Crawl4AI with your installed Edge or Chrome when the plain reader is blocked or finds almost no text. “Always” uses it for every page (slower, cleaner text).', o: [['auto', 'Only when needed'], ['always', 'Always'], ['off', 'Never']] },
        { k: 'entity_cards', t: 'toggle', l: 'Visual knowledge cards', h: 'Show an image + facts card when you ask about a named person, product, place, animal… Needs Web search on.' },
        { k: 'tool_run_python', t: 'toggle', l: 'Code execution', h: 'Always available to the model. Python runs on this computer in a per-chat folder with network access blocked.' },
        { k: 'tool_datetime', t: 'toggle', l: 'Date and time' },
        { k: 'tool_search_chat', t: 'toggle', l: 'Search earlier messages', h: 'Lets the model look up links, files and numbers you gave earlier in the same chat.' },
        { k: 'confirm_code', t: 'toggle', l: 'Ask before running code', h: 'Strongly recommended.' },
        { k: 'code_timeout', t: 'number', l: 'Code time limit (seconds)', min: 1, step: 5 },
        { k: 'search_provider', t: 'select', l: 'Search provider', o: [['ddgs', 'DuckDuckGo (no key)'], ['searxng', 'SearXNG (self-hosted)'], ['tavily', 'Tavily'], ['brave', 'Brave Search']] },
        { k: 'searxng_url', t: 'text', l: 'SearXNG URL', h: 'e.g. http://localhost:8080', show: (s) => s.search_provider === 'searxng' },
        { k: 'tavily_key', t: 'password', l: 'Tavily API key', show: (s) => s.search_provider === 'tavily' },
        { k: 'brave_key', t: 'password', l: 'Brave API key', show: (s) => s.search_provider === 'brave' },
        { k: 'search_results', t: 'number', l: 'Results per search', min: 1, step: 1 },
        { k: 'page_char_limit', t: 'number', l: 'Page text limit (characters)', h: 'How much of a web page the model gets to read.', min: 1000, step: 1000 },
      ],
    },
    personalization: {
      label: 'Personalization',
      fields: [
        { k: 'about_you', t: 'textarea', l: 'What should the AI know about you?', h: 'Applied to every chat.', ph: 'e.g. I’m a CS student who prefers concise answers with code examples.' },
        { k: 'response_style', t: 'textarea', l: 'How should it respond?', h: 'Tone, format, length…', ph: 'e.g. Be direct. Use bullet points. Avoid unnecessary caveats.' },
      ],
    },
    connections: { label: 'Connections', custom: true },
    localai: { label: 'Local AI', custom: true },
    data: { label: 'Data & usage', custom: true },
  };
  let settingsTab = 'general';

  function fieldHTML(f) {
    const v = S.settings[f.k];
    const lbl = `<div class="lbl"><b>${esc(f.l)}</b>${f.h ? `<span>${esc(f.h)}</span>` : ''}</div>`;
    switch (f.t) {
      case 'select':
        return `<div class="field">${lbl}<select data-k="${f.k}">${f.o.map(([val, name]) => `<option value="${esc(val)}"${val === v ? ' selected' : ''}>${esc(name)}</option>`).join('')}</select></div>`;
      case 'model': {
        const names = S.models.map((m) => m.name);
        if (v && !names.includes(v)) names.unshift(v);
        return `<div class="field">${lbl}<select data-k="${f.k}"><option value="">(first available)</option>${names.map((n) => `<option value="${esc(n)}"${n === v ? ' selected' : ''}>${esc(n)}</option>`).join('')}</select></div>`;
      }
      case 'range':
        return `<div class="field">${lbl}<div class="range"><input type="range" data-k="${f.k}" min="${f.min}" max="${f.max}" step="${f.step}" value="${v}"><output>${v}${f.unit || ''}</output></div></div>`;
      case 'number':
        return `<div class="field">${lbl}<input type="number" data-k="${f.k}" min="${f.min ?? ''}" step="${f.step || 1}" value="${v}"></div>`;
      case 'toggle':
        return `<div class="field">${lbl}<label class="switch"><input type="checkbox" data-k="${f.k}"${v ? ' checked' : ''}><i></i></label></div>`;
      case 'textarea':
        return `<div class="field stack">${lbl}<textarea data-k="${f.k}" placeholder="${esc(f.ph || '')}">${esc(v || '')}</textarea></div>`;
      case 'test':
        return `<div class="field stack"><div><button class="btn" id="btn-test">Test connection</button></div><div id="test-result"></div></div>`;
      default:
        return `<div class="field">${lbl}<input type="${f.t}" data-k="${f.k}" value="${esc(v ?? '')}" autocomplete="off" spellcheck="false"></div>`;
    }
  }

  async function renderSettings() {
    $('#settings-tabs').innerHTML = Object.entries(TABS)
      .map(([id, t]) => `<button data-tab="${id}" class="${id === settingsTab ? 'active' : ''}">${t.label}</button>`)
      .join('');
    const tab = TABS[settingsTab];
    const body = $('#settings-body');
    if (tab.custom) { await (settingsTab === 'connections' ? Connections.render(body) : settingsTab === 'localai' ? renderLocalAI(body) : renderDataTab(body)); return; }
    body.innerHTML = tab.fields.filter((f) => !f.show || f.show(S.settings)).map(fieldHTML).join('');
  }

  /* ---- Settings → Local AI: GPU status, app-loaded models, NLP try-it ---- */
  async function renderLocalAI(body) {
    body.innerHTML = '<div class="muted">Reading GPU status…</div>';
    let g, e;
    let media = { speech: false, ocr: false, speech_models: ['small'] };
    try { media = await API.get('/api/media/status'); } catch (_) { /* optional */ }
    try { [g, e] = await Promise.all([API.get('/api/gpu'), API.get('/api/embeddings/status')]); } catch (err) { body.textContent = 'Could not read status: ' + err.message; return; }
    const gp = g.gpu, t = g.torch;
    const pct = gp && gp.total_mb ? Math.round((gp.used_mb / gp.total_mb) * 100) : 0;
    const mode = !gp ? 'No NVIDIA GPU detected' : t.cuda ? 'CUDA ready — app models use the GPU when VRAM is free' : 'CPU only — ' + esc(t.error || 'CUDA not usable');
    body.innerHTML =
      `<div class="section-title">GPU</div>` +
      (gp
        ? `<div class="gpu-card"><div class="gpu-name">${esc(gp.name)}</div>` +
          `<div class="gpu-bar" title="${gp.used_mb} of ${gp.total_mb} MB used"><span style="width:${pct}%"></span></div>` +
          `<div class="muted small">${gp.used_mb} MB used · ${gp.free_mb} MB free of ${gp.total_mb} MB · ${gp.utilization ?? '—'}% busy · ${gp.temp_c ?? '—'} °C · driver ${esc(gp.driver)}</div></div>`
        : `<p class="muted">No NVIDIA GPU was found by <code>nvidia-smi</code>.</p>`) +
      `<p class="small"><b>Mode:</b> ${mode}${t.installed ? ` <span class="muted">(PyTorch ${esc(t.version)})</span>` : ' <span class="muted">(PyTorch not installed)</span>'}</p>` +
      `<div class="section-title">Models this app has loaded</div>` +
      (g.app_models.length
        ? `<table class="stats-table"><thead><tr><th>Model</th><th>Device</th><th>VRAM</th><th>Idle</th></tr></thead><tbody>${g.app_models.map((m) => `<tr><td>${esc(m.name)}</td><td>${esc(m.device)}</td><td>${m.vram_mb || 0} MB</td><td>${m.idle_s}s</td></tr>`).join('')}</tbody></table>`
        : `<p class="muted small">None right now. Models load on first use and are freed after 5 idle minutes.</p>`) +
      `<div class="btn-row"><button class="btn" id="ai-refresh">Refresh</button><button class="btn" id="ai-unload">Free app models</button></div>` +
      `<p class="muted small">Other programs using the GPU (Ollama, LM Studio, browsers) are only <i>read</i>, never unloaded or stopped: ${g.external.length ? g.external.map((x) => esc(x.process.split('\\').pop()) + (x.vram_mb ? ` ${x.vram_mb} MB` : '')).join(', ') : 'none listed'}.</p>` +
      (g.recent.length ? `<div class="section-title">Recent tasks</div><table class="stats-table"><thead><tr><th>Task</th><th>Device</th><th>Time</th><th>Why</th></tr></thead><tbody>${g.recent.slice().reverse().map((r) => `<tr><td>${esc(r.task)}</td><td>${esc(r.device)}</td><td>${r.ms} ms</td><td class="muted">${esc(r.reason)}</td></tr>`).join('')}</tbody></table>` : '') +
      `<div class="section-title">Semantic search</div>` +
      `<p class="small">${e.available ? 'Ready' : 'Not installed'} · model <code>${esc(e.model)}</code> · ${e.chunks} indexed passages</p>` +
      `<div class="section-title">Try the text tools</div>` +
      `<textarea id="nlp-in" rows="3" placeholder="Type English, Hindi or Hinglish…" style="width:100%"></textarea>` +
      `<div class="btn-row"><button class="btn primary" id="nlp-go">Analyze</button></div><div id="nlp-out" class="small"></div>` +
      `<div class="section-title">Speech to text (offline)</div>` +
      `<p class="small muted">${media.speech ? 'faster-whisper is installed. Use the microphone button next to the message box.' : 'faster-whisper is not installed.'} The model is loaded for each recording and released afterwards.</p>` +
      `<div class="field"><label>Model size</label><select id="stt-model">${media.speech_models.map((m) => `<option value="${m}"${S.settings.stt_model === m ? ' selected' : ''}>${m}</option>`).join('')}</select></div>` +
      `<div class="field"><label>Language</label><select id="stt-lang">${[['auto', 'Detect automatically'], ['en', 'English'], ['hi', 'Hindi']].map(([v, l]) => `<option value="${v}"${S.settings.stt_language === v ? ' selected' : ''}>${l}</option>`).join('')}</select></div>` +
      `<label class="check"><input type="checkbox" id="stt-hinglish"${S.settings.stt_hinglish ? ' checked' : ''}> Hinglish: Hindi speech written in Latin letters</label>` +
      `<label class="check"><input type="checkbox" id="stt-clean"${S.settings.stt_cleanup ? ' checked' : ''}> Remove filler words (um, uh)</label>` +
      `<div class="section-title">Read text from an image or scanned PDF (OCR)</div>` +
      `<p class="small muted">${media.ocr ? 'Runs on the CPU, so the GPU stays free for the chat model. English and Latin-script text; Hindi (Devanagari) is not supported.' : 'rapidocr-onnxruntime is not installed.'}</p>` +
      `<div class="media-try"><input type="file" id="ocr-file" accept="image/*,application/pdf"> <label class="check"><input type="checkbox" id="ocr-formulas"> Find formulas</label>` +
      `<div class="btn-row"><button class="btn" id="ocr-go">Read text</button><button class="btn" id="ocr-copy">Copy</button></div><textarea id="ocr-out" rows="6" placeholder="Extracted text appears here"></textarea></div>` +
      `<div class="section-title">Understand an image</div>` +
      `<p class="small muted">Uses the model selected for chat when it can see images; no extra model is loaded.</p>` +
      `<div class="media-try"><input type="file" id="vis-file" accept="image/*"> <select id="vis-task">${['describe', 'screenshot', 'diagram', 'chart', 'document', 'objects', 'formula'].map((t) => `<option value="${t}">${t}</option>`).join('')}</select>` +
      `<div class="btn-row"><button class="btn" id="vis-go">Analyze image</button></div><div id="vis-out" class="small"></div></div>`;
    $('#stt-model').onchange = (ev) => updateSetting('stt_model', ev.target.value);
    $('#stt-lang').onchange = (ev) => updateSetting('stt_language', ev.target.value);
    $('#stt-hinglish').onchange = (ev) => updateSetting('stt_hinglish', ev.target.checked);
    $('#stt-clean').onchange = (ev) => updateSetting('stt_cleanup', ev.target.checked);
    const upload = async (url, file, extra) => {
      const fd = new FormData();
      fd.append('file', file);
      for (const [k, v] of Object.entries(extra || {})) fd.append(k, v);
      const res = await fetch(url, { method: 'POST', body: fd });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(d.detail || 'Request failed');
      return d;
    };
    $('#ocr-go').onclick = async () => {
      const f = $('#ocr-file').files[0];
      if (!f) { toast('Choose an image or PDF first.'); return; }
      $('#ocr-out').value = 'Reading…';
      try {
        const d = await upload('/api/ocr', f, { formulas: $('#ocr-formulas').checked ? 'true' : 'false' });
        $('#ocr-out').value = d.text + (d.formulas && d.formulas.length ? '\n\n— Possible formulas —\n' + d.formulas.join('\n') : '');
        toast(`OCR done in ${(d.ms / 1000).toFixed(1)}s · ${d.device}` + (d.confidence !== undefined ? ` · confidence ${d.confidence}` : ''));
      } catch (err) { $('#ocr-out').value = err.message; }
    };
    $('#ocr-copy').onclick = () => copyText($('#ocr-out').value);
    $('#vis-go').onclick = async () => {
      const f = $('#vis-file').files[0];
      if (!f) { toast('Choose an image first.'); return; }
      $('#vis-out').textContent = 'Analyzing…';
      try {
        const d = await upload('/api/vision', f, { task: $('#vis-task').value, model: S.model || '' });
        $('#vis-out').innerHTML = (d.answer ? `<p>${esc(d.answer)}</p>` : '') + (d.latex && d.latex.length ? `<pre class="tool-code">${esc(d.latex.join('\n'))}</pre>` : '') +
          (d.ocr_text ? `<details><summary>Text found by OCR</summary><pre class="tool-out">${esc(d.ocr_text)}</pre></details>` : '') +
          (d.notes.length ? `<p class="muted small">${d.notes.map(esc).join(' ')}</p>` : '') + `<p class="muted small">${d.used_model ? 'Vision model' : 'OCR only'} · ${(d.ms / 1000).toFixed(1)}s</p>`;
      } catch (err) { $('#vis-out').textContent = err.message; }
    };
    $('#ai-refresh').onclick = () => renderLocalAI(body);
    $('#ai-unload').onclick = async () => { await API.post('/api/gpu/unload', {}); renderLocalAI(body); };
    $('#nlp-go').onclick = async () => {
      const text = $('#nlp-in').value.trim();
      if (!text) return;
      $('#nlp-out').textContent = 'Working…';
      try {
        const r = await API.post('/api/nlp/analyze', { text });
        $('#nlp-out').innerHTML = r.ok
          ? `<table class="stats-table"><tbody><tr><td>Language</td><td>${esc(r.language)} <span class="muted">(${esc(r.script)})</span></td></tr><tr><td>Intent</td><td>${esc(r.intent)}</td></tr><tr><td>Sentiment</td><td>${esc(r.sentiment)}</td></tr>` +
            `<tr><td>Keywords</td><td>${r.keywords.map(esc).join(', ') || '—'}</td></tr><tr><td>Entities</td><td>${r.entities.map((x) => esc(x.text) + ' <span class="muted">' + esc(x.label) + '</span>').join(' · ') || '—'}</td></tr>` +
            `<tr><td>Ran on</td><td>${esc(r.device)} · ${r.ms} ms</td></tr></tbody></table>`
          : `<span class="muted">${esc(r.error)}</span>`;
      } catch (err) { $('#nlp-out').textContent = err.message; }
    };
  }

  async function renderDataTab(body) {
    body.innerHTML =
      `<div class="section-title">Export</div>` +
      `<div class="btn-row"><a class="btn" href="/api/export?format=json" download>Export all chats (JSON)</a><a class="btn" href="/api/export?format=md" download>Export all chats (Markdown)</a></div>` +
      `<div class="btn-row"><a class="btn" href="/api/library/export" download>Export library (memory, assistants, projects)</a></div>` +
      `<div class="section-title">Import</div>` +
      `<div class="btn-row"><button class="btn" id="btn-import">Import chats or library…</button></div>` +
      `<p class="muted small">Accepts this app’s JSON export, ChatGPT’s <code>conversations.json</code>, Claude’s <code>conversations.json</code> and a library export. Imported chats are added; nothing is overwritten.</p>` +
      `<div class="section-title">Danger zone</div>` +
      `<div class="btn-row"><button class="btn danger-outline" id="btn-delete-all">Delete all chats</button></div>` +
      `<div class="section-title">Usage</div><div id="stats">Loading…</div>`;
    try {
      const st = await API.get('/api/stats');
      const rows = st.models.map((m) => `<tr><td>${esc(m.model)}</td><td>${m.messages.toLocaleString()}</td><td>${m.tokens_in.toLocaleString()}</td><td>${m.tokens_out.toLocaleString()}</td></tr>`).join('');
      $('#stats').innerHTML =
        `<p class="muted small">${st.chats} chats · ${st.user_messages} messages sent</p>` +
        (rows ? `<table class="stats-table"><thead><tr><th>Model</th><th>Replies</th><th>Tokens in</th><th>Tokens out</th></tr></thead><tbody>${rows}</tbody></table>` : '<p class="muted">No usage yet.</p>');
    } catch (e) { $('#stats').textContent = 'Could not load stats.'; }
  }

  let saveTimer;
  let pending = {};
  function updateSetting(k, v) {
    S.settings[k] = v;
    pending[k] = v;
    if (['theme', 'font_size', 'display_name'].includes(k)) applyAppearance();
    if (k === 'num_ctx') updateCounter();
    clearTimeout(saveTimer);
    saveTimer = setTimeout(flushSettings, 350);
  }
  async function flushSettings() {
    const patch = pending;
    pending = {};
    if (!Object.keys(patch).length) return;
    try { S.settings = await API.put('/api/settings', patch); }
    catch (e) { toast('Could not save settings: ' + e.message); return; }
    if ('provider' in patch || 'ollama_url' in patch || 'openai_url' in patch || 'openai_key' in patch) loadModels();
  }

  function openSettings(tab) {
    if (tab) settingsTab = tab;
    renderSettings();
    const dlg = $('#dlg-settings');
    if (!dlg.open) dlg.showModal();
  }
  $('#btn-settings').onclick = () => { openSettings(); if (isMobile()) setSidebar(false); };
  $('#dlg-settings').addEventListener('close', flushSettings);
  $('#settings-tabs').addEventListener('click', (e) => {
    const b = e.target.closest('[data-tab]');
    if (b) { settingsTab = b.dataset.tab; renderSettings(); }
  });
  $('#settings-body').addEventListener('input', (e) => {
    const t = e.target;
    const k = t.dataset.k;
    if (!k) return;
    let v;
    if (t.type === 'checkbox') v = t.checked;
    else if (t.type === 'range') { v = parseFloat(t.value); const out = t.parentElement.querySelector('output'); if (out) out.textContent = v + (k === 'font_size' ? 'px' : ''); }
    else if (t.type === 'number') { if (t.value === '') return; v = parseFloat(t.value); }
    else v = t.value;
    updateSetting(k, v);
  });
  $('#settings-body').addEventListener('change', (e) => {
    if (['provider', 'search_provider'].includes(e.target.dataset.k)) renderSettings();
  });
  $('#settings-body').addEventListener('click', async (e) => {
    if (e.target.id === 'btn-test') {
      await flushSettings();
      const out = $('#test-result');
      out.className = 'test-result';
      out.textContent = 'Testing…';
      await loadModels();
      if (S.modelsOk) { out.classList.add('ok'); out.textContent = `Connected — ${S.models.length} model${S.models.length === 1 ? '' : 's'} found.`; }
      else {
        out.classList.add('bad');
        const info = await API.get('/api/models');
        out.innerHTML = `${esc(info.error || 'Not connected.')} ${inlineCode(info.hint || '')}`;
      }
    } else if (e.target.id === 'btn-import') {
      $('#import-input').click();
    } else if (e.target.id === 'btn-delete-all') {
      if (!(await confirmDialog('Delete all chats?', 'Every chat and message will be permanently deleted. This cannot be undone.', 'Delete everything'))) return;
      await API.del('/api/chats');
      newChat();
      await loadChats();
      toast('All chats deleted');
      renderSettings();
    }
  });

  /* =====================================================================
     Wiring + init
     ===================================================================== */
  $('#btn-new').innerHTML = `${icon('plus')} New chat`;
  $('.sb-search .ic').innerHTML = icon('search', 16);
  $('#btn-archived .ic').innerHTML = icon('archive', 18);
  $('#btn-library .ic').innerHTML = icon('book', 18);
  $('#btn-learn .ic').innerHTML = icon('cap', 18);
  $('#btn-panel').innerHTML = icon('panel');
  $('#btn-sidebar-open').innerHTML = icon('sidebar');
  $('#btn-sidebar-close').innerHTML = icon('sidebar');
  $('#btn-palette').innerHTML = icon('command');
  $('#model-btn .chev').innerHTML = icon('chevron', 16);
  $$('[data-close]').forEach((b) => { b.innerHTML = icon('x'); b.onclick = () => b.closest('dialog').close(); });
  $$('dialog.dlg').forEach((d) => d.addEventListener('mousedown', (e) => { if (e.target === d) d.close(); }));
  $('#btn-new').onclick = () => { newChat(); if (isMobile()) setSidebar(false); };
  $('#btn-sidebar-open').onclick = () => setSidebar(true);
  $('#btn-sidebar-close').onclick = () => setSidebar(false);
  $('#scrim').onclick = () => setSidebar(false);
  $('#btn-palette').onclick = openPalette;
  window.addEventListener('hashchange', () => {
    const m = location.hash.match(/^#\/chat\/(\w+)/);
    if (m) openChat(m[1]); else if (S.current) newChat();
  });

  $('#import-input').addEventListener('change', async (e) => {
    const f = e.target.files[0];
    e.target.value = '';
    if (!f) return;
    const fd = new FormData();
    fd.append('file', f);
    try {
      const r = await fetch('/api/import', { method: 'POST', body: fd });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.detail || r.statusText);
      const bits = [];
      if (j.chats) bits.push(`${j.chats} chat${j.chats === 1 ? '' : 's'}`);
      if (j.memories) bits.push(`${j.memories} memories`);
      if (j.assistants) bits.push(`${j.assistants} assistants`);
      if (j.projects) bits.push(`${j.projects} projects`);
      toast('Imported ' + (bits.join(', ') || 'nothing new'), 3500);
      await loadChats();
      loadLib();
      if ($('#dlg-settings').open) renderSettings();
    } catch (err) { toast('Import failed: ' + err.message, 5000); }
  });

  /* ---- assistant / project chip in the top bar ---- */
  async function loadLib() {
    try {
      const [assistants, projects] = await Promise.all([API.get('/api/assistants'), API.get('/api/projects')]);
      S.lib = { assistants, projects };
    } catch (_) { /* optional */ }
    updateChip();
  }
  function updateChip() {
    const chip = $('#ctx-chip');
    const aid = S.current ? S.current.assistant_id : S.next.assistant;
    const pid = S.current ? S.current.project_id : S.next.project;
    const a = S.lib.assistants.find((x) => x.id === aid);
    const p = S.lib.projects.find((x) => x.id === pid);
    const bits = [];
    if (a) bits.push(`${icon('bot', 14)} ${esc(a.name)}`);
    if (p) bits.push(`${icon('folder', 14)} ${esc(p.name)}`);
    chip.innerHTML = bits.join('<span class="dot">·</span>');
    chip.classList.toggle('hidden', !bits.length);
  }
  $('#ctx-chip').onclick = () => window.Library && Library.open(S.lib.assistants.find((x) => x.id === (S.current ? S.current.assistant_id : S.next.assistant)) ? 'assistants' : 'projects');
  $('#btn-library').onclick = () => { window.Library && Library.open(); if (isMobile()) setSidebar(false); };
  $('#btn-panel').onclick = () => window.Panel && Panel.toggle();

  async function init() {
    if (LS.get('sb', 'open') === 'collapsed' && !isMobile()) document.body.classList.add('sidebar-collapsed');
    document.documentElement.classList.remove('pre-collapsed');
    $('#send').innerHTML = icon('arrowUp');
    setEmpty(true);
    updateSendButton();
    try { S.settings = await API.get('/api/settings'); }
    catch (e) { toast('Backend not reachable: ' + e.message, 6000); }
    applyAppearance();
    await Promise.all([loadModels(), loadChats(), loadLib()]);
    API.get('/api/connections').then((d) => { S.servers = d.servers; }).catch(() => {});
    const m = location.hash.match(/^#\/chat\/(\w+)/);
    if (m) await openChat(m[1]);
    else input.focus();
  }
  document.addEventListener('viz-fix', (e) => { if (!busy()) send(e.detail.text); });
  document.addEventListener('viz-toast', (e) => toast(e.detail));
  document.addEventListener('entity-ask', (e) => { if (!busy()) send(e.detail.text); });
  document.addEventListener('skill-submit', (e) => { if (!busy()) send(e.detail.text, { skillRun: e.detail.runId, skillAnswers: e.detail.answers }); });
  // what the Library and side panel need from the app
  window.App = { currentTab: () => settingsTab, refreshServers: (list) => { S.servers = list; }, $, $$, esc, icon, ICONS, toast, API, S, confirmDialog, closeMenus, loadChats, loadLib, updateChip, newChat, openChat, copyText, download, renderSettings, isMobile };
  init();
})();
