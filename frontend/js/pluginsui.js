'use strict';

/* Settings → Connections → Plugins: a grid of cards; opening one shows what it can and never does, numbered setup steps with links,
   the form, and "Test & connect". Security switches (Pause, ask-before-use) and the activity log sit under the grid. */
const PluginsUI = (() => {
  const A = window.App;
  const { esc, toast, API } = A;

  let ps = { plugins: [], paused: false, consent: true, protected: false };
  let view = null; // id of the plugin whose setup/manage page is open
  let values = {}; // what is typed in the open form
  let result = null; // {ok, text} shown under the form
  let busy = false;
  let pairCode = null; // {code, minutes} shown after 'Generate pairing code'
  let pairTimer = null;

  const svg = (d) => `<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
  const ICON = {
    mail: svg('<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>'),
    calendar: svg('<rect x="3" y="4.5" width="18" height="16" rx="2"/><path d="M3 9.5h18M8 3v3M16 3v3"/>'),
    folder: svg('<path d="M3 6.5A1.5 1.5 0 0 1 4.5 5H9l2 2.5h8.5A1.5 1.5 0 0 1 21 9v8.5a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z"/>'),
    chat: svg('<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20.5l1.4-4.6A8 8 0 1 1 21 12z"/><path d="M9 10h6M9 13.5h4"/>'),
    telegram: '<svg viewBox="0 0 24 24" width="28" height="28" aria-hidden="true"><circle cx="12" cy="12" r="11" fill="#229ED9"/>' +
      '<path d="M5.4 11.7l11.9-4.6c.6-.2 1 .1.9.9l-2 9.5c-.1.7-.6.9-1.2.5l-3.3-2.4-1.6 1.5c-.2.2-.3.3-.6.3l.2-3.4 6.2-5.6c.3-.2-.1-.4-.4-.2l-7.6 4.8-3.3-1c-.7-.2-.7-.7.2-1z" fill="#fff"/></svg>',
    shield: svg('<path d="M12 3 5 6v5.5c0 4.2 2.9 7.4 7 9.5 4.1-2.1 7-5.3 7-9.5V6z"/><path d="m9 12 2 2 4-4"/>'),
  };
  const brand = (p) => (p.icon === 'telegram' ? ' brand' : '');
  const rich = (t) => esc(t).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
  const when = (ts) => new Date(ts * 1000).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' });

  async function load() {
    try { ps = await API.get('/api/plugins'); } catch (e) { /* the screen shows what it has */ }
    return ps;
  }
  const plugin = (id) => ps.plugins.find((p) => p.id === id);
  const isOpen = () => !!view;

  function chip(p) {
    return p.installed ? `<span class="chip ${p.enabled ? 'ok' : 'off'}">${p.enabled ? 'Connected' : 'Off'}</span>` : '<span class="chip off">Not connected</span>';
  }

  function gridHTML() {
    const cards = ps.plugins.map((p) =>
      `<button class="pl-card" data-p="open" data-id="${esc(p.id)}"><span class="pl-ico${brand(p)}">${ICON[p.icon] || ''}</span>` +
      `<span class="pl-name">${esc(p.name)}</span><span class="pl-sum">${esc(p.summary)}</span>` +
      `<span class="pl-foot">${chip(p)}<span class="pl-go">${p.installed ? 'Manage' : 'Connect'} →</span></span></button>`).join('');
    const log = ps.plugins.flatMap((p) => p.activity || []).sort((a, b) => b.ts - a.ts).slice(0, 12)
      .map((a) => `<li><span class="muted small">${esc(when(a.ts))}</span> <b>${esc(a.server)}</b> · ${esc(a.tool)}${a.args ? ` <span class="muted small">(${esc(a.args)})</span>` : ''}${a.ok ? '' : ' <span class="conn-err small">failed</span>'}</li>`).join('');
    return `<div class="section-title">Plugins</div>` +
      `<p class="muted small">Connect the assistant to your own mail, calendar and notes. Plugins are <b>read-only</b> and can only look. The one exception is <b>Telegram</b>, which can send to chats you paired, and only after you approve each message.</p>` +
      `<div class="pl-security">${ICON.shield}<div class="pl-sec-text"><b>Security</b><span class="muted small">Read-only (Telegram sends only with your approval) · ` +
      `${ps.protected ? 'passwords encrypted with your Windows login' : 'passwords stay on this computer'} · asks before first use in each chat</span></div>` +
      `<div class="pl-sec-btns"><button class="btn sm ${ps.paused ? 'primary' : ''}" data-p="pause">${ps.paused ? 'Paused · Resume' : 'Pause all'}</button>` +
      `<button class="btn sm" data-p="consent" title="Ask before the assistant first uses a plugin in a chat">Ask first: ${ps.consent ? 'On' : 'Off'}</button></div></div>` +
      (ps.paused ? '<p class="pl-paused small">All plugins are paused: the assistant cannot see them until you resume.</p>' : '') +
      `<div class="pl-grid">${cards}</div>` +
      `<details class="pl-log"><summary class="muted small">Recent plugin activity</summary>` +
      `<ul class="mem-list">${log || '<li class="muted small">Nothing yet. Every use of a plugin is listed here (what was asked, never what was read).</li>'}</ul></details>`;
  }

  function detailHTML(p) {
    const fields = p.fields.map((f) => {
      const list = (f.options || []).length ? ` list="dl-${esc(f.key)}"` : '';
      return `<div class="field stack"><div class="lbl"><b>${esc(f.label)}</b>${f.secret && p.installed ? '<span>Saved. Leave blank to keep it.</span>' : ''}</div>` +
        `<div class="pl-input"><input data-pf="${esc(f.key)}" type="${f.secret ? 'password' : 'text'}" autocomplete="off" spellcheck="false"${list} value="${esc(values[f.key] || '')}" placeholder="${esc(f.placeholder || f.default || '')}">` +
        (f.secret ? '<button class="btn sm" type="button" data-p="reveal">Show</button>' : '') + '</div>' +
        ((f.options || []).length ? `<datalist id="dl-${esc(f.key)}">${f.options.map((o) => `<option value="${esc(o)}">`).join('')}</datalist>` : '') + '</div>';
    }).join('');
    const steps = p.steps.map((s) => `<li><span>${rich(s.text)}</span>${s.link ? `<a class="btn sm" href="${esc(s.link.url)}" target="_blank" rel="noopener noreferrer">${esc(s.link.label)} ↗</a>` : ''}</li>`).join('');
    const act = (p.activity || []).map((a) => `<li><span class="muted small">${esc(when(a.ts))}</span> ${esc(a.tool)}${a.args ? ` <span class="muted small">(${esc(a.args)})</span>` : ''}${a.ok ? '' : ' <span class="conn-err small">failed</span>'}</li>`).join('');
    return `<button class="btn sm" data-p="back">← All plugins</button>` +
      `<div class="pl-title"><span class="pl-ico big${brand(p)}">${ICON[p.icon] || ''}</span><div><h3>${esc(p.name)}</h3><span class="muted">${esc(p.description)}</span></div>${chip(p)}</div>` +
      `<div class="pl-two"><div><h4>It can</h4><ul>${p.can.map((x) => `<li class="yes">${esc(x)}</li>`).join('')}</ul></div>` +
      `<div><h4>It never</h4><ul>${p.cannot.map((x) => `<li class="no">${esc(x)}</li>`).join('')}</ul></div></div>` +
      `<h4>${p.installed ? 'Change the connection' : `Set up in ${p.steps.length} steps`}</h4><ol class="pl-steps">${steps}</ol>` +
      `<div class="pl-form">${fields}</div>` +
      `<div class="btn-row"><button class="btn primary" data-p="save" data-id="${esc(p.id)}"${busy ? ' disabled' : ''}>${busy ? 'Testing…' : 'Test &amp; connect'}</button>` +
      (p.installed ? `<button class="btn" data-p="test" data-id="${esc(p.id)}"${busy ? ' disabled' : ''}>Test again</button><button class="btn danger-outline" data-p="remove" data-id="${esc(p.id)}">Disconnect</button>` : '') + '</div>' +
      (result ? `<div class="pl-result ${result.ok ? 'ok' : 'bad'}">${result.ok ? '✓ ' : '✗ '}${rich(result.text)}</div>` : '') +
      (p.note ? `<p class="muted small">${esc(p.note)}</p>` : '') +
      (p.id === 'telegram' && p.installed ? pairingHTML(p) : '') +
      (p.installed ? `<h4>Recent activity</h4><ul class="mem-list">${act || '<li class="muted small">Not used yet.</li>'}</ul>` : '') +
      `<p class="muted small pl-fine">Your details are tested once, then stored on this computer${ps.protected ? ', encrypted with your Windows login' : ''}. “Disconnect” deletes them. You can also revoke the app password in your Google account at any time.</p>`;
  }

  function pairingHTML(p) {
    const list = (p.paired || []).map((c) => `<li><span><b>${esc(c.title)}</b> <span class="muted small">${esc(c.type || 'chat')} · id ${esc(c.id)}</span></span><button class="btn sm danger-outline" data-p="unpair" data-chat="${esc(c.id)}">Unpair</button></li>`).join('');
    const code = pairCode
      ? `<div class="pl-code"><span class="pl-code-n">${esc(pairCode.code)}</span><span>In Telegram open ${p.bot ? '<b>@' + esc(p.bot) + '</b>' : 'your bot'} and send <code>/pair ${esc(pairCode.code)}</code>. ` +
        `It works once and expires in ${pairCode.minutes} minutes. This page updates when the chat is paired.</span></div>` : '';
    return `<h4>Paired chats</h4><p class="muted small">Only paired chats can be read, and only they can ever receive a message (each one needs your approval).</p>` +
      `<ul class="mem-list pl-paired">${list || '<li class="muted small">None yet.</li>'}</ul>` +
      `<div class="btn-row"><button class="btn primary" data-p="pair">Generate pairing code</button></div>${code}`;
  }

  function html() {
    const p = view && plugin(view);
    return p ? detailHTML(p) : gridHTML();
  }

  async function click(e, repaint, refreshAll) {
    const t = e.target.closest('[data-p]');
    if (!t) return;
    const act = t.dataset.p;
    const id = t.dataset.id;
    try {
      if (act === 'open') {
        const p = plugin(id);
        values = Object.fromEntries(p.fields.map((f) => [f.key, f.secret ? '' : (p.values[f.key] || f.default || '')]));
        result = null; view = id; repaint();
      } else if (act === 'back') { view = null; result = null; repaint(); }
      else if (act === 'reveal') {
        const input = t.parentElement.querySelector('input');
        input.type = input.type === 'password' ? 'text' : 'password';
        t.textContent = input.type === 'password' ? 'Show' : 'Hide';
      } else if (act === 'pause') {
        await API.put('/api/settings', { plugins_paused: !ps.paused }); await load(); await refreshAll(); repaint();
      } else if (act === 'consent') {
        await API.put('/api/settings', { plugin_consent: !ps.consent }); await load(); repaint();
      } else if (act === 'save') {
        document.querySelectorAll('[data-pf]').forEach((i) => { values[i.dataset.pf] = i.value; });
        busy = true; result = null; repaint();
        try {
          const r = await API.post(`/api/plugins/${encodeURIComponent(id)}/install`, { values });
          ps = r; result = { ok: true, text: r.message }; values = Object.fromEntries(plugin(id).fields.map((f) => [f.key, f.secret ? '' : (plugin(id).values[f.key] || '')]));
          await refreshAll();
        } catch (err) { result = { ok: false, text: err.message }; }
        busy = false; repaint();
      } else if (act === 'test') {
        busy = true; result = null; repaint();
        try { const r = await API.post(`/api/plugins/${encodeURIComponent(id)}/test`); result = { ok: r.ok, text: r.message }; }
        catch (err) { result = { ok: false, text: err.message }; }
        busy = false; repaint();
      } else if (act === 'pair') {
        pairCode = await API.post('/api/plugins/telegram/pair-code');
        const before = (plugin('telegram').paired || []).length;
        clearInterval(pairTimer);
        let tries = 0;
        pairTimer = setInterval(async () => {
          tries += 1;
          try {
            await load();
            if ((plugin('telegram').paired || []).length !== before) { clearInterval(pairTimer); pairCode = null; toast('Chat paired', 3500); repaint(); }
          } catch (e) { /* keep waiting */ }
          if (tries > 100) clearInterval(pairTimer);
        }, 3000);
        repaint();
      } else if (act === 'unpair') {
        if (!(await A.confirmDialog('Unpair this chat?', 'The assistant can no longer read it or send to it.', 'Unpair'))) return;
        await API.del(`/api/plugins/telegram/paired/${encodeURIComponent(t.dataset.chat)}`); await load(); repaint();
      } else if (act === 'remove') {
        if (!(await A.confirmDialog('Disconnect this plugin?', 'Its saved details are deleted from this computer.', 'Disconnect'))) return;
        ps = await API.del(`/api/plugins/${encodeURIComponent(id)}`); view = null; result = null; await refreshAll(); repaint();
      }
    } catch (err) { toast(err.message, 5000); busy = false; repaint(); }
  }

  return { load, html, click, isOpen, reset: () => { view = null; result = null; pairCode = null; clearInterval(pairTimer); } };
})();
window.PluginsUI = PluginsUI;
