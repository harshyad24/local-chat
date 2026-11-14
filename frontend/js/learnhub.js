/* Learning hub: one place to come back to every day. Today (streak, due flashcards, next lessons, weak spots), Courses (all /learn courses
   with module progress), Review (spaced-repetition flashcard session), Test me (per module, only the tests the AI says that module needs: quiz, logic, coding, maths). The same tests also appear inside a lesson when its module needs them. */
const LearnHub = (() => {
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const $ = (sel, root = document) => root.querySelector(sel);
  const api = (m, u, b) => LearnLab.api(m, u, b);
  const TABS = [['today', 'Today'], ['courses', 'Courses'], ['review', 'Review'], ['cards', 'Cards'], ['quiz', 'Test me']];
  let dlg, view = 'today', param = '', data = null;

  const ago = (ts) => {
    if (!ts) return 'never';
    const d = Math.floor((Date.now() / 1000 - ts) / 86400);
    return d <= 0 ? 'today' : d === 1 ? 'yesterday' : `${d} days ago`;
  };
  const bar = (pct) => `<div class="lh-bar"><span style="width:${Math.max(0, Math.min(100, pct))}%"></span></div>`;

  async function load() { data = await api('GET', '/api/learn/overview'); return data; }

  function heat(act) {
    const cells = [];
    const today = new Date();
    for (let i = 34; i >= 0; i--) {
      const d = new Date(today); d.setDate(today.getDate() - i);
      const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
      const n = act[key] || 0;
      cells.push(`<span class="lh-cell l${n === 0 ? 0 : n < 4 ? 1 : n < 10 ? 2 : 3}" title="${key}: ${n} activit${n === 1 ? 'y' : 'ies'}"></span>`);
    }
    return `<div class="lh-heat">${cells.join('')}</div><div class="muted small">Last 5 weeks — each square is a day you studied (lessons, quizzes, cards, labs).</div>`;
  }

  /* ---- views */
  function today(el) {
    const d = data, due = d.cards.due;
    el.innerHTML = `<div class="lh-stats">` +
      `<div class="lh-stat"><b>${d.streak.current}</b><span>day streak${d.streak.today ? '' : ' (study today to keep it)'}</span></div>` +
      `<div class="lh-stat"><b>${due}</b><span>flashcard${due === 1 ? '' : 's'} due</span></div>` +
      `<div class="lh-stat"><b>${d.courses.length}</b><span>course${d.courses.length === 1 ? '' : 's'}</span></div>` +
      `<div class="lh-stat"><b>${d.streak.best}</b><span>best streak</span></div></div>` +
      `<div class="lh-actions"><button class="btn primary" data-go="review" ${due ? '' : 'disabled'}>Review ${due} card${due === 1 ? '' : 's'}</button>` +
      `</div>` +
      (d.courses.length ? `<h3 class="lh-h">Continue learning</h3>` + d.courses.map((c) => courseCard(c, true)).join('') :
        `<div class="lh-empty"><b>No courses yet.</b><p class="muted">Type <code>/learn teach me Python from beginner</code> in the chat (or attach a textbook PDF first). Each lesson adds flashcards and quizzes here, and the course stays so you can continue tomorrow.</p></div>`) +
      (d.weak.length ? `<h3 class="lh-h">Needs another look</h3>` + d.weak.map((w) => `<div class="lh-row"><span><b>${esc(w.module)}</b> <span class="muted small">${esc(w.subject)} · last quiz ${w.pct}%</span></span><button class="btn" data-quiz="${esc(w.course_id)}">Test me</button></div>`).join('') : '') +
      `<h3 class="lh-h">Your activity</h3>${heat(d.activity)}`;
  }

  function courseCard(c, compact) {
    const pct = c.total ? Math.round((100 * c.done) / c.total) : 0;
    const mods = compact ? '' : `<div class="lh-mods">${c.modules.map((m) => `<div class="lh-mod ${m.done ? 'done' : m.index === c.current ? 'cur' : ''}"><span>${m.done ? '✓' : m.index === c.current ? '▶' : '○'}</span><span>${esc(m.title)}</span><span class="muted small">${m.last_pct != null ? `quiz ${m.last_pct}%${m.attempts > 1 ? ` · ${m.attempts} tries` : ''}` : m.started ? 'no quiz yet' : ''}</span></div>`).join('')}</div>`;
    return `<div class="lh-course" data-course="${esc(c.id)}"><div class="lh-ct"><b>${esc(c.subject)}</b><span class="muted small">${esc(c.level || '')}${c.book ? ` · from ${esc(c.book)}` : ''} · studied ${ago(c.last_activity)}</span></div>` +
      `${bar(pct)}<div class="muted small">${c.done} of ${c.total} modules done${c.avg_quiz != null ? ` · average quiz ${c.avg_quiz}%` : ''} · ${c.cards.total} flashcards (${c.cards.due} due)</div>${mods}` +
      `<div class="lh-actions"><button class="btn primary" data-next="${esc(c.id)}" data-chat="${esc(c.chat_id)}">${c.done ? 'Next lesson ▸' : 'Open lesson ▸'}</button>` +
      `<button class="btn" data-quiz="${esc(c.id)}">Test me on past lessons</button><button class="btn" data-review-course="${esc(c.id)}" ${c.cards.due ? '' : 'disabled'}>Flashcards (${c.cards.due})</button>` +
      `<button class="btn" data-chat-open="${esc(c.chat_id)}">Open chat</button></div></div>`;
  }

  function courses(el) {
    el.innerHTML = data.courses.length ? data.courses.map((c) => courseCard(c, false)).join('') : '<div class="lh-empty"><b>No courses yet.</b><p class="muted">Start one with <code>/learn …</code> in the chat.</p></div>';
  }

  /* ---- flashcard review: previews on the buttons, keyboard, undo, edit, suspend, cloze cards, source pages */
  let keyHandler = null;
  function stopKeys() { if (keyHandler) { document.removeEventListener('keydown', keyHandler, true); keyHandler = null; } }
  const faceFront = (c, revealed) => {
    const t = esc(c.front);
    if (c.kind !== 'cloze') return t;
    return revealed ? t.replace('[…]', `<mark class="fc-fill">${esc(c.back)}</mark>`) : t.replace('[…]', '<span class="fc-blank">[…]</span>');
  };
  const dueIn = (ts) => { const s = ts - Date.now() / 1000; return s <= 0 ? 'due now' : s < 3600 ? `in ${Math.max(1, Math.round(s / 60))} min` : s < 86400 ? `in ${Math.round(s / 3600)} h` : `in ${Math.round(s / 86400)} d`; };

  async function review(el) {
    stopKeys();
    const d = await api('GET', `/api/learn/review?course=${encodeURIComponent(param)}&limit=40`);
    const queue = d.cards.slice();
    const total = queue.length;
    let done = 0, again = 0, right = 0, last = null, shown = false, editing = false;
    const st = d.stats || {};
    if (!total) {
      el.innerHTML = `<div class="lh-empty"><b>Nothing due right now 🎉</b><p class="muted">${d.counts.total} card(s) in ${param ? 'this course' : 'your decks'}. ` +
        `${st.new_per_day ? `New cards arrive at ${st.new_per_day} a day.` : ''} ${st.maturity && st.maturity.new ? `${st.maturity.new} are still waiting to be introduced.` : ''}</p>` +
        `<button class="btn" data-go="cards">Browse cards</button></div>`;
      return;
    }
    const finish = async () => {
      stopKeys();
      const s2 = await api('GET', `/api/learn/cards/stats?course=${encodeURIComponent(param)}`).catch(() => null);
      const tomorrow = s2 && s2.forecast[1] ? s2.forecast[1].due : null;
      el.innerHTML = `<div class="lh-empty"><b>Session complete</b><p class="muted">${done} review${done === 1 ? '' : 's'} · ${done ? Math.round((100 * right) / done) : 0}% remembered${again ? ` · ${again} to see again soon` : ''}.` +
        `${tomorrow != null ? ` Tomorrow: ${tomorrow} card${tomorrow === 1 ? '' : 's'} due.` : ''}</p><button class="btn primary" data-go="today">Back to Today</button></div>`;
      load().catch(() => {});
    };
    const grade = async (g) => {
      const c = queue[0]; if (!c || !shown || editing) return;
      const res = await api('POST', `/api/learn/cards/${c.id}/grade`, { grade: g }).catch(() => null);
      queue.shift(); done++;
      if (g === 'again') { again++; queue.push(c); } else right++;
      last = res ? { card: c, res, again: g === 'again' } : null;
      shown = false; draw();
    };
    const undo = async () => {
      if (!last || editing) return;
      await api('POST', `/api/learn/cards/${last.card.id}/undo`, { prev: last.res.prev, event: last.res.event }).catch(() => {});
      if (last.again) { const i = queue.lastIndexOf(last.card); if (i >= 0) queue.splice(i, 1); again--; } else right--;
      queue.unshift(last.card); done--; last = null; shown = false; draw();
    };
    const suspend = async () => {
      const c = queue[0]; if (!c || editing) return;
      await api('POST', `/api/learn/cards/${c.id}/suspend`, { suspended: true }).catch(() => {});
      queue.shift(); shown = false; draw();
    };
    const edit = () => {
      const c = queue[0]; if (!c || editing) return; editing = true;
      $('.lh-card', el).innerHTML = `<label class="small muted">Question</label><textarea id="fc-ef" rows="3">${esc(c.front)}</textarea><label class="small muted">Answer</label><textarea id="fc-eb" rows="2">${esc(c.back)}</textarea>` +
        `<div class="lh-actions"><button class="btn primary" data-save>Save</button><button class="btn" data-cancel>Cancel</button></div>`;
      $('[data-save]', el).onclick = async () => {
        const front = $('#fc-ef', el).value, back = $('#fc-eb', el).value;
        try { await api('PUT', `/api/learn/cards/${c.id}`, { front, back }); c.front = front; c.back = back; } catch (e) { /* keep the old text */ }
        editing = false; draw();
      };
      $('[data-cancel]', el).onclick = () => { editing = false; draw(); };
    };
    function draw() {
      if (!queue.length) { finish(); return; }
      const c = queue[0];
      const gr = (k, g, label, cls) => `<button class="btn ${cls || ''}" data-g="${g}"><span>${label}</span><small>${esc(c.preview[g])}</small><kbd>${k}</kbd></button>`;
      el.innerHTML = `<div class="lh-prog"><div class="lh-bar"><span style="width:${Math.round((100 * done) / (done + queue.length))}%"></span></div>` +
        `<span class="muted small">${queue.length} left · ${done} done${last ? '' : ''}</span></div>` +
        `<div class="lh-card fc-face">` +
          `<div class="fc-meta"><span class="lh-chip">${c.kind === 'cloze' ? 'Fill the blank' : 'Question'}</span>` +
            `${c.subject ? `<span class="muted small">${esc(c.subject)}${c.module ? ' · ' + esc(c.module) : ''}</span>` : ''}` +
            `${c.page ? `<span class="lh-chip src" title="Page of your book this came from">p. ${c.page}</span>` : ''}` +
            `${c.maturity === 'new' ? '<span class="lh-chip new">New</span>' : ''}</div>` +
          `<div class="lh-front">${faceFront(c, shown)}</div>` +
          `${shown && c.kind !== 'cloze' ? `<div class="lh-back">${esc(c.back)}</div>` : ''}` +
          `${!shown && c.hint ? `<button class="btn sm" data-hint>Show hint</button><div class="muted small fc-hint hidden">${esc(c.hint)}</div>` : ''}` +
          `${c.leech ? '<div class="fc-leech">⚠ You have missed this card ' + c.lapses + ' times. Try <b>Edit</b> to make it simpler, or <b>Suspend</b> it.</div>' : ''}` +
        `</div>` +
        `<div class="lh-grade">${shown ? gr(1, 'again', 'Again') + gr(2, 'hard', 'Hard') + gr(3, 'good', 'Good', 'primary') + gr(4, 'easy', 'Easy') : '<button class="btn primary" data-show>Show answer <kbd>Space</kbd></button>'}</div>` +
        `<div class="fc-tools"><button class="btn sm" data-undo ${last ? '' : 'disabled'}>↶ Undo <kbd>U</kbd></button><button class="btn sm" data-edit>Edit <kbd>E</kbd></button>` +
        `<button class="btn sm" data-suspend title="Never show this card again">Suspend <kbd>S</kbd></button></div>`;
      const show = $('[data-show]', el); if (show) show.onclick = () => { shown = true; draw(); };
      el.querySelectorAll('[data-g]').forEach((b) => { b.onclick = () => grade(b.dataset.g); });
      const hint = $('[data-hint]', el); if (hint) hint.onclick = () => { $('.fc-hint', el).classList.remove('hidden'); hint.remove(); };
      $('[data-undo]', el).onclick = undo; $('[data-edit]', el).onclick = edit; $('[data-suspend]', el).onclick = suspend;
    }
    keyHandler = (e) => {
      const tgt = e.target && e.target.closest ? e.target : document.body;
      if (view !== 'review' || !dlg.open || editing || tgt.closest('textarea,input,select')) return;
      const k = e.key.toLowerCase();
      if ((k === ' ' || k === 'enter') && !shown) { e.preventDefault(); shown = true; draw(); }
      else if (shown && '1234'.includes(k) && k) { e.preventDefault(); grade(['again', 'hard', 'good', 'easy'][Number(k) - 1]); }
      else if (k === 'u') undo(); else if (k === 'e') { e.preventDefault(); edit(); } else if (k === 's') suspend();
    };
    document.addEventListener('keydown', keyHandler, true);
    draw();
  }

  /* ---- Cards tab: statistics and a browser for every card */
  async function cardsTab(el) {
    stopKeys();
    if (!data.courses.length) { el.innerHTML = '<div class="lh-empty"><b>No cards yet.</b><p class="muted">Every <code>/learn</code> lesson adds flashcards.</p></div>'; return; }
    const cid = param || data.courses[0].id;
    param = cid;
    const [stats, list] = await Promise.all([api('GET', `/api/learn/cards/stats?course=${encodeURIComponent(cid)}`), api('GET', `/api/learn/courses/${cid}/cards`)]);
    const m = stats.maturity, max = Math.max(1, ...stats.forecast.map((f) => f.due));
    let filter = 'all', q = '';
    const rows = () => list.cards.filter((c) => (filter === 'all' ? !c.suspended : filter === 'due' ? !c.suspended && c.due <= Date.now() / 1000 + 86400 && c.first_seen : filter === 'leech' ? c.leech && !c.suspended : c.suspended) &&
      (!q || (c.front + ' ' + c.back).toLowerCase().includes(q)));
    const rowHTML = (c) => `<div class="lh-crow" data-cid="${c.id}"><div class="lh-ctext"><div class="lh-cf">${faceFront(c, false)}</div><div class="muted small lh-cb">${esc(c.back)}</div>` +
      `<div class="lh-cmeta"><span class="lh-chip">${c.kind === 'cloze' ? 'cloze' : 'basic'}</span><span class="lh-chip ${c.maturity}">${c.maturity}</span>${c.page ? `<span class="lh-chip src">p. ${c.page}</span>` : ''}` +
      `${c.leech ? '<span class="lh-chip bad">leech</span>' : ''}<span class="muted small">${c.suspended ? 'suspended' : c.first_seen ? 'due ' + dueIn(c.due) : 'not started'}${c.module ? ' · ' + esc(c.module) : ''}</span></div></div>` +
      `<div class="lh-cact"><button class="btn sm" data-ced>Edit</button><button class="btn sm" data-csus>${c.suspended ? 'Restore' : 'Suspend'}</button><button class="btn sm danger-outline" data-cdel>Delete</button></div></div>`;
    el.innerHTML = `<div class="lh-actions"><select id="cd-course">${data.courses.map((c) => `<option value="${esc(c.id)}"${c.id === cid ? ' selected' : ''}>${esc(c.subject)}</option>`).join('')}</select>` +
      `<button class="btn primary" data-go="review" ${stats.total ? '' : 'disabled'}>Review due cards</button></div>` +
      `<div class="lh-stats"><div class="lh-stat"><b>${stats.total}</b><span>cards</span></div><div class="lh-stat"><b>${stats.retention == null ? '–' : stats.retention + '%'}</b><span>remembered (30 days)</span></div>` +
      `<div class="lh-stat"><b>${stats.reviewed_today}</b><span>reviewed today</span></div><div class="lh-stat"><b>${stats.leeches}</b><span>leeches</span></div></div>` +
      `<div class="lh-mature"><span class="seg new" style="flex:${m.new || 0.001}" title="New">${m.new} new</span><span class="seg learning" style="flex:${m.learning || 0.001}">${m.learning} learning</span>` +
      `<span class="seg young" style="flex:${m.young || 0.001}">${m.young} young</span><span class="seg mature" style="flex:${m.mature || 0.001}">${m.mature} mature</span></div>` +
      `<h3 class="lh-h">Due over the next 7 days</h3><div class="lh-forecast">${stats.forecast.map((f) => `<div><span class="bar" style="height:${Math.round((100 * f.due) / max)}%"></span><b>${f.due}</b><small>${esc(f.day)}</small></div>`).join('')}</div>` +
      `<div class="lh-actions"><label class="small muted">New cards a day <select id="cd-new">${[5, 10, 15, 20, 30, 50].map((n) => `<option${n === stats.new_per_day ? ' selected' : ''}>${n}</option>`).join('')}</select></label></div>` +
      `<h3 class="lh-h">All cards</h3><div class="lh-filters"><input id="cd-q" type="search" placeholder="Search cards"><div>` +
      [['all', 'All'], ['due', 'Due'], ['leech', 'Leeches'], ['sus', 'Suspended']].map(([k, l]) => `<button class="btn sm${k === 'all' ? ' primary' : ''}" data-f="${k}">${l}</button>`).join('') + `</div></div>` +
      `<div class="lh-add"><input id="cd-f" placeholder="New card: question"><input id="cd-b" placeholder="answer"><button class="btn sm" data-cadd>Add</button></div><div id="cd-list"></div>`;
    const paint = () => { const r = rows(); $('#cd-list', el).innerHTML = r.length ? r.map(rowHTML).join('') : '<p class="muted small">No cards match.</p>'; };
    paint();
    $('#cd-course', el).onchange = (e) => { param = e.target.value; render(); };
    $('#cd-new', el).onchange = async (e) => { await api('PUT', '/api/settings', { cards_new_per_day: Number(e.target.value) }).catch(() => {}); };
    $('#cd-q', el).oninput = (e) => { q = e.target.value.toLowerCase(); paint(); };
    el.querySelectorAll('[data-f]').forEach((b) => { b.onclick = () => { filter = b.dataset.f; el.querySelectorAll('[data-f]').forEach((x) => x.classList.toggle('primary', x === b)); paint(); }; });
    el.querySelector('[data-cadd]').onclick = async () => {
      const f = $('#cd-f', el).value.trim(), bk = $('#cd-b', el).value.trim(); if (f.length < 4 || !bk) return;
      await api('POST', `/api/learn/courses/${cid}/cards`, { front: f, back: bk }).catch(() => {}); render();
    };
    $('#cd-list', el).addEventListener('click', async (e) => {
      const row = e.target.closest('.lh-crow'); if (!row) return;
      const id = Number(row.dataset.cid), c = list.cards.find((x) => x.id === id);
      if (e.target.closest('[data-cdel]')) { await api('DELETE', `/api/learn/cards/${id}`).catch(() => {}); list.cards.splice(list.cards.indexOf(c), 1); paint(); }
      else if (e.target.closest('[data-csus]')) { await api('POST', `/api/learn/cards/${id}/suspend`, { suspended: !c.suspended }).catch(() => {}); c.suspended = c.suspended ? 0 : 1; paint(); }
      else if (e.target.closest('[data-ced]')) {
        row.querySelector('.lh-ctext').innerHTML = `<textarea rows="2">${esc(c.front)}</textarea><textarea rows="2">${esc(c.back)}</textarea>`;
        const btn = row.querySelector('[data-ced]'); btn.textContent = 'Save'; btn.dataset.ced = ''; btn.dataset.csave = '1'; btn.removeAttribute('data-ced');
      } else if (e.target.closest('[data-csave]')) {
        const [f, b] = row.querySelectorAll('textarea');
        try { await api('PUT', `/api/learn/cards/${id}`, { front: f.value, back: b.value }); c.front = f.value; c.back = b.value; } catch (err) { /* keep */ }
        paint();
      }
    });
  }

  /* ---- Test me: per module, only the tests the AI says that module needs (quiz, logic, coding, maths), plus a mixed quiz of past lessons */
  const TESTS = { quiz: ['Quiz', 'Multiple choice'], logic: ['Logic test', 'Explain and reason'], coding: ['Coding test', 'Write or explain code'], math: ['Maths practice', 'Generated problems'] };

  function runQuiz(el, qs, label, backParam) {
    let i = 0, right = 0; const missedMods = new Set();
    const ask = () => {
      if (i >= qs.length) {
        el.innerHTML = `<div class="lh-empty"><b>Score ${right} of ${qs.length} (${Math.round((100 * right) / qs.length)}%)</b><p class="muted">${missedMods.size ? 'Look again at: ' + [...missedMods].map(esc).join(', ') + '.' : 'Everything in this set is solid.'}</p><button class="btn primary" data-quiz="${esc(backParam)}">Back to tests</button></div>`;
        api('POST', '/api/learn/events', { course_id: backParam, kind: 'review', module: label, right, total: qs.length }).catch(() => {});
        return;
      }
      const q = qs[i];
      el.innerHTML = `<div class="lh-prog muted small">Question ${i + 1} of ${qs.length}${q.module_title ? ` · ${esc(q.module_title)}` : ''}${q.was_missed ? ' · you missed this before' : ''}</div><div class="lh-card"><div class="lh-front">${esc(q.q)}</div></div>` +
        q.options.map((o, oi) => `<button class="sk-opt" data-oi="${oi}">${esc(o)}</button>`).join('') + '<div class="sk-expl muted small hidden"></div><div class="lh-actions"></div>';
      el.querySelectorAll('.sk-opt').forEach((b) => b.addEventListener('click', () => {
        const ok = Number(b.dataset.oi) === Number(q.answer);
        el.querySelectorAll('.sk-opt').forEach((x) => { x.disabled = true; });
        b.classList.add(ok ? 'right' : 'wrong'); if (!ok) { el.querySelector(`.sk-opt[data-oi="${q.answer}"]`).classList.add('right'); missedMods.add(q.module_title || q.module || label); }
        if (ok) right++;
        const ex = $('.sk-expl', el); ex.textContent = q.explain || ''; ex.classList.remove('hidden');
        $('.lh-actions', el).innerHTML = '<button class="btn primary" data-n>Next ▸</button>';
        $('[data-n]', el).addEventListener('click', () => { i++; ask(); });
      }));
    };
    ask();
  }

  async function quiz(el) {
    if (!param) {
      el.innerHTML = '<h3 class="lh-h">Test me on…</h3>' + (data.courses.length ? data.courses.map((c) => `<div class="lh-row"><span><b>${esc(c.subject)}</b> <span class="muted small">${c.done} modules done${c.avg_quiz != null ? ` · avg ${c.avg_quiz}%` : ''}</span></span><button class="btn primary" data-quiz="${esc(c.id)}">Open</button></div>`).join('') : '<p class="muted">Start a course with <code>/learn …</code> first; the tests for each module appear here.</p>');
      return;
    }
    const cid = param;
    const d = await api('GET', `/api/learn/courses/${cid}/tests`);
    const hasMixed = d.modules.some((m) => m.quiz_count);
    const back = (box) => { const b = document.createElement('div'); b.innerHTML = '<button class="btn sm" data-back>‹ Back to modules</button>'; box.prepend(b); $('[data-back]', box).onclick = () => quiz(el); };
    const open = async (idx, kind) => {
      const m = d.modules[idx];
      el.innerHTML = '<div class="muted small">Loading…</div>';
      if (kind === 'quiz') {
        const q = await api('GET', `/api/learn/courses/${cid}/modules/${idx}/quiz`);
        if (!(q.questions || []).length) { el.innerHTML = '<div class="lh-empty"><b>No quiz questions saved for this module.</b><p class="muted">Open its lesson with Next lesson to generate them.</p><button class="btn" data-back>‹ Back</button></div>'; $('[data-back]', el).onclick = () => quiz(el); return; }
        runQuiz(el, q.questions.map((x) => ({ ...x, module_title: m.title })), m.title, cid); return;
      }
      const blk = kind === 'math' ? { type: 'mathlab', title: `Maths practice: ${m.title}`, topic: (m.plan && m.plan.math_topic) || 'arithmetic', level: 2, course_id: cid, module: m.title }
        : { type: 'check', kind, title: `${TESTS[kind][0]}: ${m.title}`, course_id: cid, module: idx, module_title: m.title, why: (m.plan && m.plan.why) || '' };
      el.innerHTML = `<div class="lh-prog"><button class="btn sm" data-back>‹ Back to modules</button></div><div id="tm-box">${LearnLab.blocks[blk.type](blk)}</div>`;
      $('[data-back]', el).onclick = () => quiz(el);
      LearnLab.mount($('#tm-box', el), { id: cid }, [blk]);
    };
    const row = (m) => {
      const p = m.plan, rec = p ? Object.keys(TESTS).filter((k) => p[k] && (k !== 'quiz' || m.quiz_count)) : [];
      const others = Object.keys(TESTS).filter((k) => !rec.includes(k) && k !== 'math' && (k !== 'quiz' || m.quiz_count));
      return `<div class="lh-course tm-mod" data-idx="${m.index}"><div class="lh-ct"><b>${esc(m.title)}</b><span class="muted small">${m.last_pct != null ? `last quiz ${m.last_pct}%` : m.started ? 'lesson opened' : 'not studied yet'}</span></div>` +
        (p ? `<div class="muted small">${p.why ? esc(p.why) : ''}${rec.length ? '' : ' No test recommended for this module.'}</div>` : '<div class="muted small">The AI has not decided what this module needs yet.</div>') +
        `<div class="lh-actions">${rec.map((k) => `<button class="btn primary" data-t="${k}">${TESTS[k][0]}</button>`).join('')}` +
        `${p ? '' : '<button class="btn primary" data-plan>What should I test?</button>'}` +
        `${p && others.length ? `<span class="muted small tm-other">Also: ${others.map((k) => `<a href="#" data-t="${k}">${TESTS[k][0].toLowerCase()}</a>`).join(' · ')}</span>` : ''}` +
        `${p ? '<a href="#" class="muted small" data-plan>decide again</a>' : ''}</div></div>`;
    };
    el.innerHTML = `<h3 class="lh-h">${esc(d.subject)}</h3><p class="muted small">For each module the AI picks only the tests it needs. A module can need none.</p>` +
      `${hasMixed ? `<div class="lh-actions"><button class="btn" data-mixed>Mixed quiz of past lessons</button></div>` : ''}` + d.modules.map(row).join('');
    const mx = $('[data-mixed]', el);
    if (mx) mx.onclick = async () => {
      const q = await api('GET', `/api/learn/courses/${cid}/past-quiz?n=8`);
      if (!(q.questions || []).length) return;
      runQuiz(el, q.questions, 'mixed quiz', cid);
    };
    el.querySelectorAll('.tm-mod').forEach((box) => {
      const idx = Number(box.dataset.idx);
      box.addEventListener('click', async (e) => {
        const a = e.target.closest('[data-t],[data-plan]'); if (!a) return;
        e.preventDefault();
        if (a.dataset.t) { open(idx, a.dataset.t); return; }
        a.textContent = 'Deciding… (can take a minute)'; a.disabled = true;
        const r = await api('POST', `/api/learn/courses/${cid}/modules/${idx}/plan`, {}).catch(() => null);
        if (r && r.ok && !r.fallback) quiz(el);
        else { a.textContent = 'The model gave no answer - try again'; a.disabled = false; }
      });
    });
  }

  /* ---- shell */
  async function render() {
    $('#lh-tabs').innerHTML = TABS.map(([id, l]) => `<button data-tab="${id}" class="${id === view ? 'active' : ''}">${l}${id === 'review' && data && data.cards.due ? ` <span class="lh-badge">${data.cards.due}</span>` : ''}</button>`).join('');
    const body = $('#lh-body');
    body.innerHTML = '<div class="muted small">Loading…</div>';
    try {
      await load();
      $('#lh-tabs').innerHTML = TABS.map(([id, l]) => `<button data-tab="${id}" class="${id === view ? 'active' : ''}">${l}${id === 'review' && data.cards.due ? ` <span class="lh-badge">${data.cards.due}</span>` : ''}</button>`).join('');
      if (view !== 'review') stopKeys();
      if (view === 'today') today(body); else if (view === 'courses') courses(body); else if (view === 'review') await review(body); else if (view === 'cards') await cardsTab(body);
      else if (view === 'quiz') await quiz(body);
    } catch (err) { body.innerHTML = `<div class="muted small">${esc(err.message)}</div>`; }
  }

  function open(v, p) {
    view = v || 'today'; param = p || '';
    if (!dlg.open) dlg.showModal();
    render();
  }

  async function goNext(courseId, chatId) {
    dlg.close();
    try { if (window.App && App.openChat) await App.openChat(chatId); } catch (_) { /* optional */ }
    setTimeout(() => document.dispatchEvent(new CustomEvent('skill-submit', { detail: { text: 'Next lesson', runId: courseId, answers: { _action: 'next' } } })), 400);
  }

  let ready = false;
  function init() {
    if (ready) return;
    dlg = document.getElementById('dlg-learn');
    if (!dlg) return;
    ready = true;
    const btn = document.getElementById('btn-learn');
    if (btn) btn.addEventListener('click', () => open('today'));
    document.addEventListener('learn-open', (e) => open((e.detail && e.detail.view) || 'today', e.detail && e.detail.course));
    dlg.addEventListener('click', async (e) => {
      const t = e.target.closest('button'); if (!t) return;
      if (t.dataset.tab) { view = t.dataset.tab; param = ''; render(); }
      else if (t.dataset.go) { view = t.dataset.go; param = ''; render(); }
      else if (t.dataset.quiz) { view = 'quiz'; param = t.dataset.quiz; render(); }
      else if (t.hasAttribute('data-review-course')) { view = 'review'; param = t.dataset.reviewCourse; render(); }
      else if (t.dataset.chatOpen) { dlg.close(); if (window.App && App.openChat) App.openChat(t.dataset.chatOpen); }
      else if (t.dataset.next) goNext(t.dataset.next, t.dataset.chat);
    });
  }

  document.addEventListener('DOMContentLoaded', init);
  if (document.readyState !== 'loading') init();
  return { open };
})();
