'use strict';
/* Entity cards: a search-engine style knowledge panel rendered from validated JSON (never from model-written HTML).
   EntityCard.render(container, detail)  — detail is {card} or {compare, cards}. */
const EntityCard = (() => {
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const safe = (u) => (/^https?:\/\//i.test(u || '') ? u : '');
  const host = (u) => { try { return new URL(u).hostname.replace(/^www\./, ''); } catch (_) { return ''; } };

  /* Which facts matter most per kind of thing, and sensible follow-up questions. Unknown families use "thing". */
  const TEMPLATES = {
    person: { facts: [/^born$/i, /birth/i, /nationality|citizen/i, /occupation|profession/i, /education/i, /spouse/i], asks: ['What are their best-known works?', 'Tell me about their early life', 'What are they doing now?'] },
    place: { facts: [/country/i, /located|location|state|region/i, /built|founded|inception|established/i, /architect/i, /style/i, /heritage|status/i, /coordinates/i], asks: ['What is its history?', 'How do I visit it?', 'What are similar places?'] },
    animal: { facts: [/scientific/i, /rank/i, /parent|family|order|class/i, /conservation|status/i, /native|range|habitat/i], asks: ['Where does it live?', 'What does it eat?', 'How is it different from similar species?'] },
    plant: { facts: [/scientific/i, /rank/i, /parent|family/i, /conservation|status/i, /native|range|habitat/i], asks: ['Where does it grow?', 'What is it used for?', 'How do I tell it apart from similar plants?'] },
    instrument: { facts: [/origin/i, /classification/i, /inventor|invented/i, /introduced/i], asks: ['How is it played?', 'How does it produce sound?', 'What are related instruments?'] },
    vehicle: { facts: [/price/i, /manufacturer/i, /engine|powerplant/i, /transmission/i, /released|introduced/i, /length|width|height|mass|weight/i, /speed/i], asks: ['What are its alternatives?', 'What are its pros and cons?', 'How safe is it?'] },
    laptop: { facts: [/price/i, /manufacturer|brand/i, /processor|cpu/i, /graphics|gpu/i, /ram|memory/i, /storage|ssd/i, /display|screen/i, /battery/i], asks: ['What are its pros and cons?', 'What are the alternatives?', 'Is it good for gaming or work?'] },
    smartphone: { facts: [/price/i, /manufacturer|brand/i, /processor|chip|soc/i, /display|screen/i, /camera/i, /battery/i, /ram|storage/i], asks: ['What are its pros and cons?', 'What are the alternatives?', 'How is the camera?'] },
    product: { facts: [/price/i, /manufacturer|brand/i, /released|introduced/i], asks: ['What are its pros and cons?', 'What are the alternatives?'] },
    company: { facts: [/industry/i, /founded/i, /headquarter/i, /founder/i, /ceo/i, /employees/i], asks: ['What are its main products?', 'How did it start?', 'Who are its competitors?'] },
    software: { facts: [/developer/i, /released|introduced/i, /platform/i, /written/i, /license/i], asks: ['What is it used for?', 'What are the alternatives?', 'How do I get started?'] },
    food: { facts: [/origin/i, /ingredient/i], asks: ['How is it made?', 'What is its history?'] },
    thing: { facts: [/origin/i, /introduced|founded|built/i, /manufacturer|creator/i], asks: ['Tell me more about its history', 'What is it used for?'] },
  };
  const FAMILY_LABEL = { person: 'Person', place: 'Place', animal: 'Animal', plant: 'Plant', instrument: 'Instrument', vehicle: 'Vehicle', laptop: 'Laptop', smartphone: 'Smartphone', product: 'Product', company: 'Company', software: 'Software', food: 'Food', thing: '' };

  function splitFacts(e) {
    const tpl = TEMPLATES[e.family] || TEMPLATES.thing;
    const used = new Set();
    const key = [];
    for (const rx of tpl.facts) {
      for (const a of e.attributes) if (!used.has(a) && rx.test(a.label)) { used.add(a); key.push(a); }
    }
    for (const a of e.attributes) if (key.length < 6 && !used.has(a) && !/website|coordinates/i.test(a.label)) { used.add(a); key.push(a); }
    const price = e.attributes.find((a) => a.volatile);
    if (price && !used.has(price)) { used.add(price); key.unshift(price); }
    return { key: key.slice(0, 8), rest: e.attributes.filter((a) => !key.slice(0, 8).includes(a)) };
  }

  const factHTML = (a) =>
    `<div class="ec-fact${a.volatile ? ' vol' : ''}"><dt>${esc(a.label)}</dt><dd>${/^https?:\/\//i.test(a.value) ? `<a href="${esc(safe(a.value.split(';')[0]))}" target="_blank" rel="noopener noreferrer">${esc(host(a.value.split(';')[0]))}</a>` : esc(a.value)}` +
    (a.volatile ? `<small class="ec-asof" title="${esc(a.note || '')}">as of ${esc(a.as_of || 'retrieval')} · check seller</small>` : '') +
    `</dd></div>`;

  function imgTag(im, cls, eager) {
    return `<img class="${cls}" src="${esc(safe(im.thumb_url || im.url))}" alt="${esc(im.title || '')}" ${eager ? '' : 'loading="lazy"'} decoding="async" referrerpolicy="no-referrer">`;
  }

  function creditHTML(im) {
    if (!im) return '';
    const bits = [];
    if (im.credit) bits.push(esc(im.credit));
    if (im.license) bits.push(im.license_url ? `<a href="${esc(safe(im.license_url))}" target="_blank" rel="noopener noreferrer">${esc(im.license)}</a>` : esc(im.license));
    const src = im.source_url ? `<a href="${esc(safe(im.source_url))}" target="_blank" rel="noopener noreferrer">${esc(im.source || host(im.source_url) || 'Source')}</a>` : esc(im.source || '');
    return `<figcaption>${[...bits, src].filter(Boolean).join(' · ')}</figcaption>`;
  }

  /* ---------- lightbox ---------- */
  function lightbox(images, start) {
    let i = start;
    const box = document.createElement('div');
    box.className = 'ec-lb';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-label', 'Image viewer');
    const draw = () => {
      const im = images[i];
      box.innerHTML = `<button class="ec-lb-x" aria-label="Close">✕</button>` +
        (images.length > 1 ? `<button class="ec-lb-prev" aria-label="Previous">‹</button><button class="ec-lb-next" aria-label="Next">›</button>` : '') +
        `<figure><img src="${esc(safe(im.url || im.thumb_url))}" alt="${esc(im.title || '')}" referrerpolicy="no-referrer">${creditHTML(im)}` +
        `<div class="ec-lb-links"><a href="${esc(safe(im.url))}" target="_blank" rel="noopener noreferrer">Open original image</a>${im.source_url ? ` · <a href="${esc(safe(im.source_url))}" target="_blank" rel="noopener noreferrer">Open source page</a>` : ''} · ${i + 1} / ${images.length}</div></figure>`;
      const big = box.querySelector('figure img');
      big.onerror = () => { if (im.thumb_url && big.src !== im.thumb_url) big.src = im.thumb_url; };
    };
    const close = () => { box.remove(); document.removeEventListener('keydown', key, true); };
    const key = (e) => {
      if (e.key === 'Escape') { e.stopPropagation(); close(); }
      else if (e.key === 'ArrowRight' && images.length > 1) { i = (i + 1) % images.length; draw(); }
      else if (e.key === 'ArrowLeft' && images.length > 1) { i = (i - 1 + images.length) % images.length; draw(); }
    };
    box.addEventListener('click', (e) => {
      if (e.target === box || e.target.closest('.ec-lb-x')) close();
      else if (e.target.closest('.ec-lb-next')) { i = (i + 1) % images.length; draw(); }
      else if (e.target.closest('.ec-lb-prev')) { i = (i - 1 + images.length) % images.length; draw(); }
    });
    document.addEventListener('keydown', key, true);
    document.body.appendChild(box);
    draw();
  }

  function plainText(e) {
    const lines = [e.name + (e.category ? ` (${e.category})` : '')];
    if (e.description) lines.push('', e.description);
    if (e.attributes.length) lines.push('', ...e.attributes.map((a) => `${a.label}: ${a.value}`));
    for (const s of e.sections.slice(0, 3)) lines.push('', `${s.title}: ${s.content}`);
    if (e.sources.length) lines.push('', 'Sources:', ...e.sources.slice(0, 5).map((s) => `${s.title} — ${s.url}`));
    return lines.join('\n');
  }

  function toast(m) { document.dispatchEvent(new CustomEvent('viz-toast', { detail: m })); }
  async function copy(text) {
    try { await navigator.clipboard.writeText(text); toast('Copied.'); } catch (_) { toast('Could not copy.'); }
  }
  async function share(title, text) {
    if (navigator.share) { try { await navigator.share({ title, text }); return; } catch (err) { if (err && err.name === 'AbortError') return; } }
    copy(text);
  }
  const ask = (text) => document.dispatchEvent(new CustomEvent('entity-ask', { detail: { text } }));

  /* ---------- single card ---------- */
  function cardHTML(card) {
    const e = card.entity;
    const { key, rest } = splitFacts(e);
    const tpl = TEMPLATES[e.family] || TEMPLATES.thing;
    const hero = e.image;
    const all = [hero, ...e.gallery].filter(Boolean);
    const cat = [FAMILY_LABEL[e.family], e.category && e.category.toLowerCase() !== (FAMILY_LABEL[e.family] || '').toLowerCase() ? e.category : ''].filter(Boolean).join(' · ');
    const gallery = (card.mode === 'images' ? all : e.gallery);
    return `<article class="ec ec-${esc(e.family)}${card.mode === 'images' ? ' ec-images' : ''}" data-name="${esc(e.name)}">` +
      `<div class="ec-top">` +
      (hero && card.mode !== 'images' ? `<figure class="ec-hero" data-i="0"><button class="ec-hero-btn" aria-label="View image larger">${imgTag(hero, 'ec-hero-img', true)}</button>${creditHTML(hero)}</figure>` : '') +
      `<div class="ec-main"><div class="ec-cat">${esc(cat)}</div><h3 class="ec-name">${esc(e.name)}</h3>` +
      (e.description ? `<p class="ec-desc">${esc(e.description)}</p>` : '') +
      (key.length ? `<dl class="ec-facts">${key.map(factHTML).join('')}</dl>` : '') +
      `</div></div>` +
      (gallery.length ? `<div class="ec-gallery" role="list">${gallery.map((g, i) => `<button class="ec-thumb" role="listitem" data-i="${card.mode === 'images' ? i : i + 1}" aria-label="${esc(g.title || 'Image ' + (i + 1))}">${imgTag(g, 'ec-thumb-img', false)}</button>`).join('')}</div>` : '') +
      (e.sections.length ? `<div class="ec-sections">${e.sections.map((s, i) => `<details class="ec-sec"${i === 0 ? ' open' : ''}><summary>${esc(s.title)}</summary><p>${esc(s.content)}</p></details>`).join('')}</div>` : '') +
      (rest.length ? `<details class="ec-sec ec-more"><summary>All details (${rest.length})</summary><dl class="ec-facts">${rest.map(factHTML).join('')}</dl></details>` : '') +
      (e.notes.length ? `<ul class="ec-notes">${e.notes.map((n) => `<li>${esc(n)}</li>`).join('')}</ul>` : '') +
      (e.sources.length ? `<div class="ec-sources"><span class="ec-src-label">Sources</span>${e.sources.slice(0, 8).map((s) => `<a class="ec-src" href="${esc(safe(s.url))}" target="_blank" rel="noopener noreferrer" title="${esc(s.title)}${s.date ? ' · ' + esc(s.date) : ''}">${esc(host(s.url) || s.title)}</a>`).join('')}</div>` : '') +
      `<div class="ec-actions"><button data-ec="copy">Copy</button><button data-ec="share">Share</button>${tpl.asks.map((q) => `<button class="ec-ask" data-ask="${esc(q)}">${esc(q)}</button>`).join('')}</div>` +
      `</article>`;
  }

  /* ---------- comparison ---------- */
  function compareHTML(detail) {
    const c = detail.compare, ents = c.entities, n = ents.length;
    const head = ents.map((e) => `<div class="ec-cmp-head">${e.image ? `<figure>${imgTag(e.image, 'ec-cmp-img', true)}</figure>` : ''}<div class="ec-name">${esc(e.name)}</div><div class="ec-cat">${esc(e.category)}</div></div>`).join('');
    const rows = c.rows.map((r) => `<div class="ec-cmp-row${r.differs ? ' differs' : ''}"><div class="ec-cmp-label">${esc(r.label)}${r.volatile ? ' <small>(changes over time)</small>' : ''}</div>${r.values.map((v) => `<div class="ec-cmp-val">${v ? esc(v) : '<span class="muted">not found</span>'}</div>`).join('')}</div>`).join('');
    const srcs = ents.flatMap((e) => e.sources.slice(0, 2).map((s) => ({ ...s, who: e.name })));
    return `<article class="ec ec-compare" style="--n:${n}"><h3 class="ec-name">Comparison</h3>` +
      `<div class="ec-cmp-grid"><div class="ec-cmp-row ec-cmp-top"><div></div>${head}</div>${rows}</div>` +
      (c.notes.length ? `<ul class="ec-notes">${c.notes.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : '') +
      (srcs.length ? `<div class="ec-sources"><span class="ec-src-label">Sources</span>${srcs.slice(0, 10).map((s) => `<a class="ec-src" href="${esc(safe(s.url))}" target="_blank" rel="noopener noreferrer" title="${esc(s.who)}: ${esc(s.title)}">${esc(host(s.url))}</a>`).join('')}</div>` : '') +
      `<div class="ec-actions"><button data-ec="copy">Copy</button><button data-ec="share">Share</button><button class="ec-ask" data-ask="Which one should I pick, and why?">Which one should I pick, and why?</button></div></article>`;
  }

  function wire(root, detail) {
    // images that fail to load: drop the thumb, or hide the hero and fall back to nothing (never a broken-image icon)
    root.querySelectorAll('img').forEach((img) => {
      img.addEventListener('error', () => {
        const btn = img.closest('.ec-thumb, .ec-hero, figure');
        if (img.classList.contains('ec-hero-img')) {
          const e = detail.card && detail.card.entity;
          const next = e && e.gallery.find((g) => g.thumb_url !== img.src);
          if (next && !img.dataset.retried) { img.dataset.retried = '1'; img.src = next.thumb_url || next.url; return; }
        }
        if (btn) btn.classList.add('ec-broken');
        img.remove();
        if (btn && btn.classList.contains('ec-hero')) btn.closest('.ec-top').classList.add('no-hero');
      }, { once: true });
    });
    root.addEventListener('click', (ev) => {
      const t = ev.target;
      const thumb = t.closest('[data-i]');
      if (thumb && detail.card) {
        const e = detail.card.entity;
        const imgs = detail.card.mode === 'images' ? [e.image, ...e.gallery].filter(Boolean) : [e.image, ...e.gallery].filter(Boolean);
        const idx = Number(thumb.dataset.i);
        if (imgs[idx]) lightbox(imgs, idx);
        return;
      }
      const a = t.closest('[data-ask]');
      if (a) { ask(`${detail.card ? detail.card.entity.name + ': ' : ''}${a.dataset.ask}`); return; }
      const b = t.closest('[data-ec]');
      if (!b) return;
      const text = detail.card ? plainText(detail.card.entity) : detail.compare.entities.map((e) => plainText({ ...e })).join('\n\n---\n\n');
      if (b.dataset.ec === 'copy') copy(text);
      else if (b.dataset.ec === 'share') share(detail.card ? detail.card.entity.name : 'Comparison', text);
    });
  }

  function render(container, detail) {
    if (!detail || (!detail.card && !detail.compare)) return;
    container.classList.add('ec-wrap');
    container.innerHTML = detail.compare ? compareHTML(detail) : cardHTML(detail.card);
    wire(container, detail);
  }

  return { render, plainText, splitFacts, TEMPLATES };
})();
if (typeof module !== 'undefined') module.exports = EntityCard;
