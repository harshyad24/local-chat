/* Skills UI: the "/" command menu, the active-skill chip, question cards, progress, and rich result blocks (tables, comparison, itinerary,
   timetable, calendar, map, quiz...). One assistant with five broad commands; the internal workflows never appear as commands. */
const SkillView = (() => {
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const safeUrl = (u) => (/^https?:\/\//i.test(u || '') ? u : '#');
  const $ = (sel, root = document) => root.querySelector(sel);
  const svg = (p) => `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${p}</svg>`;
  const ICON = {
    planning: svg('<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>'),
    compare: svg('<path d="M8 3L4 7l4 4M4 7h12M16 21l4-4-4-4M20 17H8"/>'),
    analyze: svg('<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>'),
    create: svg('<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/>'),
    learn: svg('<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v16H6.5A2.5 2.5 0 0 0 4 21.5zM4 19.5A2.5 2.5 0 0 1 6.5 17H20"/>'),
  };
  const ico = (cmd) => ICON[cmd.replace('/', '')] || ICON.create;
  let COMMANDS = [
    { command: '/planning', hint: 'Trips, study timetables, outings, events, schedules and routines', examples: ['Plan a 5-day trip to Goa'] },
    { command: '/compare', hint: 'Compare products, universities, offers or options side by side', examples: ['Compare smartphones under ₹30,000'] },
    { command: '/analyze', hint: 'Analyze an attached spreadsheet, dataset or document', examples: ['Analyze this Excel sales dataset'] },
    { command: '/create', hint: 'Create documents, presentations, dashboards, code and more', examples: ['Create an executive business dashboard'] },
    { command: '/learn', hint: 'A personalised course, explanation, quiz or interview preparation', examples: ['Teach me Python from beginner to advanced'] },
  ];
  const api = async (method, url, body) => {
    const res = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(d.detail || 'Request failed');
    return d;
  };
  const submit = (detail) => document.dispatchEvent(new CustomEvent('skill-submit', { detail }));

  /* ------------------------------------------------------------ result blocks */
  const B = {
    overview: (b) => `<div class="sk-overview"><div class="sk-ov-title">${esc(b.title)}</div><p>${esc(b.text)}</p>` +
      (b.kpis && b.kpis.length ? `<div class="sk-kpis">${b.kpis.map((k) => `<div class="sk-kpi"><div class="sk-kpi-v">${esc(k.value)}</div><div class="sk-kpi-l">${esc(k.label)}</div>${k.sub ? `<div class="sk-kpi-s">${esc(k.sub)}</div>` : ''}</div>`).join('')}</div>` : '') + '</div>',
    kpis: (b) => `<div class="sk-kpis">${b.items.map((k) => `<div class="sk-kpi"><div class="sk-kpi-v">${esc(k.value)}</div><div class="sk-kpi-l">${esc(k.label)}</div>${k.sub ? `<div class="sk-kpi-s">${esc(k.sub)}</div>` : ''}</div>`).join('')}</div>`,
    table: (b) => `<div class="sk-sec">${b.title ? `<div class="sk-sec-t">${esc(b.title)}</div>` : ''}<div class="sk-tablewrap"><table><thead><tr>${b.columns.map((c) => `<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${b.rows.map((r) => `<tr>${r.map((c, i) => `<td>${/^https?:\/\//.test(String(c)) ? `<a href="${esc(safeUrl(c))}" target="_blank" rel="noopener noreferrer">open ↗</a>` : esc(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>${b.note ? `<div class="muted small">${esc(b.note)}</div>` : ''}</div>`,
    comparison: (b) => `<div class="sk-sec"><div class="sk-sec-t">Side by side</div><div class="sk-tablewrap"><table class="sk-cmp"><thead><tr><th>Criterion</th>${b.items.map((n, i) => `<th>${b.urls && b.urls[i] ? `<a href="${esc(safeUrl(b.urls[i]))}" target="_blank" rel="noopener noreferrer">${esc(n)}</a>` : esc(n)}</th>`).join('')}<th>Weight</th></tr></thead><tbody>` +
      b.rows.map((r) => `<tr><td><b>${esc(r.name)}</b><div class="muted small">${esc(r.direction)}</div></td>${r.values.map((v, i) => `<td class="${r.best === i ? 'best' : ''}">${v ? esc(v) : '<span class="muted">not found</span>'}${r.best === i ? ' ✓' : ''}</td>`).join('')}<td>${r.weight}/5</td></tr>`).join('') +
      `</tbody></table></div><div class="muted small">${esc(b.explain)}</div></div>`,
    scores: (b) => `<div class="sk-sec"><div class="sk-sec-t">Weighted score</div>${b.rows.map((r) => `<div class="sk-bar"><span class="sk-bar-l">${esc(r.item)}</span><span class="sk-bar-track"><span style="width:${r.pct == null ? 0 : r.pct}%"></span></span><span class="sk-bar-v">${r.pct == null ? 'n/a' : r.pct + '%'}</span><span class="muted small">${r.criteria_used} criteria scored</span></div>`).join('')}</div>`,
    proscons: (b) => `<div class="sk-sec"><div class="sk-sec-t">Strengths and weaknesses (from the numbers)</div><div class="sk-pc">${b.rows.map((r) => `<div class="sk-pc-card"><b>${esc(r.item)}</b>${r.pros.map((p) => `<div class="pro">＋ ${esc(p)}</div>`).join('')}${r.cons.map((p) => `<div class="con">－ ${esc(p)}</div>`).join('')}${!r.pros.length && !r.cons.length ? '<div class="muted small">No standout numbers</div>' : ''}</div>`).join('')}</div></div>`,
    recommendation: (b) => `<div class="sk-reco"><div class="sk-sec-t">Recommendation</div><p>${esc(b.text)}</p>${b.caveat ? `<div class="muted small">${esc(b.caveat)}</div>` : ''}</div>`,
    markdown: (b) => `<div class="sk-sec">${b.title ? `<div class="sk-sec-t">${esc(b.title)}</div>` : ''}<div class="sk-md">${typeof Render !== 'undefined' && Render.markdown ? Render.markdown(b.text) : esc(b.text).replace(/\n/g, '<br>')}</div></div>`,
    links: (b) => `<div class="sk-sec"><div class="sk-sec-t">${esc(b.title || 'Links')}</div><div class="sk-links">${b.items.map((l) => `<a class="sk-link" href="${esc(safeUrl(l.url))}" target="_blank" rel="noopener noreferrer"><b>${esc(l.label)}</b>${l.note ? `<span class="muted small">${esc(l.note)}</span>` : ''}</a>`).join('')}</div></div>`,
    itinerary: (b) => `<div class="sk-sec"><div class="sk-sec-t">${esc(b.title || 'Itinerary')}</div>${b.days.map((d) => `<div class="sk-day"><div class="sk-day-h"><b>${esc(d.title)}</b>${d.date ? `<span class="muted"> · ${esc(d.date)}</span>` : ''}${d.summary ? `<span class="muted small"> · ${esc(d.summary)}</span>` : ''}</div>` +
      d.items.map((it) => `<div class="sk-stop"><span class="sk-time">${esc(it.time || '')}</span><span class="sk-stop-b"><b>${esc(it.title)}</b>${it.note ? `<div class="muted small">${esc(it.note)}</div>` : ''}${it.km ? `<div class="muted small">≈ ${esc(it.km)} km from the previous stop</div>` : ''}</span>${it.link ? `<a class="sk-mini" href="${esc(safeUrl(it.link))}" target="_blank" rel="noopener noreferrer">map ↗</a>` : ''}</div>`).join('') + '</div>').join('')}</div>`,
    budget: (b) => `<div class="sk-sec"><div class="sk-sec-t">${esc(b.title || 'Budget')}</div><div class="sk-tablewrap"><table><thead><tr><th>Item</th><th>Planned</th><th>Found online</th><th>Note</th></tr></thead><tbody>${b.rows.map((r) => `<tr><td>${esc(r.label)}</td><td>${esc(r.planned == null ? '—' : r.planned)}</td><td>${esc(r.found == null ? '—' : r.found)}</td><td class="muted small">${esc(r.note || '')}</td></tr>`).join('')}</tbody>${b.total ? `<tfoot><tr><td><b>Total</b></td><td><b>${esc(b.total.planned)}</b></td><td><b>${esc(b.total.found || '')}</b></td><td></td></tr></tfoot>` : ''}</table></div>${b.note ? `<div class="muted small">${esc(b.note)}</div>` : ''}</div>`,
    countdown: (b) => `<div class="sk-kpis">${b.items.map((c) => `<div class="sk-kpi ${c.days <= 7 ? 'urgent' : ''}"><div class="sk-kpi-v">${c.days}</div><div class="sk-kpi-l">days to ${esc(c.label)}</div><div class="sk-kpi-s">${esc(c.date)}</div></div>`).join('')}</div>`,
    findings: (b) => `<div class="sk-sec"><div class="sk-sec-t">${esc(b.title || 'Findings')}</div><ul class="sk-find">${b.items.map((f) => `<li class="${esc(f.kind || 'finding')}"><span class="tag">${f.kind === 'assumption' ? 'assumption' : f.kind === 'warning' ? 'check' : 'finding'}</span> ${esc(f.text)}</li>`).join('')}</ul></div>`,
  };
  B.dq = (b) => B.table({ title: b.title || 'Data quality', columns: ['Column', 'Type', 'Missing', 'Unique', 'Note'], rows: b.rows.map((r) => [r.column, r.type, r.missing_pct + '%', r.unique, r.note || '']), note: b.summary });

  function calendarHTML(b) {
    const evs = b.events || [];
    if (!evs.length) return '';
    const dates = evs.map((e) => e.date).sort();
    const start = new Date(b.month_start || dates[0] + 'T00:00:00');
    const last = new Date(dates[dates.length - 1] + 'T00:00:00');
    const months = [];
    for (let d = new Date(start.getFullYear(), start.getMonth(), 1); d <= last && months.length < 4; d = new Date(d.getFullYear(), d.getMonth() + 1, 1)) months.push(new Date(d));
    return `<div class="sk-sec"><div class="sk-sec-t">${esc(b.title || 'Calendar')}</div>` + months.map((m) => {
      const first = (new Date(m.getFullYear(), m.getMonth(), 1).getDay() + 6) % 7; // Monday first
      const days = new Date(m.getFullYear(), m.getMonth() + 1, 0).getDate();
      let cells = '';
      for (let i = 0; i < first; i++) cells += '<div class="cal-c empty"></div>';
      for (let day = 1; day <= days; day++) {
        const iso = `${m.getFullYear()}-${String(m.getMonth() + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
        const es = evs.filter((e) => e.date === iso);
        cells += `<div class="cal-c ${es.length ? 'has' : ''}"><span class="cal-d">${day}</span>${es.slice(0, 3).map((e) => `<span class="cal-e ${esc(e.kind || '')}" title="${esc(e.title)}">${esc(e.title)}</span>`).join('')}${es.length > 3 ? `<span class="cal-more">+${es.length - 3}</span>` : ''}</div>`;
      }
      return `<div class="cal"><div class="cal-h">${m.toLocaleString(undefined, { month: 'long', year: 'numeric' })}</div><div class="cal-g"><div class="cal-w">Mon</div><div class="cal-w">Tue</div><div class="cal-w">Wed</div><div class="cal-w">Thu</div><div class="cal-w">Fri</div><div class="cal-w">Sat</div><div class="cal-w">Sun</div>${cells}</div></div>`;
    }).join('') + '</div>';
  }
  B.calendar = calendarHTML;

  /* ------------------------------------------------------------ interactive blocks (state is saved on the server) */
  function mountChecks(holder, run) {
    holder.querySelectorAll('input[data-check]').forEach((box) => {
      const key = box.dataset.key;
      const saved = (run.state && run.state[key]) || [];
      box.checked = saved.includes(box.dataset.check);
      box.addEventListener('change', async () => {
        const boxes = [...holder.querySelectorAll(`input[data-key="${CSS.escape(key)}"]`)];
        const val = boxes.filter((x) => x.checked).map((x) => x.dataset.check);
        run.state = { ...(run.state || {}), [key]: val };
        const done = holder.querySelector(`[data-progress="${CSS.escape(key)}"]`);
        if (done) done.textContent = `${val.length} of ${boxes.length} done (${Math.round((100 * val.length) / Math.max(1, boxes.length))}%)`;
        try { await api('PUT', `/api/skills/runs/${run.id}/state`, { key, value: val }); } catch (_) { /* keeps working offline */ }
      });
    });
    holder.querySelectorAll('[data-progress]').forEach((el) => {
      const boxes = holder.querySelectorAll(`input[data-key="${CSS.escape(el.dataset.progress)}"]`);
      const n = [...boxes].filter((x) => x.checked).length;
      el.textContent = `${n} of ${boxes.length} done (${Math.round((100 * n) / Math.max(1, boxes.length))}%)`;
    });
  }
  B.timetable = (b) => `<div class="sk-sec"><div class="sk-sec-t">${esc(b.title || 'Timetable')}</div><div class="sk-progress" data-progress="${esc(b.key)}"></div>${b.rows.map((r) => `<div class="sk-day"><div class="sk-day-h"><b>${esc(r.day)}</b><span class="muted"> · ${esc(r.date)}</span>${r.note ? `<span class="muted small"> · ${esc(r.note)}</span>` : ''}</div>${r.slots.map((s) => `<label class="sk-slot ${esc(s.kind)}"><input type="checkbox" data-key="${esc(b.key)}" data-check="${esc(s.id)}"><span class="sk-time">${esc(s.time)}</span><span class="sk-slot-b"><b>${esc(s.subject)}</b>${s.topic ? ` <span class="muted small">· ${esc(s.topic)}</span>` : ''}</span><span class="tag ${esc(s.kind)}">${esc(s.kind)}</span></label>`).join('')}</div>`).join('')}</div>`;
  B.checklist = (b) => `<div class="sk-sec"><div class="sk-sec-t">${esc(b.title || 'Checklist')}</div><div class="sk-progress" data-progress="${esc(b.key)}"></div>${b.items.map((it) => `<label class="sk-slot"><input type="checkbox" data-key="${esc(b.key)}" data-check="${esc(it.id)}"><span class="sk-time">${esc(it.due || '')}</span><span class="sk-slot-b">${esc(it.text)}</span></label>`).join('')}</div>`;
  B.path = (b) => `<div class="sk-sec"><div class="sk-sec-t">${esc(b.title || 'Learning path')}</div><div class="sk-progress" data-progress="${esc(b.key)}"></div>${b.modules.map((m, i) => `<label class="sk-slot ${i === b.current ? 'current' : ''}"><input type="checkbox" data-key="${esc(b.key)}" data-check="m${i}"><span class="sk-time">${i + 1}</span><span class="sk-slot-b"><b>${esc(m.title)}</b><div class="muted small">${esc(m.goal || '')}</div>${(m.lessons || []).length ? `<div class="muted small">${m.lessons.map(esc).join(' · ')}</div>` : ''}</span>${i === b.current ? '<span class="tag study">now</span>' : ''}</label>`).join('')}</div>`;
  B.quiz = (b) => `<div class="sk-sec sk-quiz" data-qkey="${esc(b.key)}" data-module="${esc(b.module)}"><div class="sk-sec-t">${esc(b.title || 'Quiz')}</div>${b.questions.map((q, qi) => `<div class="sk-q" data-qi="${qi}" data-answer="${q.answer}"><div class="sk-q-t">${qi + 1}. ${esc(q.q)}</div>${q.options.map((o, oi) => `<button class="sk-opt" data-oi="${oi}">${esc(o)}</button>`).join('')}<div class="sk-expl muted small hidden">${esc(q.explain || '')}</div></div>`).join('')}<div class="sk-quiz-foot"><span class="sk-score muted"></span></div></div>`;

  function mountQuiz(holder, run, detail) {
    holder.querySelectorAll('.sk-quiz').forEach((quiz) => {
      const qs = [...quiz.querySelectorAll('.sk-q')];
      let answered = 0, right = 0;
      const missed = [];
      qs.forEach((q) => q.querySelectorAll('.sk-opt').forEach((btn) => btn.addEventListener('click', async () => {
        if (q.dataset.done) return;
        q.dataset.done = '1';
        const ok = Number(btn.dataset.oi) === Number(q.dataset.answer);
        btn.classList.add(ok ? 'right' : 'wrong');
        if (!ok) { q.querySelector(`.sk-opt[data-oi="${q.dataset.answer}"]`).classList.add('right'); missed.push(Number(q.dataset.qi)); }
        q.querySelector('.sk-expl').classList.remove('hidden');
        answered++; if (ok) right++;
        quiz.querySelector('.sk-score').textContent = `${right} of ${answered} correct so far`;
        if (answered === qs.length) {
          const pct = Math.round((100 * right) / qs.length);
          quiz.querySelector('.sk-score').innerHTML = `<b>Score ${right}/${qs.length} (${pct}%)</b> — ${pct >= 80 ? 'the next lesson will be harder' : pct <= 40 ? 'the next lesson will go slower and revisit what you missed' : 'the next lesson keeps this level'}.`;
          const hist = ((run.state && run.state.quiz_history) || []).concat([{ module: quiz.dataset.module, right, total: qs.length, missed }]);
          run.state = { ...(run.state || {}), quiz_history: hist };
          try { await api('PUT', `/api/skills/runs/${run.id}/state`, { key: 'quiz_history', value: hist }); } catch (_) { /* optional */ }
          api('POST', '/api/learn/events', { course_id: run.id, kind: 'quiz', module: quiz.dataset.module, right, total: qs.length, detail: { missed } }).catch(() => {});
          const nextBtn = holder.querySelector('[data-next]');
          if (nextBtn) nextBtn.classList.remove('hidden');
        }
      })));
    });
  }

  /* ------------------------------------------------------------ cards */
  function slotsLine(d) {
    return (d.slots || []).length ? `<div class="sk-slots">${d.slots.map((s) => `<span class="sk-chip"><i>${esc(s.label)}</i> ${esc(s.value)}</span>`).join('')}</div>` : '';
  }
  function head(d) {
    return `<div class="sk-head"><span class="sk-ico">${ico(d.skill || 'create')}</span><div><div class="sk-title">/${esc(d.skill)} · ${esc(d.title)}</div><div class="muted small">${esc(d.description || '')}</div></div></div>`;
  }

  function fieldHTML(q, i) {
    if (q.kind === 'file') return `<div class="sk-field" data-slot="${esc(q.slot)}" data-kind="file"><label>📎 ${esc(q.question)}</label></div>`;
    const id = `skq-${i}-${Math.random().toString(36).slice(2, 7)}`;
    let input;
    if (q.kind === 'choice') {
      input = `<div class="sk-choices">${q.choices.map((c) => `<button type="button" class="sk-choice" data-val="${esc(c)}">${esc(c)}</button>`).join('')}</div><input type="text" class="sk-in" id="${id}" placeholder="or type your own">`;
    } else if (q.kind === 'date') {
      input = `<div class="sk-dates"><input type="date" class="sk-in sk-d1"> <span class="muted">to</span> <input type="date" class="sk-in sk-d2"></div><input type="text" class="sk-in" id="${id}" placeholder="or type, e.g. “15 Dec to 20 Dec”">`;
    } else if (q.kind === 'number') {
      input = `<input type="text" inputmode="decimal" class="sk-in" id="${id}" placeholder="${esc(q.hint || 'a number')}">`;
    } else {
      input = `<textarea class="sk-in" id="${id}" rows="${q.kind === 'list' ? 2 : 1}" placeholder="${esc(q.hint || (q.kind === 'list' ? 'separate with commas' : 'your answer'))}"></textarea>`;
    }
    const ex = q.examples && q.examples.length ? `<div class="sk-ex">${q.examples.map((e) => `<button type="button" class="sk-choice" data-val="${esc(e)}">${esc(e)}</button>`).join('')}</div>` : '';
    return `<div class="sk-field" data-slot="${esc(q.slot)}" data-kind="${esc(q.kind)}"><label for="${id}">${esc(q.question)}</label>${ex}${input}${q.hint && q.kind !== 'text' && q.kind !== 'list' ? `<div class="muted small">${esc(q.hint)}</div>` : ''}</div>`;
  }

  function collect(form) {
    const out = {};
    form.querySelectorAll('.sk-field').forEach((f) => {
      let v = f.dataset.value || (f.querySelector('.sk-in:not([type=date])') || {}).value || '';
      if (f.dataset.kind === 'date' && !v.trim()) {
        const a = f.querySelector('.sk-d1').value, b = f.querySelector('.sk-d2').value;
        if (a) v = a + (b ? ` to ${b}` : '');
      }
      if (String(v).trim()) out[f.dataset.slot] = String(v).trim();
    });
    return out;
  }

  function renderAsking(holder, d) {
    holder.innerHTML = `<div class="sk-card asking">${head(d)}${slotsLine(d)}<form class="sk-form">${d.questions.map(fieldHTML).join('')}` +
      `<div class="sk-actions"><button class="btn primary${d.questions.every((q) => q.kind === 'file') ? ' hidden' : ''}" type="submit">Continue</button>${d.run_id ? '<button class="btn" type="button" data-skip>Skip, use sensible defaults</button>' : ''}</div></form></div>`;
    const form = $('.sk-form', holder);
    form.addEventListener('click', (e) => {
      const c = e.target.closest('.sk-choice');
      if (!c) return;
      const f = c.closest('.sk-field');
      if (f.dataset.slot === '_request') { f.querySelector('.sk-in').value = c.dataset.val; return; }
      f.dataset.value = c.dataset.val;
      f.querySelectorAll('.sk-choice').forEach((x) => x.classList.toggle('on', x === c));
      const inp = f.querySelector('.sk-in:not([type=date])'); if (inp) inp.value = '';
    });
    form.addEventListener('input', (e) => { if (e.target.classList.contains('sk-in')) { const f = e.target.closest('.sk-field'); if (!e.target.type || e.target.type !== 'date') { delete f.dataset.value; f.querySelectorAll('.sk-choice').forEach((x) => x.classList.remove('on')); } } });
    const done = (answers, text) => {
      if (answers._request) { holder.classList.add('answered'); submit({ text: `/${d.skill} ${answers._request}` }); return; }
      holder.classList.add('answered');
      form.querySelectorAll('input,textarea,button').forEach((x) => { x.disabled = true; });
      submit({ text, runId: d.run_id, answers });
    };
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      const answers = collect(form);
      if (!Object.keys(answers).length) { form.querySelector('.sk-in').focus(); return; }
      done(answers, Object.entries(answers).map(([k, v]) => `${(d.questions.find((q) => q.slot === k) || {}).label || k}: ${v}`).join(' · '));
    });
    const skip = $('[data-skip]', holder);
    if (skip) skip.addEventListener('click', () => {
      const answers = {};
      d.questions.forEach((q) => { answers[q.slot] = 'flexible'; });
      done(answers, 'No preference — use sensible defaults.');
    });
  }

  function reviseHTML(d) {
    return `<form class="sk-form sk-revise hidden">${(d.slot_defs || []).map((s) => `<div class="sk-field" data-slot="${esc(s.name)}" data-kind="${esc(s.kind)}"><label>${esc(s.label)}</label><input type="text" class="sk-in" value="${esc(s.value && typeof s.value === 'object' && !Array.isArray(s.value) ? (s.value.start || '') + (s.value.end && s.value.end !== s.value.start ? ' to ' + s.value.end : '') : Array.isArray(s.value) ? s.value.join(', ') : s.value == null ? '' : s.value)}"></div>`).join('')}<div class="sk-actions"><button class="btn primary" type="submit">Update the result</button></div></form>`;
  }

  function renderDone(holder, d, ctx) {
    const run = { id: d.run_id, state: d.state || {} };
    const files = (d.files || []).length ? `<div class="sk-files">${d.files.map((f) => `<a class="sk-file" href="${esc(f.url)}" download>⬇ ${esc(f.name)}</a>`).join('')}</div>` : '';
    const notes = (d.notes || []).length ? `<div class="sk-notes">${d.notes.map((n) => `<div>ⓘ ${esc(n)}</div>`).join('')}</div>` : '';
    holder.innerHTML = `<div class="sk-card done">${head(d)}${slotsLine(d)}<div class="sk-blocks"></div>${notes}${files}` +
      `<div class="sk-foot"><button class="btn" data-revise>Revise</button>${d.skill === 'learn' ? '<button class="btn primary" data-next>Next lesson ▸</button>' : ''}` +
      `<span class="sk-fb"><button class="sk-thumb" data-fb="up" title="Helpful">👍</button><button class="sk-thumb" data-fb="down" title="Not helpful">👎</button></span></div>` +
      `${reviseHTML(d)}<div class="sk-fbbox hidden"><textarea class="sk-in" rows="2" placeholder="What should be different next time? e.g. “I prefer trains over flights”, “shorter answers”, “include more charts”"></textarea><button class="btn primary" data-fbsend>Save feedback</button><div class="sk-fbres muted small"></div></div></div>`;
    const box = $('.sk-blocks', holder);
    for (const b of d.blocks || []) {
      const el = document.createElement('div');
      el.className = 'sk-block sk-b-' + b.type;
      box.appendChild(el);
      try {
        if (b.type === 'chart' && typeof Viz !== 'undefined') { el.className += ' viz-block'; Viz.renderSpec(el, b.spec); }
        else if (b.type === 'dashboard' && typeof Viz !== 'undefined') { el.className += ' dash-block'; Viz.renderDashboard(el, b.spec); }
        else if (b.type === 'map' && typeof MapView !== 'undefined') MapView.render(el, b.spec);
        else if (typeof LearnLab !== 'undefined' && LearnLab.blocks[b.type]) el.innerHTML = LearnLab.blocks[b.type](b);
        else if (B[b.type]) el.innerHTML = B[b.type](b);
      } catch (err) { el.innerHTML = `<div class="muted small">Could not display a ${esc(b.type)} block.</div>`; }
    }
    if (typeof Viz !== 'undefined' && Viz.renderAll) { try { Viz.renderAll(holder); } catch (_) { /* optional */ } }
    mountChecks(holder, run);
    mountQuiz(holder, run, d);
    if (typeof LearnLab !== 'undefined') LearnLab.mount(holder, run, d.blocks || []);
    const nextBtn = $('[data-next]', holder);
    if (nextBtn) {
      const hasQuiz = (d.blocks || []).some((b) => b.type === 'quiz');
      if (hasQuiz) nextBtn.classList.add('hidden');
      nextBtn.addEventListener('click', () => { nextBtn.disabled = true; submit({ text: 'Next lesson', runId: d.run_id, answers: { _action: 'next' } }); });
    }
    const rev = $('.sk-revise', holder);
    $('[data-revise]', holder).addEventListener('click', () => rev.classList.toggle('hidden'));
    rev.addEventListener('submit', (e) => {
      e.preventDefault();
      const answers = collect(rev);
      answers._action = 'rerun';
      submit({ text: 'Revise: ' + Object.entries(answers).filter(([k]) => !k.startsWith('_')).map(([k, v]) => `${k}: ${v}`).join(' · '), runId: d.run_id, answers });
    });
    const fb = $('.sk-fbbox', holder);
    let rating = null;
    holder.querySelectorAll('.sk-thumb').forEach((b) => b.addEventListener('click', async () => {
      rating = b.dataset.fb;
      holder.querySelectorAll('.sk-thumb').forEach((x) => x.classList.toggle('on', x === b));
      fb.classList.remove('hidden');
      try { await api('POST', `/api/skills/runs/${d.run_id}/feedback`, { rating, text: '' }); } catch (_) { /* optional */ }
    }));
    $('[data-fbsend]', holder).addEventListener('click', async () => {
      const text = $('textarea', fb).value.trim();
      if (!text) return;
      try {
        const r = await api('POST', `/api/skills/runs/${d.run_id}/feedback`, { rating, text });
        $('.sk-fbres', fb).innerHTML = r.saved.length ? 'Saved for next time: ' + r.saved.map((p) => `<b>${esc(p.key)}</b> = ${esc(p.value)}`).join(', ') + '. You can review or delete these in Library → Skill preferences.' : 'Thanks — noted.';
      } catch (err) { $('.sk-fbres', fb).textContent = err.message; }
    });
  }

  function render(holder, d, ctx) {
    holder.className = 'sk-holder';
    if (!d) return;
    if (d.status === 'asking') renderAsking(holder, d);
    else if (d.status === 'error') holder.innerHTML = `<div class="sk-card error">${head(d)}${slotsLine(d)}<div class="sk-notes">${esc(d.error || 'The workflow failed.')}</div></div>`;
    else renderDone(holder, d, ctx);
  }

  /* ------------------------------------------------------------ progress (while a workflow runs) */
  function progress(box, ev) {
    if (!box) return;
    if (!ev) { box.classList.add('hidden'); box.innerHTML = ''; return; }
    box.innerHTML = `<div class="prog-detail"><b>Step ${ev.step} of ${ev.total}</b> — ${esc(ev.label)}</div><div class="sk-prog-track"><span style="width:${Math.min(100, Math.round((100 * ev.step) / ev.total))}%"></span></div>`;
    box.classList.remove('hidden');
  }

  /* ------------------------------------------------------------ "/" menu and active-skill chip */
  let composerReady = false;
  function initComposer() {
    if (composerReady) return;
    const input = document.getElementById('input');
    const comp = document.getElementById('composer');
    if (!input || !comp) return;
    composerReady = true;
    const menu = document.createElement('div');
    menu.id = 'skill-menu';
    menu.className = 'skill-menu hidden';
    menu.setAttribute('role', 'listbox');
    comp.appendChild(menu);
    const chip = document.createElement('div');
    chip.id = 'skill-chip';
    chip.className = 'skill-chip hidden';
    comp.insertBefore(chip, input);
    let idx = 0, shown = [];
    fetch('/api/skills').then((r) => r.json()).then((d) => { if (d && d.commands) COMMANDS = d.commands; if (d && d.enabled === false) comp.dataset.noskills = '1'; }).catch(() => {});

    const active = () => { const m = /^\s*\/(planning|compare|analy[sz]e|create|learn)\b/i.exec(input.value); return m ? COMMANDS.find((c) => c.command === '/' + m[1].toLowerCase().replace('analyse', 'analyze')) : null; };
    function paintChip() {
      const a = active();
      if (!a) { chip.classList.add('hidden'); return; }
      chip.innerHTML = `<span class="sk-ico">${ico(a.command)}</span><b>${esc(a.command)}</b><button type="button" class="sk-x" aria-label="Remove command">×</button>`;
      chip.classList.remove('hidden');
    }
    chip.addEventListener('click', (e) => { if (e.target.closest('.sk-x')) { input.value = input.value.replace(/^\s*\/\w+\s*/, ''); input.dispatchEvent(new Event('input', { bubbles: true })); input.focus(); } });
    function paintMenu() {
      const m = /^\/(\w*)$/.exec(input.value.trimStart().split('\n')[0] && !/\s/.test(input.value.trim()) ? input.value.trim() : '');
      if (!m || comp.dataset.noskills) { menu.classList.add('hidden'); shown = []; return; }
      shown = COMMANDS.filter((c) => c.command.slice(1).startsWith(m[1].toLowerCase()));
      if (!shown.length || (shown.length === 1 && shown[0].command === input.value.trim())) { menu.classList.add('hidden'); shown = []; return; }
      idx = Math.min(idx, shown.length - 1);
      menu.innerHTML = shown.map((c, i) => `<div class="skill-opt ${i === idx ? 'on' : ''}" role="option" data-i="${i}"><span class="sk-ico">${ico(c.command)}</span><b>${esc(c.command)}</b><span class="muted small sk-hint">${esc(c.hint)}</span></div>`).join('');
      menu.classList.remove('hidden');
    }
    function pick(i) {
      const c = shown[i];
      if (!c) return;
      input.value = c.command + ' ';
      menu.classList.add('hidden');
      shown = [];
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.focus();
    }
    menu.addEventListener('mousedown', (e) => { const o = e.target.closest('.skill-opt'); if (o) { e.preventDefault(); pick(Number(o.dataset.i)); } });
    input.addEventListener('input', () => { idx = 0; paintMenu(); paintChip(); });
    input.addEventListener('keydown', (e) => {
      if (!shown.length) return;
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); idx = (idx + (e.key === 'ArrowDown' ? 1 : shown.length - 1)) % shown.length; paintMenu(); }
      else if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey)) { e.preventDefault(); e.stopImmediatePropagation(); pick(idx); }
      else if (e.key === 'Escape') { menu.classList.add('hidden'); shown = []; }
    }, true);
    input.addEventListener('blur', () => setTimeout(() => menu.classList.add('hidden'), 150));
    document.addEventListener('skills-sent', () => { chip.classList.add('hidden'); menu.classList.add('hidden'); });
  }

  /* ------------------------------------------------------------ preferences (Library tab) */
  async function renderPrefs(box) {
    let prefs = [];
    try { prefs = await api('GET', '/api/skills/prefs'); } catch (_) { box.innerHTML = '<div class="muted small">Preferences are unavailable.</div>'; return; }
    box.innerHTML = prefs.length
      ? `<table class="sk-prefs"><thead><tr><th>Applies to</th><th>Preference</th><th>Value</th><th></th></tr></thead><tbody>${prefs.map((p) => `<tr data-id="${p.id}"><td>${p.skill === '*' ? 'all skills' : esc(p.skill)}</td><td>${esc(p.key)}</td><td><input class="sk-in" value="${esc(p.value)}"></td><td><button class="btn" data-del>Delete</button></td></tr>`).join('')}</tbody></table>`
      : '<div class="muted small">Nothing saved yet. Use the 👍/👎 and feedback box under a skill result to teach the app what you prefer.</div>';
    box.querySelectorAll('tr[data-id]').forEach((tr) => {
      const id = tr.dataset.id;
      tr.querySelector('input').addEventListener('change', (e) => api('PUT', `/api/skills/prefs/${id}`, { value: e.target.value }).catch(() => {}));
      tr.querySelector('[data-del]').addEventListener('click', async () => { await api('DELETE', `/api/skills/prefs/${id}`).catch(() => {}); renderPrefs(box); });
    });
  }

  document.addEventListener('DOMContentLoaded', initComposer);
  if (document.readyState !== 'loading') initComposer();
  return { render, progress, renderPrefs, commands: () => COMMANDS };
})();
