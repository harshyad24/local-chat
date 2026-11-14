'use strict';

/* Settings → Connections: MCP servers, n8n, LibreOffice. */
const Connections = (() => {
  const A = window.App;
  const { $, $$, esc, icon, toast, API, S } = A;

  let data = { servers: [], libreoffice: false, n8n: { configured: false, templates: [] } };
  let form = null; // server being added/edited (a config object) or null
  let created = null; // result of the last n8n template creation
  let body = null;

  const STATUS = { ready: ['Connected', 'ok'], error: ['Error', 'bad'], connecting: ['Connecting…', 'wait'], off: ['Off', 'off'] };

  const lines = (obj, sep) => Object.entries(obj || {}).map(([k, v]) => `${k}${sep}${v}`).join('\n');
  const parseLines = (text, sep) => {
    const out = {};
    for (const ln of String(text || '').split('\n')) {
      const i = ln.indexOf(sep);
      if (i > 0) out[ln.slice(0, i).trim()] = ln.slice(i + sep.length).trim();
    }
    return out;
  };
  // arguments: one per line (so spaces inside a path stay intact)
  const parseArgs = (text) => String(text || '').split('\n').map((x) => x.trim()).filter(Boolean);

  async function load() {
    try { data = await API.get('/api/connections'); } catch (e) { toast('Could not load connections: ' + e.message); }
    await PluginsUI.load();
    A.refreshServers && A.refreshServers(data.servers);
  }

  function serverCard(sv) {
    const [label, cls] = STATUS[sv.status] || STATUS.connecting;
    const where = sv.transport === 'stdio' ? `${esc(sv.command)} ${esc((sv.args || []).join(' '))}` : esc(sv.url);
    const tools = (sv.tools || [])
      .map((t) => `<li><span class="mem-text"><b>${esc(t.name)}</b> <span class="muted small">${esc(t.description.slice(0, 120))}</span></span>` +
        (t.read_only ? '<span class="mem-src">read-only</span>' : `<label class="small muted"><input type="checkbox" data-allow="${esc(t.name)}"${t.allowed ? ' checked' : ''}> allow without asking</label>`) + '</li>')
      .join('');
    return `<div class="lib-card conn" data-name="${esc(sv.name)}"><div class="lib-main"><b>${esc(sv.name)}</b>` +
      `<span class="muted small mono">${where}</span>` +
      (sv.error ? `<span class="conn-err small">${esc(sv.error)}</span>` : '') + '</div>' +
      `<span class="chip ${cls}">${label}${sv.status === 'ready' ? ` · ${sv.tools.length} tool${sv.tools.length === 1 ? '' : 's'}` : ''}</span>` +
      `<label class="switch"><input type="checkbox" data-c="enabled"${sv.enabled ? ' checked' : ''}><i></i></label>` +
      `<div class="lib-actions"><button class="btn sm" data-c="restart">Restart</button><button class="btn sm" data-c="edit">Edit</button><button class="btn sm danger-outline" data-c="remove">Remove</button></div>` +
      (tools ? `<details class="conn-tools"><summary class="muted small">Tools (${sv.tools.length})</summary><ul class="mem-list">${tools}</ul></details>` : '') + '</div>';
  }

  function formHTML(f) {
    const stdio = f.transport === 'stdio';
    return `<div class="section-title">${f.isNew ? 'Add server' : 'Edit server'}</div>` +
      `<div class="field stack"><div class="lbl"><b>Name</b></div><input id="c-name" value="${esc(f.name)}" maxlength="40" placeholder="e.g. Files"></div>` +
      `<div class="field"><div class="lbl"><b>Type</b><span>${stdio ? 'Starts a program on this computer.' : 'Connects to a server by URL.'}</span></div>` +
      `<select id="c-transport"><option value="stdio"${stdio ? ' selected' : ''}>Local command</option><option value="http"${f.transport === 'http' ? ' selected' : ''}>Streamable HTTP</option><option value="sse"${f.transport === 'sse' ? ' selected' : ''}>SSE</option></select></div>` +
      (stdio
        ? `<div class="field stack"><div class="lbl"><b>Command</b><span>e.g. npx, uvx, python</span></div><input id="c-command" value="${esc(f.command)}"></div>` +
          `<div class="field stack"><div class="lbl"><b>Arguments</b><span>One per line.</span></div><textarea id="c-args" rows="3">${esc((f.args || []).join('\n'))}</textarea></div>` +
          `<div class="field stack"><div class="lbl"><b>Environment</b><span>KEY=value, one per line. Values are hidden after saving.</span></div><textarea id="c-env" rows="2">${esc(lines(f.env, '='))}</textarea></div>`
        : `<div class="field stack"><div class="lbl"><b>URL</b></div><input id="c-url" value="${esc(f.url)}" placeholder="http://localhost:5678/mcp/..."></div>` +
          `<div class="field stack"><div class="lbl"><b>Headers</b><span>Name: value, one per line (e.g. Authorization: Bearer …). Hidden after saving.</span></div><textarea id="c-headers" rows="2">${esc(lines(f.headers, ': '))}</textarea></div>`) +
      `<p class="muted small">⚠ A local command runs on your computer with your permissions. Only add servers you trust.</p>` +
      `<div class="btn-row"><button class="btn primary" data-c="save">Save and connect</button><button class="btn" data-c="cancel">Cancel</button></div>`;
  }

  function n8nHTML() {
    const key = S.settings.n8n_api_key;
    const tpl = (data.n8n.templates || []).map((t) =>
      `<div class="lib-card" data-tpl="${esc(t.id)}"><div class="lib-main"><b>${esc(t.title)}</b><span class="muted">${esc(t.description)}</span><span class="muted small">Needs ${esc(t.needs)} in n8n</span></div>` +
      (t.fields || []).map((f) => `<label class="small muted"><input type="checkbox" data-field="${esc(f.key)}"> ${esc(f.label)}</label>`).join('') +
      `<button class="btn sm primary" data-n="create">Create draft in n8n</button></div>`).join('');
    const made = created
      ? `<div class="lib-card ok-card"><div class="lib-main"><b>Created “${esc(created.name)}” (inactive)</b>` +
        `<span class="small">1. <a href="${esc(created.url)}" target="_blank" rel="noopener">Open it in n8n</a>, attach ${esc(created.needs)}, check each node, switch it on.</span>` +
        `<span class="small">2. In the <b>MCP Server</b> node copy the production URL (it should look like <code>${esc(created.mcp_url)}</code>), then add it here.</span></div>` +
        `<button class="btn sm" data-n="addmcp" data-url="${esc(created.mcp_url)}" data-name="${esc(created.name)}">Add as MCP server</button></div>`
      : '';
    return `<div class="section-title">n8n</div>` +
      `<p class="muted small">n8n connects mail, calendar and hundreds of other apps. Your accounts stay in n8n; this app only talks to it. Not installed? Run <code>npx n8n</code>, open http://localhost:5678, create an account, then n8n → Settings → n8n API → create an API key.</p>` +
      `<div class="field"><div class="lbl"><b>n8n URL</b></div><input type="text" data-k="n8n_url" value="${esc(S.settings.n8n_url || '')}"></div>` +
      `<div class="field"><div class="lbl"><b>API key</b><span>Used to create draft workflows.</span></div><input type="password" data-k="n8n_api_key" value="${esc(key || '')}" autocomplete="off"></div>` +
      `<div class="btn-row"><button class="btn" data-n="test">Test connection</button><span id="n8n-result" class="small"></span></div>` +
      `<div class="section-title">Starter workflows</div>` +
      `<p class="muted small">Created as <b>drafts</b>: nothing runs until you attach your accounts in n8n and switch them on.</p>` + made + tpl;
  }

  async function render(target) {
    PluginsUI.reset();
    body = target;
    body.innerHTML = '<p class="muted">Loading…</p>';
    await load();
    paint();
  }

  function paint() {
    if (!body) return;
    const list = data.servers.map(serverCard).join('') || '<p class="muted">No servers yet.</p>';
    const advanced =
      `<div class="section-title">MCP servers</div>` +
      `<p class="muted small">MCP lets the assistant use outside tools: files, databases, n8n workflows and more. Each tool that can change something asks for your OK first. Turn servers on or off per chat from the <b>+</b> menu.</p>` +
      list +
      (form ? formHTML(form) :
        `<div class="btn-row"><button class="btn primary" data-c="add">Add server</button>` +
        `<button class="btn" data-c="preset-files">Add a folder (Filesystem)</button></div>`) +
      n8nHTML() +
      `<div class="section-title">LibreOffice</div>` +
      `<p class="small">${data.libreoffice ? '✓ Found. Ask the assistant to convert a document (docx → pdf, xlsx → csv, …) — it appears when you attach a file or mention converting.' : 'Not found on this computer, so document conversion is off. Install LibreOffice to enable it.'}</p>`;
    body.innerHTML = PluginsUI.html() + (PluginsUI.isOpen() ? '' :
      `<details class="pl-adv"${form ? ' open' : ''}><summary>Advanced: MCP servers, n8n, LibreOffice</summary>${advanced}</details>`);
  }

  async function saveServers(servers) {
    try { data = await API.put('/api/connections/servers', { servers }); } catch (e) { toast(e.message, 4000); return false; }
    A.refreshServers && A.refreshServers(data.servers);
    return true;
  }

  const raw = (sv) => ({ name: sv.name, transport: sv.transport, command: sv.command, args: sv.args, env: sv.env, url: sv.url, headers: sv.headers, enabled: sv.enabled, always_allow: sv.always_allow || (sv.tools || []).filter((t) => t.allowed && !t.read_only).map((t) => t.name) });

  async function click(e) {
    const t = e.target.closest('[data-c],[data-n],[data-p]');
    if (!t) return;
    if (t.dataset.p) { await PluginsUI.click(e, paint, async () => { await load(); }); return; }
    const card = t.closest('.conn');
    const name = card && card.dataset.name;
    const c = t.dataset.c;
    const n = t.dataset.n;
    try {
      if (c === 'add') { form = { isNew: true, name: '', transport: 'stdio', command: '', args: [], env: {}, url: '', headers: {}, enabled: true }; paint(); }
      else if (c === 'preset-files') {
        const dir = prompt('Folder the assistant may read and write (full path):');
        if (!dir) return;
        form = { isNew: true, name: 'Files', transport: 'stdio', command: 'npx', args: ['-y', '@modelcontextprotocol/server-filesystem', dir.trim()], env: {}, url: '', headers: {}, enabled: true };
        paint();
      }
      else if (c === 'cancel') { form = null; paint(); }
      else if (c === 'edit') { form = { ...data.servers.find((x) => x.name === name), isNew: false, orig: name }; paint(); }
      else if (c === 'remove') {
        if (!(await A.confirmDialog('Remove this server?', 'Its tools will no longer be available.', 'Remove'))) return;
        await saveServers(data.servers.filter((x) => x.name !== name).map(raw));
        paint();
      }
      else if (c === 'restart') { t.textContent = 'Restarting…'; data = await API.post(`/api/connections/servers/${encodeURIComponent(name)}/restart`); paint(); }
      else if (c === 'save') {
        const f = { ...form };
        f.name = $('#c-name').value.trim();
        f.transport = $('#c-transport').value;
        if (!f.name) { toast('Give it a name'); return; }
        if (f.transport === 'stdio') { f.command = $('#c-command').value.trim(); f.args = parseArgs($('#c-args').value); f.env = parseLines($('#c-env').value, '='); f.url = ''; f.headers = {}; }
        else { f.url = $('#c-url').value.trim(); f.headers = parseLines($('#c-headers').value, ':'); f.command = ''; f.args = []; f.env = {}; }
        const others = data.servers.filter((x) => x.name !== (f.orig || f.name)).map(raw);
        if (others.some((x) => x.name.toLowerCase() === f.name.toLowerCase())) { toast('A server with that name exists'); return; }
        t.textContent = 'Connecting…';
        if (await saveServers([...others, raw(f)])) { form = null; created = created && null; }
        paint();
      }
      else if (n === 'test') {
        const out = $('#n8n-result');
        out.textContent = 'Testing…';
        try { await API.put('/api/settings', { n8n_url: S.settings.n8n_url, n8n_api_key: S.settings.n8n_api_key }); const r = await API.post('/api/n8n/test'); out.className = 'small test-result ok'; out.textContent = `Connected (${r.workflows >= 0 ? 'API works' : ''}).`; await load(); }
        catch (err) { out.className = 'small test-result bad'; out.textContent = err.message; }
      }
      else if (n === 'create') {
        const card2 = t.closest('[data-tpl]');
        const allow = !!card2.querySelector('[data-field=allow_send]:checked');
        t.disabled = true;
        t.textContent = 'Creating…';
        try {
          await API.put('/api/settings', { n8n_url: S.settings.n8n_url, n8n_api_key: S.settings.n8n_api_key });
          created = await API.post(`/api/n8n/templates/${card2.dataset.tpl}`, { allow_send: allow });
        } catch (err) { toast(err.message, 5000); }
        paint();
      }
      else if (n === 'addmcp') {
        form = { isNew: true, name: t.dataset.name.replace(/^Local Chat · /, '').slice(0, 40), transport: 'http', command: '', args: [], env: {}, url: t.dataset.url, headers: {}, enabled: true };
        paint();
        body.scrollTop = 0;
      }
    } catch (err) { toast(err.message, 4000); }
  }

  async function change(e) {
    const t = e.target;
    const card = t.closest('.conn');
    if (!card) return;
    const name = card.dataset.name;
    try {
      if (t.dataset.c === 'enabled') { data = await API.post(`/api/connections/servers/${encodeURIComponent(name)}/enabled`, { enabled: t.checked }); A.refreshServers && A.refreshServers(data.servers); paint(); }
      else if (t.dataset.allow) { data = await API.post(`/api/connections/servers/${encodeURIComponent(name)}/allow`, { tool: t.dataset.allow, allow: t.checked }); }
    } catch (err) { toast(err.message); }
  }

  function attach() {
    const el = $('#settings-body');
    if (el._connWired) return;
    el._connWired = true;
    el.addEventListener('click', (e) => { if (body === el && A.currentTab() === 'connections') click(e); });
    el.addEventListener('change', (e) => { if (body === el && A.currentTab() === 'connections') { change(e); if (e.target.id === 'c-transport' && form) { form = { ...form, transport: e.target.value }; syncForm(); } } });
  }
  function syncForm() { // keep typed values when the type changes
    const v = (id) => (document.getElementById(id) ? document.getElementById(id).value : null);
    form.name = v('c-name') ?? form.name;
    paint();
  }

  return { render: (target) => { attach(); return render(target); } };
})();
window.Connections = Connections;
