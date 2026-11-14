/* Hands-on blocks for /learn lessons and the Learning hub: Python code lab (runs in the local sandbox, checks tests), HTML/JS try-it
   (sandboxed iframe), maths practice (generated problems, answers checked by the server), flashcards, and typed-answer logic / coding tests that the AI adds only when a module needs them. */
const LearnLab = (() => {
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const api = async (method, url, body) => {
    const res = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(d.detail || 'Request failed');
    return d;
  };
  const logEvent = (ev) => api('POST', '/api/learn/events', ev).catch(() => {});
  const store = {
    get: (k) => { try { return localStorage.getItem(k); } catch (_) { return null; } },
    set: (k, v) => { try { localStorage.setItem(k, v); } catch (_) { /* optional */ } },
  };
  const $ = (sel, root = document) => root.querySelector(sel);

  /* ------------------------------------------------------------ renderers (HTML strings; behaviour is attached by mount()) */
  const blocks = {
    codelab: (b) => {
      const ex = b.exercises || [];
      const tabs = ex.length > 1 ? `<div class="lab-tabs">${ex.map((_, i) => `<button class="lab-tab ${i === 0 ? 'on' : ''}" data-ex="${i}">Exercise ${i + 1}<span class="lab-ok"></span></button>`).join('')}</div>` : '';
      return `<div class="sk-sec lab" data-lab="code" data-course="${esc(b.course_id || '')}" data-module="${esc(b.module || '')}">` +
        `<div class="sk-sec-t">${esc(b.title || 'Coding lab')}</div>${tabs}` +
        `<div class="lab-task muted"></div><textarea class="lab-code" spellcheck="false" rows="9" aria-label="Python code"></textarea>` +
        `<div class="lab-bar"><button class="btn" data-run>Run ▶</button>${ex.length ? '<button class="btn primary" data-check>Check answer ✓</button><button class="btn" data-hint>Hint</button><button class="btn" data-sol>Show solution</button>' : ''}` +
        `<button class="btn" data-reset>Reset</button><span class="lab-status muted small">Ctrl+Enter runs · runs locally, no internet</span></div>` +
        `<pre class="lab-out hidden"></pre><div class="lab-tests"></div></div>`;
    },
    tryit: (b) => `<div class="sk-sec lab" data-lab="web"><div class="sk-sec-t">${esc(b.title || 'Try it yourself')}</div><div class="lab-split">` +
      `<textarea class="lab-code" spellcheck="false" rows="12" aria-label="HTML, CSS and JavaScript">${esc(b.html || '')}</textarea>` +
      `<iframe class="lab-frame" sandbox="allow-scripts" title="Result"></iframe></div>` +
      `<div class="lab-bar"><button class="btn primary" data-run>Run ▶</button><span class="lab-status muted small">The preview is sandboxed: it cannot reach this app or the internet.</span></div></div>`,
    mathlab: (b) => `<div class="sk-sec lab" data-lab="math" data-course="${esc(b.course_id || '')}" data-module="${esc(b.module || '')}" data-topic="${esc(b.topic || 'arithmetic')}" data-level="${esc(b.level || 1)}">` +
      `<div class="sk-sec-t">${esc(b.title || 'Maths practice')}</div><div class="lab-bar"><select class="sk-in" data-topic></select><select class="sk-in" data-level><option value="1">Easy</option><option value="2">Medium</option><option value="3">Hard</option></select>` +
      `<button class="btn" data-new>New set</button><span class="lab-score muted small"></span></div><div class="math-body"></div></div>`,
    flashcards: (b) => `<div class="sk-sec fc-block" data-course="${esc(b.course_id || '')}"><div class="sk-sec-t">${esc(b.title || 'Flashcards')}</div>` +
      `<div class="fc-grid">${(b.cards || []).map((c) => `<button class="fc-card" title="Click to flip"><span class="fc-front">${c.kind === 'cloze' ? esc(c.front).replace('[…]', '<span class="fc-blank">[…]</span>') : esc(c.front)}</span><span class="fc-back">${esc(c.back)}${c.page ? ` <span class="lh-chip src">p. ${c.page}</span>` : ''}</span></button>`).join('')}</div>` +
      `<div class="lab-bar"><span class="muted small">${(b.cards || []).length} new card(s) added to your deck · ${b.deck ? `${b.deck.total} in this course, ${b.deck.due} due today` : ''}. They come back on a spaced schedule.</span>` +
      `<button class="btn primary" data-review>Review now</button></div></div>`,
    check: (b) => `<div class="sk-sec lab" data-lab="check"><div class="sk-sec-t">${esc(b.title || 'Test')}</div><div class="ck-body"></div></div>`,
  };

  /* ------------------------------------------------------------ behaviour */
  function tabKey(ta) {
    ta.addEventListener('keydown', (e) => {
      if (e.key === 'Tab' && !e.shiftKey) {
        e.preventDefault();
        const s = ta.selectionStart;
        ta.value = ta.value.slice(0, s) + '    ' + ta.value.slice(ta.selectionEnd);
        ta.selectionStart = ta.selectionEnd = s + 4;
      }
    });
  }

  function mountCode(box, run, b) {
    const ex = b.exercises || [];
    const ta = $('.lab-code', box), out = $('.lab-out', box), tests = $('.lab-tests', box), task = $('.lab-task', box), status = $('.lab-status', box);
    let cur = 0;
    const passed = new Set();
    const key = (i) => `lab:${run.id}:${b.module}:${i}:${b.scratch ? 's' : 'e'}`;
    const starter = () => (ex[cur] ? ex[cur].starter || '' : '# Write any Python here and press Run\nprint("Hello")\n');
    function show(i) {
      if (ex.length) cur = i;
      ta.value = store.get(key(cur)) ?? starter();
      task.innerHTML = ex[cur] ? `<b>${cur + 1}.</b> ${esc(ex[cur].task)}` : '';
      out.classList.add('hidden'); tests.innerHTML = '';
      status.textContent = 'Ctrl+Enter runs · runs locally, no internet';
      box.querySelectorAll('.lab-tab').forEach((t, ti) => t.classList.toggle('on', ti === cur));
    }
    tabKey(ta);
    ta.addEventListener('input', () => store.set(key(cur), ta.value));
    async function exec(withTests) {
      status.textContent = 'Running…';
      out.classList.remove('hidden');
      try {
        const r = await api('POST', '/api/lab/run', { code: ta.value, tests: withTests ? ex[cur].tests : null });
        out.textContent = (r.stdout || '') + (r.stderr ? (r.stdout ? '\n' : '') + r.stderr : '') + (r.timed_out ? '\n(stopped: time limit)' : '') || '(no output — use print() to see values)';
        out.classList.toggle('err', !r.ok);
        if (withTests) {
          tests.innerHTML = `<div class="lab-res ${r.passed === r.total ? 'pass' : 'fail'}"><b>${r.passed} of ${r.total} tests passed</b></div>` +
            r.tests.map((t) => `<div class="lab-t ${t.ok ? 'ok' : 'bad'}"><span>${t.ok ? '✓' : '✗'}</span><code>${esc(t.test)}</code>${t.ok ? '' : `<em>${esc(t.error)}</em>`}</div>`).join('');
          if (r.total && r.passed === r.total) {
            if (!passed.has(cur)) { passed.add(cur); logEvent({ course_id: b.course_id, kind: 'lab', module: b.module, right: 1, total: 1, detail: { exercise: cur } }); }
            const tab = box.querySelectorAll('.lab-tab')[cur]; if (tab) tab.querySelector('.lab-ok').textContent = ' ✓';
            status.textContent = 'All tests pass — nice work.';
          } else status.textContent = 'Not yet — read the failing test and try again.';
        } else status.textContent = r.ok ? 'Done.' : 'There was an error.';
      } catch (err) { out.textContent = err.message; status.textContent = 'Could not run.'; }
    }
    box.querySelectorAll('.lab-tab').forEach((t) => t.addEventListener('click', () => show(Number(t.dataset.ex))));
    $('[data-run]', box).addEventListener('click', () => exec(false));
    const chk = $('[data-check]', box); if (chk) chk.addEventListener('click', () => exec(true));
    const hint = $('[data-hint]', box); if (hint) hint.addEventListener('click', () => { tests.innerHTML = `<div class="lab-res">${esc(ex[cur].hint || 'Re-read the task and the failing test; build the result step by step.')}</div>`; });
    const sol = $('[data-sol]', box); if (sol) sol.addEventListener('click', () => { if (confirm('Show the solution? Try once more first if you can.')) tests.innerHTML = `<pre class="lab-out">${esc(ex[cur].solution || '')}</pre>`; });
    $('[data-reset]', box).addEventListener('click', () => { store.set(key(cur), ''); try { localStorage.removeItem(key(cur)); } catch (_) { /* optional */ } show(cur); });
    ta.addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); exec(false); } });
    show(0);
  }

  function mountWeb(box) {
    const ta = $('.lab-code', box), fr = $('.lab-frame', box);
    tabKey(ta);
    const run = () => { fr.srcdoc = ta.value; };
    $('[data-run]', box).addEventListener('click', run);
    ta.addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); run(); } });
    run();
  }

  async function mountMath(box, b) {
    const body = $('.math-body', box), score = $('.lab-score', box), selT = $('[data-topic]', box), selL = $('[data-level]', box);
    let set = [], i = 0, right = 0, done = 0, logged = false;
    selL.value = String(b.level || 1);
    async function load() {
      body.innerHTML = '<div class="muted small">Preparing problems…</div>';
      try {
        const d = await api('GET', `/api/learn/practice/math?topic=${encodeURIComponent(selT.value || b.topic)}&level=${selL.value}&n=5`);
        if (!selT.options.length) { selT.innerHTML = d.topics.map((t) => `<option ${t === (b.topic || d.topic) ? 'selected' : ''}>${esc(t)}</option>`).join(''); }
        set = d.problems; i = 0; right = 0; done = 0; logged = false; ask();
      } catch (err) { body.innerHTML = `<div class="muted small">${esc(err.message)}</div>`; }
    }
    function ask() {
      score.textContent = `${right} correct of ${done}`;
      if (i >= set.length) {
        body.innerHTML = `<div class="lab-res ${right >= 4 ? 'pass' : ''}"><b>Set finished: ${right} of ${set.length}.</b> ${right >= 4 ? 'Try a harder level.' : 'Do another set to lock it in.'}</div>`;
        if (!logged) { logged = true; logEvent({ course_id: b.course_id || null, kind: 'math', module: b.module || selT.value, right, total: set.length, detail: { topic: selT.value, level: Number(selL.value) } }); }
        return;
      }
      const p = set[i];
      body.innerHTML = `<div class="math-q"><span class="muted small">Problem ${i + 1} of ${set.length}</span><div class="math-text">${esc(p.q)}</div>` +
        `<div class="lab-bar"><input class="sk-in math-in" autocomplete="off" placeholder="Your answer" aria-label="Your answer"><button class="btn primary" data-go>Check</button>${p.hint ? '<button class="btn" data-h>Hint</button>' : ''}</div><div class="math-fb"></div></div>`;
      const inp = $('.math-in', body), fb = $('.math-fb', body);
      inp.focus();
      const go = async () => {
        if (!inp.value.trim()) return;
        const r = await api('POST', '/api/learn/practice/check', { kind: p.kind, expected: p.answer, given: inp.value, tol: p.tol || 0 }).catch((e) => ({ correct: false, message: e.message }));
        if (r.message && !r.correct) { fb.innerHTML = `<span class="muted small">${esc(r.message)}</span>`; return; }
        done++; if (r.correct) right++;
        fb.innerHTML = r.correct ? '<b class="ok">Correct ✓</b>' : `<b class="bad">Not quite.</b> The answer is <code>${esc(p.answer)}</code>.`;
        $('[data-go]', body).outerHTML = '<button class="btn primary" data-next>Next ▸</button>';
        inp.disabled = true;
        $('[data-next]', body).addEventListener('click', () => { i++; ask(); });
        score.textContent = `${right} correct of ${done}`;
      };
      $('[data-go]', body).addEventListener('click', go);
      inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); const nx = $('[data-next]', body); nx ? nx.click() : go(); } });
      const h = $('[data-h]', body); if (h) h.addEventListener('click', () => { fb.innerHTML = `<span class="muted small">${esc(p.hint)}</span>`; });
    }
    $('[data-new]', box).addEventListener('click', load);
    selT.addEventListener('change', load); selL.addEventListener('change', load);
    selT.innerHTML = `<option selected>${esc(b.topic || 'arithmetic')}</option>`;
    await load();
  }

  /* A typed-answer test inside a lesson (logic or coding): the AI asks, the learner types, the answer is checked against the course book / the internet,
     then up to three follow-up questions find out whether the idea was really understood. Only appears when the AI decided the module needs it. */
  function mountCheck(box, b) {
    const kind = b.kind === 'coding' ? 'coding' : 'logic', code = kind === 'coding';
    const run = { rounds: [], q: '', key_points: [] };
    const body = (html) => { box.querySelector('.ck-body').innerHTML = html; };
    const bar = (pct) => `<div class="lh-bar"><span style="width:${Math.max(0, Math.min(100, pct))}%"></span></div>`;
    const list = (title, items, cls) => (items && items.length ? `<div class="ex-sec ${cls}"><b>${title}</b><ul>${items.map((x) => `<li>${esc(x)}</li>`).join('')}</ul></div>` : '');
    const intro = () => {
      body(`<div class="muted small">${esc(b.why || '')} Answer in your own words${code ? ' or with code' : ''}; the AI checks it against ${b.course_id ? 'your book first, and the internet only if the book does not cover it' : 'the internet'} and may ask follow-up questions. A study aid, not an official grade.</div>` +
        `<div class="lab-bar"><button class="btn primary" data-go>Ask me a question</button><span class="lab-status muted small ex-err"></span></div>`);
      $('[data-go]', box).onclick = async () => {
        const btn = $('[data-go]', box); btn.disabled = true; btn.textContent = 'Writing a question…';
        const d = await api('POST', '/api/learn/exam/question', { course_id: b.course_id || null, module: b.module == null ? null : Number(b.module), topic: b.module_title || '', kind, avoid: [] }).catch((e) => ({ ok: false, error: e.message }));
        if (!d.ok) { btn.disabled = false; btn.textContent = 'Ask me a question'; $('.ex-err', box).textContent = d.error || 'Could not write a question.'; return; }
        run.q = d.question; run.key_points = d.key_points || []; ask();
      };
    };
    const ask = () => {
      const n = run.rounds.length + 1;
      body(`<div class="muted small">${n === 1 ? 'Question' : 'Follow-up ' + (n - 1)}</div><div class="lh-card"><div class="lh-front ex-q">${esc(run.q).replace(/\n/g, '<br>')}</div></div>` +
        `<textarea class="ex-ans ${code ? 'ex-code' : ''}" rows="${code ? 9 : 5}" spellcheck="false" placeholder="${code ? 'Write your code or explanation here…' : 'Type your answer in your own words…'}"></textarea>` +
        `<div class="lab-bar"><button class="btn primary" data-check>Check my answer</button>${run.rounds.length ? '<button class="btn" data-fin>Finish now</button>' : ''}<span class="lab-status muted small ex-err"></span></div>`);
      const ta = $('.ex-ans', box); ta.focus(); if (code) tabKey(ta);
      const fin = $('[data-fin]', box); if (fin) fin.onclick = finish;
      $('[data-check]', box).onclick = async () => {
        const ans = ta.value.trim(), err = $('.ex-err', box);
        if (ans.length < 2) { err.textContent = 'Type your answer first.'; return; }
        const btn = $('[data-check]', box); btn.disabled = true; btn.textContent = 'Checking… (can take a minute on a local model)';
        const d = await api('POST', '/api/learn/exam/grade', { course_id: b.course_id || null, module: b.module == null ? null : Number(b.module), topic: b.module_title || '', kind, question: run.q, answer: ans, key_points: run.key_points, sources: 'auto',
          history: run.rounds.map((r) => ({ q: r.q, score: r.score, missing: r.missing })) }).catch((e) => ({ ok: false, error: e.message }));
        if (!d.ok) { err.textContent = d.error || 'Could not check the answer.'; btn.disabled = false; btn.textContent = 'Check my answer'; return; }
        run.rounds.push({ q: run.q, a: ans, score: d.score, missing: d.missing, wrong: d.wrong, model_answer: d.model_answer });
        result(d);
      };
    };
    const result = (d) => {
      const src = (d.sources || []).map((s) => (s.kind === 'book' ? `<span class="lh-chip src">book ${esc(s.label)}</span>` : `<a class="lh-chip src" href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc((s.title || s.url).slice(0, 40))}</a>`)).join(' ');
      body(`<div class="ex-score ${d.verdict}"><b>${d.score}%</b><span>${{ correct: 'Correct', partly: 'Partly right', incorrect: 'Not yet' }[d.verdict]}</span></div>${bar(d.score)}` +
        `<div class="muted small">Checked against: ${esc(d.used === 'none' ? 'general knowledge only (no source text found)' : d.used)}${d.ran ? ' · your code was actually run' : ''}</div>` +
        list('You got right', d.right, 'ok') + list('Missing', d.missing, 'miss') + list('Not quite right', d.wrong, 'bad') +
        (d.model_answer ? `<details class="ex-sec"><summary><b>A good answer</b></summary><p>${esc(d.model_answer).replace(/\n/g, '<br>')}</p></details>` : '') +
        (d.source_note || src ? `<div class="muted small ex-src">${esc(d.source_note || '')} ${src}</div>` : '') +
        (d.followup ? `<div class="ex-follow"><b>Follow-up question</b><p>${esc(d.followup)}</p></div>` : '') +
        `<div class="lab-bar">${d.followup ? '<button class="btn primary" data-next>Answer the follow-up</button>' : ''}<button class="btn${d.followup ? '' : ' primary'}" data-fin>${d.followup ? 'Finish now' : 'See my result'}</button></div>`);
      const nx = $('[data-next]', box); if (nx) nx.onclick = () => { run.q = d.followup; ask(); };
      $('[data-fin]', box).onclick = finish;
    };
    const finish = async () => {
      body('<div class="muted small">Working out your result…</div>');
      const d = await api('POST', '/api/learn/exam/finish', { course_id: b.course_id || null, module_title: b.module_title || '', topic: b.module_title || '', kind, rounds: run.rounds.map((r) => ({ q: r.q, score: r.score, missing: r.missing, wrong: r.wrong })) }).catch(() => null);
      if (!d) { body('<div class="muted small">Could not save the result.</div>'); return; }
      const msg = { solid: 'You understand this well.', shaky: 'Partly there - the gaps below are worth another look.', 'not yet': 'Not there yet - re-read the lesson, then try again.', 'memorised, not understood': 'Your first answer was good, but the follow-ups showed the idea is not fully understood yet.' }[d.level] || '';
      const weak = run.rounds.filter((x) => x.model_answer && x.score < 75);
      body(`<div class="ex-score ${d.score >= 75 ? 'correct' : d.score >= 40 ? 'partly' : 'incorrect'}"><b>${d.score}%</b><span>${esc(d.level)}</span></div>${bar(d.score)}` +
        `<p class="muted">${esc(msg)} (${d.rounds} question${d.rounds === 1 ? '' : 's'}; later questions count more.)</p>` +
        (d.gaps.length ? `<div class="ex-sec miss"><b>Gaps to review</b><ul>${d.gaps.map((g) => `<li>${esc(g)}</li>`).join('')}</ul></div>` : '') +
        `<div class="lab-bar">${b.course_id && weak.length ? '<button class="btn" data-cards>Add what I missed to my flashcards</button>' : ''}<button class="btn" data-again>Try another question</button></div>`);
      $('[data-again]', box).onclick = () => { run.rounds = []; run.q = ''; intro(); };
      const cb = $('[data-cards]', box);
      if (cb) cb.onclick = async () => {
        let n = 0;
        for (const r of weak) { const out = await api('POST', `/api/learn/courses/${b.course_id}/cards`, { front: r.q.slice(0, 400), back: r.model_answer.slice(0, 400) }).catch(() => null); n += out && out.added ? out.added : 0; }
        cb.disabled = true; cb.textContent = n ? `Added ${n} card${n === 1 ? '' : 's'}` : 'Already in your cards';
      };
    };
    intro();
  }

  function mountCards(box) {
    box.querySelectorAll('.fc-card').forEach((c) => c.addEventListener('click', () => c.classList.toggle('flip')));
    const r = $('[data-review]', box);
    if (r) r.addEventListener('click', () => document.dispatchEvent(new CustomEvent('learn-open', { detail: { view: 'review', course: box.dataset.course } })));
  }

  /** Attach behaviour to every lab block inside a rendered skill card. `blockList` are the block descriptors in order. */
  function mount(holder, run, blockList) {
    const nodes = [...holder.querySelectorAll('.lab, .fc-block')];
    const labs = (blockList || []).filter((b) => blocks[b.type]);
    nodes.forEach((node, i) => {
      const b = labs[i]; if (!b) return;
      try {
        if (b.type === 'codelab') mountCode(node, run, b);
        else if (b.type === 'tryit') mountWeb(node);
        else if (b.type === 'mathlab') mountMath(node, b);
        else if (b.type === 'flashcards') mountCards(node);
        else if (b.type === 'check') mountCheck(node, b);
      } catch (_) { /* a broken lab must not break the lesson */ }
    });
  }

  return { blocks, mount, mountCode, mountWeb, mountMath, api, esc };
})();
