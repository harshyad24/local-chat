'use strict';

/* Library dialog: Memory, Assistants, Projects. */
const Library = (() => {
  const A = window.App;
  const { $, $$, esc, icon, toast, API, S } = A;
  const dlg = $('#dlg-library');
  const TABS = [['memory', 'Memory'], ['assistants', 'Assistants'], ['projects', 'Projects'], ['skills', 'Skill preferences']];
  const STARTERS = [
    { name: 'Code reviewer', description: 'Finds bugs and suggests fixes', prompt: 'You are a careful senior code reviewer. Read the code, point out bugs, security problems and unclear parts, in that order of importance, and show a corrected version of anything you flag. Be specific and brief.' },
    { name: 'Study coach', description: 'Explains simply, then quizzes you', prompt: 'You are a patient tutor. Explain the idea in plain words with one concrete example, then ask the student two short questions to check understanding. Correct mistakes kindly.' },
    { name: 'Editor', description: 'Tightens writing without changing your voice', prompt: 'You are a sharp editor. Improve clarity and flow while keeping the author\'s voice. Return the edited text first, then a short list of the most important changes.' },
    { name: 'Data analyst', description: 'Turns numbers into charts and insight', prompt: 'You are a data analyst. When the user gives data, summarise what stands out, build charts or a dashboard with the built-in tools, and state any caveats about the data.' },
  ];

  let tab = 'memory';
  let editing = null; // {kind, id} while a form is open
  let memories = [];
  let editMem = null;

  const body = () => $('#lib-body');

  async function setSetting(k, v) {
    S.settings[k] = v;
    try { S.settings = await API.put('/api/settings', { [k]: v }); } catch (e) { toast('Could not save: ' + e.message); }
  }

  const sw = (k, label, hint) =>
    `<div class="field"><div class="lbl"><b>${label}</b><span>${hint}</span></div><label class="switch"><input type="checkbox" data-s="${k}"${S.settings[k] ? ' checked' : ''}><i></i></label></div>`;

  /* ------------------------------------------------------------ memory */
  async function renderMemory() {
    try { memories = await API.get('/api/memories'); } catch (_) { memories = []; }
    const list = memories
      .map((m) =>
        editMem === m.id
          ? `<li data-id="${m.id}"><input class="mem-edit" value="${esc(m.text)}" maxlength="300"><button class="btn primary sm" data-m="save">Save</button><button class="btn sm" data-m="cancel">Cancel</button></li>`
          : `<li data-id="${m.id}"><span class="mem-text">${esc(m.text)}</span>${m.source !== 'user' ? `<span class="mem-src">${esc(m.source)}</span>` : ''}<button class="icon-btn sm" data-m="edit" title="Edit" aria-label="Edit">${icon('edit', 15)}</button><button class="icon-btn sm" data-m="del" title="Forget" aria-label="Forget">${icon('x', 15)}</button></li>`
      )
      .join('');
    body().innerHTML =
      sw('memory_enabled', 'Use memory', 'Saved facts are added to every chat, so the model knows you. Say “remember that …” in any chat to save one, or “forget …” to remove it.') +
      sw('memory_auto', 'Learn automatically', 'After each reply the model looks for lasting facts you stated about yourself. Costs one short extra call per message; you can review and delete everything below.') +
      `<div class="section-title">Remembered (${memories.length})</div>` +
      `<div class="mem-add"><input id="mem-new" placeholder="Add something to remember, e.g. I prefer metric units" maxlength="300"><button class="btn primary" id="mem-add">Add</button></div>` +
      (memories.length ? `<ul class="mem-list">${list}</ul><div class="btn-row"><button class="btn danger-outline" id="mem-clear">Forget everything</button></div>` : '<p class="muted">Nothing yet. Try telling the model: “Remember that I work in Python and prefer short answers.”</p>') +
      `<p class="muted small">Stored only on this computer. Passwords and keys are never saved.</p>`;
  }

  async function memoryClick(e) {
    const t = e.target;
    if (t.id === 'mem-add') {
      const inp = $('#mem-new');
      if (!inp.value.trim()) return;
      try { await API.post('/api/memories', { text: inp.value }); inp.value = ''; renderMemory(); } catch (err) { toast(err.message); }
      return;
    }
    if (t.id === 'mem-clear') {
      if (!(await A.confirmDialog('Forget everything?', 'All remembered facts will be deleted.', 'Forget everything'))) return;
      await API.del('/api/memories');
      renderMemory();
      return;
    }
    const b = t.closest('[data-m]');
    if (!b) return;
    const li = b.closest('li');
    const id = Number(li.dataset.id);
    if (b.dataset.m === 'del') { await API.del(`/api/memories/${id}`); renderMemory(); }
    else if (b.dataset.m === 'edit') { editMem = id; renderMemory(); }
    else if (b.dataset.m === 'cancel') { editMem = null; renderMemory(); }
    else if (b.dataset.m === 'save') {
      try { await API.put(`/api/memories/${id}`, { text: $('.mem-edit', li).value }); editMem = null; renderMemory(); } catch (err) { toast(err.message); }
    }
  }

  /* -------------------------------------------------------- assistants */
  function modelOptions(sel) {
    return `<option value="">(use the chat’s model)</option>` + S.models.map((m) => `<option value="${esc(m.name)}"${m.name === sel ? ' selected' : ''}>${esc(m.name)}</option>`).join('');
  }

  function renderAssistants() {
    const b = body();
    if (editing && editing.kind === 'assistant') {
      const a = S.lib.assistants.find((x) => x.id === editing.id) || { name: '', description: '', prompt: '', model: '' };
      b.innerHTML =
        `<div class="section-title">${editing.id ? 'Edit assistant' : 'New assistant'}</div>` +
        `<div class="field stack"><div class="lbl"><b>Name</b></div><input id="f-name" value="${esc(a.name)}" maxlength="80" placeholder="e.g. Study coach"></div>` +
        `<div class="field stack"><div class="lbl"><b>Description</b><span>Shown in the list.</span></div><input id="f-desc" value="${esc(a.description)}" maxlength="200"></div>` +
        `<div class="field stack"><div class="lbl"><b>Instructions</b><span>How it should behave. This is added to the system prompt of every chat that uses it.</span></div><textarea id="f-prompt" rows="9" placeholder="You are…">${esc(a.prompt)}</textarea></div>` +
        `<div class="field"><div class="lbl"><b>Model</b><span>Optional. New chats with this assistant start on it.</span></div><select id="f-model">${modelOptions(a.model)}</select></div>` +
        `<div class="btn-row"><button class="btn primary" data-a="save">Save</button><button class="btn" data-a="cancel">Cancel</button></div>`;
      return;
    }
    const cards = S.lib.assistants
      .map((a) =>
        `<div class="lib-card" data-id="${a.id}"><div class="lib-main"><b>${esc(a.name)}</b>${a.description ? `<span class="muted">${esc(a.description)}</span>` : ''}${a.model ? `<span class="muted small">${esc(a.model)}</span>` : ''}</div>` +
        `<div class="lib-actions"><button class="btn sm primary" data-a="chat">New chat</button>${S.current ? '<button class="btn sm" data-a="use">Use here</button>' : ''}<button class="btn sm" data-a="edit">Edit</button><button class="btn sm danger-outline" data-a="del">Delete</button></div></div>`
      )
      .join('');
    const starters = STARTERS.filter((s) => !S.lib.assistants.some((a) => a.name === s.name))
      .map((s, i) => `<button class="btn sm" data-a="starter" data-i="${STARTERS.indexOf(s)}">+ ${esc(s.name)}</button>`)
      .join('');
    b.innerHTML =
      `<p class="muted">A custom assistant is a saved set of instructions (and optionally a model). Start a chat with it, or switch the current chat to it.</p>` +
      `<div class="btn-row"><button class="btn primary" data-a="new">New assistant</button></div>` +
      (cards || '<p class="muted">No assistants yet.</p>') +
      (starters ? `<div class="section-title">Starters</div><div class="btn-row">${starters}</div>` : '');
  }

  async function assistantClick(e) {
    const btn = e.target.closest('[data-a]');
    if (!btn) return;
    const act = btn.dataset.a;
    const id = (btn.closest('.lib-card') || {}).dataset ? btn.closest('.lib-card').dataset.id : null;
    try {
      if (act === 'new') { editing = { kind: 'assistant', id: null }; renderAssistants(); }
      else if (act === 'edit') { editing = { kind: 'assistant', id }; renderAssistants(); }
      else if (act === 'cancel') { editing = null; renderAssistants(); }
      else if (act === 'starter') {
        await API.post('/api/assistants', STARTERS[Number(btn.dataset.i)]);
        await A.loadLib();
        renderAssistants();
      } else if (act === 'save') {
        const data = { name: $('#f-name').value, description: $('#f-desc').value, prompt: $('#f-prompt').value, model: $('#f-model').value || null };
        if (!data.name.trim()) { toast('Give it a name'); return; }
        if (editing.id) await API.put(`/api/assistants/${editing.id}`, data); else await API.post('/api/assistants', data);
        editing = null;
        await A.loadLib();
        renderAssistants();
      } else if (act === 'del') {
        if (!(await A.confirmDialog('Delete assistant?', 'Chats that used it keep their messages.', 'Delete'))) return;
        await API.del(`/api/assistants/${id}`);
        if (S.current && S.current.assistant_id === id) S.current.assistant_id = null;
        await A.loadLib();
        renderAssistants();
      } else if (act === 'chat') { dlg.close(); A.newChat({ assistant: id }); }
      else if (act === 'use') {
        S.current = await API.patch(`/api/chats/${S.current.id}`, { assistant_id: id });
        A.updateChip();
        toast('This chat now uses that assistant');
        dlg.close();
      }
    } catch (err) { toast(err.message); }
  }

  /* ---------------------------------------------------------- projects */
  let projectFiles = [];

  async function renderProjects() {
    const b = body();
    if (editing && editing.kind === 'project') {
      const p = editing.id ? await API.get(`/api/projects/${editing.id}`).catch(() => null) : null;
      projectFiles = p ? p.files : [];
      const prj = p || { name: '', instructions: '' };
      b.innerHTML =
        `<div class="section-title">${editing.id ? 'Edit project' : 'New project'}</div>` +
        `<div class="field stack"><div class="lbl"><b>Name</b></div><input id="f-name" value="${esc(prj.name)}" maxlength="80" placeholder="e.g. Thesis"></div>` +
        `<div class="field stack"><div class="lbl"><b>Instructions</b><span>Applied to every chat in this project.</span></div><textarea id="f-instr" rows="7" placeholder="e.g. Use APA citations. The audience is first-year students.">${esc(prj.instructions)}</textarea></div>` +
        (editing.id
          ? `<div class="section-title">Files</div><p class="muted small">The model can read these in every chat of the project.</p>` +
            `<ul class="mem-list">${projectFiles.map((f) => `<li data-fid="${esc(f.id)}"><span class="mem-text">${esc(f.name)}</span><button class="icon-btn sm" data-p="rmfile" aria-label="Remove">${icon('x', 15)}</button></li>`).join('') || '<li class="muted">No files yet.</li>'}</ul>` +
            `<div class="btn-row"><button class="btn" data-p="addfile">Add files…</button><input type="file" id="p-file" multiple hidden></div>`
          : '<p class="muted small">Save the project first, then add files.</p>') +
        `<div class="btn-row"><button class="btn primary" data-p="save">Save</button><button class="btn" data-p="cancel">Back</button></div>`;
      return;
    }
    const cards = S.lib.projects
      .map((p) =>
        `<div class="lib-card" data-id="${p.id}"><div class="lib-main"><b>${esc(p.name)}</b><span class="muted small">${p.chat_count || 0} chat${p.chat_count === 1 ? '' : 's'}${p.instructions ? ' · has instructions' : ''}</span></div>` +
        `<div class="lib-actions"><button class="btn sm primary" data-p="chat">New chat</button>${S.current ? '<button class="btn sm" data-p="use">Move this chat here</button>' : ''}<button class="btn sm" data-p="edit">Edit</button><button class="btn sm danger-outline" data-p="del">Delete</button></div></div>`
      )
      .join('');
    b.innerHTML =
      `<p class="muted">A project groups chats that share instructions and files — a thesis, a codebase, a client.</p>` +
      `<div class="btn-row"><button class="btn primary" data-p="new">New project</button></div>` +
      (cards || '<p class="muted">No projects yet.</p>');
  }

  async function projectClick(e) {
    const btn = e.target.closest('[data-p]');
    if (!btn) return;
    const act = btn.dataset.p;
    const card = btn.closest('.lib-card');
    const id = card ? card.dataset.id : null;
    try {
      if (act === 'new') { editing = { kind: 'project', id: null }; await renderProjects(); }
      else if (act === 'edit') { editing = { kind: 'project', id }; await renderProjects(); }
      else if (act === 'cancel') { editing = null; await A.loadLib(); await renderProjects(); }
      else if (act === 'save') {
        const data = { name: $('#f-name').value, instructions: $('#f-instr').value };
        if (!data.name.trim()) { toast('Give it a name'); return; }
        const saved = editing.id ? await API.put(`/api/projects/${editing.id}`, data) : await API.post('/api/projects', data);
        editing = editing.id ? null : { kind: 'project', id: saved.id }; // a new project stays open so files can be added
        await A.loadLib();
        await renderProjects();
        toast('Saved');
      } else if (act === 'addfile') $('#p-file').click();
      else if (act === 'rmfile') {
        await API.del(`/api/projects/${editing.id}/files/${btn.closest('li').dataset.fid}`);
        await renderProjects();
      } else if (act === 'del') {
        if (!(await A.confirmDialog('Delete project?', 'Its chats are kept but no longer belong to a project.', 'Delete'))) return;
        await API.del(`/api/projects/${id}`);
        if (S.current && S.current.project_id === id) S.current.project_id = null;
        await A.loadLib();
        await renderProjects();
      } else if (act === 'chat') { dlg.close(); A.newChat({ project: id }); }
      else if (act === 'use') {
        S.current = await API.patch(`/api/chats/${S.current.id}`, { project_id: id });
        A.updateChip();
        toast('Chat moved to the project');
        dlg.close();
      }
    } catch (err) { toast(err.message); }
  }

  async function uploadProjectFiles(files) {
    for (const f of files) {
      const fd = new FormData();
      fd.append('file', f);
      try {
        const r = await fetch('/api/files', { method: 'POST', body: fd });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
        const meta = await r.json();
        await API.post(`/api/projects/${editing.id}/files`, { file_id: meta.id });
      } catch (err) { toast(`${f.name}: ${err.message}`); }
    }
    await renderProjects();
  }

  /* ------------------------------------------------------------- shell */
  async function render() {
    $('#lib-tabs').innerHTML = TABS.map(([id, l]) => `<button data-tab="${id}" class="${id === tab ? 'active' : ''}">${l}</button>`).join('');
    if (tab === 'memory') await renderMemory();
    else if (tab === 'assistants') renderAssistants();
    else if (tab === 'skills') { body().innerHTML = '<p class="muted small">Preferences saved from your feedback on /planning, /compare, /analyze, /create and /learn results. They shape later runs (defaults, level of detail, charts); they do not retrain the model. Edit a value or delete a row any time.</p><div id="skill-prefs"></div>'; await SkillView.renderPrefs(document.getElementById('skill-prefs')); }
    else await renderProjects();
  }

  function open(t) {
    if (t) tab = t;
    editing = null;
    editMem = null;
    render();
    if (!dlg.open) dlg.showModal();
  }

  $('#lib-tabs').addEventListener('click', (e) => {
    const b = e.target.closest('[data-tab]');
    if (b) { tab = b.dataset.tab; editing = null; editMem = null; render(); }
  });
  body().addEventListener('click', (e) => {
    if (tab === 'memory') memoryClick(e);
    else if (tab === 'assistants') assistantClick(e);
    else projectClick(e);
  });
  body().addEventListener('change', (e) => {
    const t = e.target;
    if (t.dataset.s) setSetting(t.dataset.s, t.checked);
    else if (t.id === 'p-file') uploadProjectFiles([...t.files]);
  });
  body().addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && e.target.id === 'mem-new') { e.preventDefault(); $('#mem-add').click(); }
  });
  document.addEventListener('memory-changed', () => { if (dlg.open && tab === 'memory') renderMemory(); });

  return { open };
})();
window.Library = Library; // a top-level `const` is not a window property
