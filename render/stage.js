// Builds one storyboard scene as DOM and exposes a deterministic timeline.
// render.mjs calls window.__load(spec) then window.__seek(ms) per frame.
// Motion follows the design system exactly: 160ms quick, 320ms settle, one curve.
import ICONS from './icon-data.js';

const EASE = 'cubic-bezier(0.2, 0.8, 0.2, 1)';
const QUICK = 160, SETTLE = 320, STAGGER = 70;
const tickers = [];   // custom per-frame updaters (count-ups, captions)

const LOGO = `<svg class="logo" viewBox="0 0 100 29" fill="none" aria-label="CheqUp">__PATHS__</svg>`;
const STAR = '<svg viewBox="0 0 24 24"><path d="M12 2.5l2.94 6.08 6.56.82-4.84 4.54 1.24 6.56L12 17.27l-5.9 3.23 1.24-6.56L2.5 9.4l6.56-.82z" fill="var(--color-accent-default)"/></svg>';

function el(tag, cls, html) { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; }
function esc(s) { return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
function icon(name) {
  const i = ICONS['IconName' + name] || ICONS[name];
  return i ? `<svg viewBox="${i.viewBox}" fill="currentColor" aria-hidden="true">${i.body}</svg>` : '';
}

// Reveal: rise 12px + fade, settle duration, the relief curve.
function reveal(node, at, dur = SETTLE, dy = 12) {
  node.animate([{ opacity: 0, transform: `translateY(${dy}px)` }, { opacity: 1, transform: 'none' }],
    { duration: dur, delay: at, easing: EASE, fill: 'both' });
}
function words(node, text, at) {
  node.innerHTML = '';
  String(text).split(/(\s+)/).forEach((t, i) => {
    if (/^\s+$/.test(t)) { node.appendChild(document.createTextNode(t)); return; }
    const s = el('span', 'w', esc(t)); node.appendChild(s); reveal(s, at + (i / 2) * STAGGER, SETTLE, 16);
  });
  return at + (String(text).split(/\s+/).length) * STAGGER + SETTLE;
}
function scene(ground) { const s = el('section', 'scene g-' + (ground || 'sand')); document.getElementById('stage').appendChild(s); return s; }

function exitDivider(s, spec) {
  // PageDivider as motion: next ground rises in over the final settle.
  if (!spec.exit_to) return;
  const d = el('div', 'divider g-' + spec.exit_to); s.parentNode.appendChild(d);
  const start = spec.dur * 1000 - SETTLE * 2;
  d.animate([{ transform: 'translateY(0)' }, { transform: 'translateY(-115vh)' }],
    { duration: SETTLE * 2, delay: start, easing: EASE, fill: 'both' });
}

function fadeIn(node, at, dur = 430) { node.animate([{ opacity: 0 }, { opacity: 1 }], { duration: dur, delay: at, easing: 'linear', fill: 'both' }); }
function fadeOut(node, at, dur = 430) { node.animate([{ opacity: 1 }, { opacity: 0 }], { duration: dur, delay: at, easing: 'linear', fill: 'both' }); }
function logoSvg(w) { const d = el('div', '', LOGO.replace('__PATHS__', window.__LOGO_PATHS || '')); d.firstChild.classList.add('live-logo'); d.firstChild.style.width = w + 'px'; return d.firstChild; }
function plate(s, spec) {
  if (spec.src && !/\.(mp4|mov|webm)$/i.test(spec.src)) {
    const p = el('div', 'live-plate', `<img src="${esc(spec.src)}" alt="">`); s.appendChild(p);
    if (spec.focus) p.firstChild.style.objectPosition = spec.focus;   // e.g. "30% 20%" keeps a face in shorter crops
    if (spec.push !== false) p.firstChild.animate([{ transform: 'scale(1)' }, { transform: 'scale(1.05)' }], { duration: spec.dur * 1000, easing: 'linear', fill: 'both' });
  } else if (!spec.src) s.appendChild(el('div', 'live-grad'));
  if (spec.wash !== false && spec.src) {
    const w = spec.wash || (spec.type === 'glass' ? 'top' : spec.type === 'logo' && spec.headline ? 'intro' : 'bottom');
    s.appendChild(el('div', { top: 'live-topwash', intro: 'live-introwash', bottom: 'live-wash' }[w] || 'live-wash'));
  }
}
function legal(s, spec, top) {
  if (!spec.legal) return;
  const l = el('div', 'live-legal', spec.legal_html ? spec.legal : esc(spec.legal)); l.style.top = top; s.appendChild(l); return l;
}

// meta-live scene builders (replica of the current ad template — see reference/meta-account/LOOK.md)
const LIVE = {
  // 0–1.3 s: wordmark fades in (430 ms) centred on the deep gradient, or over the hero plate.
  logo(spec) {
    const s = scene('none'); s.style.padding = '0'; plate(s, spec);
    const c = el('div', 'live-center'); c.style.top = spec.headline ? '48%' : (spec.src ? '30%' : '40%'); c.appendChild(logoSvg(spec.src ? 175 : 170)); s.appendChild(c);
    if (spec.headline) {   // LTN pattern: hook line under the wordmark from frame 0
      c.insertAdjacentHTML('beforeend', `<h1 class="live-h" style="font-size:25px;margin-top:10px;padding:0 9%">${esc(spec.headline)}</h1>${spec.body ? `<p class="live-b" style="padding:0 12%">${esc(spec.body)}</p>` : ''}`);
    } else fadeIn(c, 0);
    const l = legal(s, spec, spec.headline ? '74%' : '62%'); if (l) fadeIn(l, 0);
    return s;
  },
  // Hero plate + frosted card (wordmark, headline, body, cyan CTA); at switch_at the card
  // dissolves into a frosted quote card. Price roundel optional.
  glass(spec) {
    const s = scene('none'); s.style.padding = '0'; plate(s, spec);
    const T = (spec.switch_at ?? spec.dur * 0.45) * 1000;
    const card = el('div', 'live-card' + (spec.card_align === 'center' ? ' center' : ''));
    card.appendChild(logoSvg(62)).classList.add('wm');
    card.insertAdjacentHTML('beforeend', `<h2 class="live-h">${esc(spec.headline)}</h2>${spec.body ? `<p class="live-b">${esc(spec.body)}</p>` : ''}${spec.cta ? `<span class="live-cta">${esc(spec.cta)}</span>` : ''}`);
    s.appendChild(card); fadeIn(card, 0, 430);
    if (spec.quote) fadeOut(card, T, 430);
    if (spec.price) {
      const p = el('div', 'live-price', `<small>${esc(spec.price.pre || 'From')}</small><b>${esc(spec.price.value)}</b><small>${esc(spec.price.post || '/month')}</small>`);
      p.style.top = '45%'; s.appendChild(p); fadeIn(p, 200); if (spec.quote) fadeOut(p, T);
    }
    if (spec.quote) {
      const q = el('div', 'live-quote', `<div class="qm">“</div><p>${esc(spec.quote)}</p>${spec.by ? `<div class="by">– ${esc(spec.by)}</div>` : ''}`);
      s.appendChild(q); fadeIn(q, T + 120, 430);
    }
    legal(s, spec, '57%');
    return s;
  },
  // End card: deep gradient, wordmark at 33 %, cyan CTA at 48 %, URL at 57 %, legal at 64 %.
  endcard(spec) {
    const s = scene('none'); s.style.padding = '0'; s.appendChild(el('div', 'live-grad'));
    const add = (node, top) => { const c = el('div', 'live-center'); c.style.top = top; c.appendChild(node); s.appendChild(c); fadeIn(c, 0); };
    add(logoSvg(165), '33%');
    if (spec.headline) { const h = el('h2', 'live-h', esc(spec.headline)); h.style.cssText = 'font-size:22px;padding:0 10%'; add(h, '43%'); }
    add(el('span', 'live-cta big', esc(spec.cta)), spec.headline ? '52%' : '48%');
    add(el('div', 'live-url', esc(spec.url || 'chequp.com')), spec.headline ? '60%' : '57%');
    legal(s, spec, spec.headline ? '67%' : '64%');
    return s;
  },
};

const BUILD = {
  hook(spec) {
    const s = scene(spec.ground || 'midnight'); s.classList.add(spec.align === 'top' ? 'top' : 'center');
    s.style.justifyContent = spec.align === 'top' ? 'flex-start' : 'center';
    let t = 80;
    if (spec.eyebrow) { const e = el('p', 'eyebrow', esc(spec.eyebrow)); s.appendChild(e); reveal(e, t); t += QUICK; }
    const h = el('h1', spec.size === 'h1' ? 'h1' : 'display'); s.appendChild(h); t = words(h, spec.headline, t);
    if (spec.body) { const b = el('p', 'body-lg', esc(spec.body)); s.appendChild(b); reveal(b, t); }
    return s;
  },

  media(spec) {
    const frame = spec.frame || 'full';
    if (frame === 'relief') {
      const s = scene(spec.ground || 'sand');
      const id = 'r' + Math.random().toString(36).slice(2, 7);
      const d = 'M210 0C325.98 0 420 31.34 420 70V272C420 298.51 398.51 320 372 320H48C21.49 320 0 298.51 0 272V70C0 31.34 94.02 0 210 0Z';
      const wrap = el('div', '', `<svg class="relief" viewBox="0 0 420 420"><defs><clipPath id="${id}"><path transform="scale(1,1.3125)" d="${d}"/></clipPath></defs>` +
        (spec.src && !/\.(mp4|mov|webm)$/i.test(spec.src) ? `<image href="${esc(spec.src)}" width="420" height="420" preserveAspectRatio="xMidYMid slice" clip-path="url(#${id})"/>` : `<path transform="scale(1,1.3125)" d="${d}" fill="var(--color-surface-secondary)"/>`) + '</svg>');
      s.appendChild(wrap); reveal(wrap, 0, SETTLE, 24);
      if (spec.tag) { const g = el('span', 'tag', esc(spec.tag)); g.style.marginTop = 'var(--space-lg)'; s.appendChild(g); reveal(g, SETTLE); }
      if (spec.headline) { const h = el('h2', 'h3'); h.style.marginTop = 'var(--space-md)'; s.appendChild(h); words(h, spec.headline, SETTLE + QUICK); }
      return s;
    }
    // Full bleed. Video b-roll is composited underneath by ffmpeg, so the page stays transparent.
    const s = scene('none'); s.style.padding = '0';
    if (spec.src && !/\.(mp4|mov|webm)$/i.test(spec.src)) {
      const m = el('div', 'media-full', `<img src="${esc(spec.src)}" alt="">`); s.appendChild(m);
      if (spec.focus) m.firstChild.style.objectPosition = spec.focus;
      if (spec.push !== false) m.firstChild.animate([{ transform: 'scale(1)' }, { transform: 'scale(1.06)' }], { duration: spec.dur * 1000, easing: 'linear', fill: 'both' });
    }
    if (spec.caption) { const c = el('div', 'media-caption', esc(spec.caption)); s.appendChild(c); reveal(c, 240); }
    if (spec.tag) { const g = el('span', 'tag', esc(spec.tag)); g.style.cssText = 'position:absolute;top:var(--safe-top);left:var(--safe-x)'; s.appendChild(g); reveal(g, 120); }
    return s;
  },

  steps(spec) {
    const s = scene(spec.ground || 'sand'); s.style.justifyContent = 'center';
    let t = 60;
    if (spec.eyebrow) { const e = el('p', 'eyebrow', esc(spec.eyebrow)); s.appendChild(e); reveal(e, t); }
    const h = el('h2', 'h1'); s.appendChild(h); t = words(h, spec.title, t + QUICK);
    const list = el('div', 'steps'); s.appendChild(list);
    const per = Math.max(SETTLE * 2, ((spec.dur * 1000) - t - 600) / spec.steps.length);
    spec.steps.forEach((st, i) => {
      const item = typeof st === 'string' ? { label: st } : st;
      const row = el('div', 'step', `<div class="dot">${i + 1}</div><div><div class="lbl">${esc(item.label)}</div>${item.sub ? `<div class="sub">${esc(item.sub)}</div>` : ''}</div>`);
      list.appendChild(row);
      const at = t + i * per; reveal(row, at);
      // upcoming → current → complete, as in the eligibility flow.
      tickers.push(ms => { row.classList.toggle('current', ms >= at && ms < at + per); row.classList.toggle('complete', ms >= at + per); const dot = row.firstChild; dot.innerHTML = ms >= at + per ? '<svg width="18" height="18" viewBox="0 0 16 16" fill="none"><path d="M3.5 8.5l3 3 6-6.5" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"/></svg>' : String(i + 1); });
    });
    return s;
  },

  checklist(spec) {
    const s = scene(spec.ground || 'sand'); s.style.justifyContent = 'center';
    let t = 60;
    if (spec.eyebrow) { const e = el('p', 'eyebrow', esc(spec.eyebrow)); s.appendChild(e); reveal(e, t); }
    const h = el('h2', 'h1'); s.appendChild(h); t = words(h, spec.title, t + QUICK);
    const list = el('div', 'checks'); s.appendChild(list);
    spec.items.forEach((it, i) => {
      const item = typeof it === 'string' ? { text: it } : it;
      const row = el('div', 'check', icon(item.icon || 'CircleCheck') + `<span>${esc(item.text)}</span>`);
      list.appendChild(row); reveal(row, t + i * (SETTLE + 120));
    });
    return s;
  },

  stat(spec) {
    if (!spec.source) throw new Error('stat scene without a source: "A proof module without a source is a claim."');
    const s = scene(spec.ground || 'sand'); s.style.justifyContent = 'center'; s.classList.add('stat');
    if (spec.eyebrow) { const e = el('p', 'eyebrow', esc(spec.eyebrow)); s.appendChild(e); reveal(e, 60); }
    const n = el('div', '', `<span class="num"></span><span class="unit">${esc(spec.unit || '')}</span>`); s.appendChild(n); reveal(n, 120);
    const num = n.firstChild, target = parseFloat(String(spec.value).replace(/,/g, '')), dec = (String(spec.value).split('.')[1] || '').length;
    const fmt = v => v.toLocaleString('en-GB', { minimumFractionDigits: dec, maximumFractionDigits: dec });
    const run = Math.min(1200, spec.dur * 1000 * 0.4);
    tickers.push(ms => { const p = Math.max(0, Math.min(1, (ms - 120) / run)); const e = 1 - Math.pow(1 - p, 3); num.textContent = (spec.prefix || '') + fmt(target * e); });
    const l = el('div', 'label', esc(spec.label)); s.appendChild(l); reveal(l, 120 + QUICK);
    const src = el('div', 'src mono', esc('Source: ' + spec.source)); s.appendChild(src); reveal(src, 120 + SETTLE);
    return s;
  },

  quote(spec) {
    const s = scene(spec.ground || 'yellow-wash'); s.style.justifyContent = 'center';
    const st = el('div', 'stars', STAR.repeat(spec.stars || 5)); s.appendChild(st); reveal(st, 60);
    if (spec.title) { const h = el('h2', 'h3', esc(spec.title)); h.style.marginBottom = 'var(--space-sm)'; s.appendChild(h); reveal(h, 60 + QUICK); }
    const q = el('p', 'quote'); s.appendChild(q); const t = words(q, '“' + spec.quote + '”', 60 + SETTLE);
    if (spec.name) { const w = el('div', 'who', esc(spec.name) + (spec.meta ? ` <span>· ${esc(spec.meta)}</span>` : '')); s.appendChild(w); reveal(w, t); }
    if (spec.source) { const src = el('div', 'mono', esc(spec.source)); src.style.marginTop = 'var(--space-sm)'; s.appendChild(src); reveal(src, t + QUICK); }
    return s;
  },

  ui(spec) {
    // The eligibility check, animated: progress fills, an answer is selected.
    const s = scene(spec.ground || 'lavender'); s.style.justifyContent = 'center';
    if (spec.title) { const h = el('h2', 'h3'); h.style.marginBottom = 'var(--space-lg)'; s.appendChild(h); words(h, spec.title, 60); }
    const ph = el('div', 'phone'); s.appendChild(ph); reveal(ph, 200, SETTLE, 24);
    const bar = el('div', 'pbar', '<i></i>'); ph.appendChild(bar);
    const from = spec.progress_from ?? 0.25, to = spec.progress_to ?? 0.5;
    bar.firstChild.animate([{ width: from * 100 + '%' }, { width: to * 100 + '%' }], { duration: SETTLE, delay: spec.dur * 1000 * 0.6, easing: EASE, fill: 'both' });
    ph.appendChild(el('div', 'q', esc(spec.question)));
    const pick = spec.select ?? 0, at = spec.dur * 1000 * 0.45;
    (spec.options || []).forEach((o, i) => {
      const r = el('div', 'radio', `<span class="o"></span><span>${esc(o)}</span>`); ph.appendChild(r); reveal(r, 320 + i * 90);
      if (i === pick) tickers.push(ms => r.classList.toggle('sel', ms >= at));
    });
    if (spec.cta) { const b = el('div', 'btn full', esc(spec.cta)); b.style.marginTop = 'var(--space-sm)'; ph.appendChild(b); reveal(b, 320 + (spec.options || []).length * 90); }
    return s;
  },

  chat(spec) {
    // Coach conversation, message by message. Always labelled illustrative (no fabricated members).
    const s = scene(spec.ground || 'sand'); s.style.justifyContent = 'center';
    if (spec.title) { const h = el('h2', 'h3'); h.style.marginBottom = 'var(--space-lg)'; s.appendChild(h); words(h, spec.title, 60); }
    const box = el('div', 'chat'); s.appendChild(box);
    const msgs = spec.messages || [], start = spec.title ? 700 : 200;
    const per = Math.max(600, (spec.dur * 1000 - start - 800) / Math.max(1, msgs.length));
    msgs.forEach((m, i) => {
      const at = start + i * per;
      if (m.from !== 'you') {
        const typing = el('div', 'bubble them typing', '<i></i><i></i><i></i>'); box.appendChild(typing);
        tickers.push(ms => { typing.style.display = ms >= at - 520 && ms < at ? 'flex' : 'none'; });
      }
      const b = el('div', 'bubble ' + (m.from === 'you' ? 'you' : 'them'), (m.from !== 'you' && m.name ? `<b>${esc(m.name)}</b>` : '') + esc(m.text));
      box.appendChild(b); reveal(b, at, SETTLE, 10);
      tickers.push(ms => { b.style.display = ms >= at ? '' : 'none'; });
    });
    const note = el('p', 'legal', esc(spec.note || 'Illustrative conversation')); note.style.marginTop = 'var(--space-md)'; s.appendChild(note);
    return s;
  },

  endcard(spec) {
    const s = scene(spec.ground || 'midnight'); s.style.justifyContent = 'center';
    const svg = LOGO.replace('__PATHS__', window.__LOGO_PATHS || '');
    const lg = el('div', '', svg); lg.firstChild.style.width = '132px'; s.appendChild(lg); reveal(lg, 40);
    const h = el('h2', 'h1'); h.style.marginTop = 'var(--space-xl)'; s.appendChild(h); let t = words(h, spec.headline, 40 + QUICK);
    if (spec.body) { const b = el('p', 'body-lg', esc(spec.body)); s.appendChild(b); reveal(b, t); t += QUICK; }
    if (spec.rating) { const r = el('div', 'stars', STAR.repeat(5) + `<span class="body" style="margin-left:8px;color:var(--ink-muted)">${esc(spec.rating)}</span>`); r.style.cssText += ';align-items:center;margin-top:var(--space-lg)'; s.appendChild(r); reveal(r, t); }
    const b = el('div', 'btn', esc(spec.cta)); b.style.marginTop = 'var(--space-xl)'; s.appendChild(b); reveal(b, t + QUICK);
    if (spec.legal) { const l = el('p', 'legal', esc(spec.legal)); l.style.marginTop = 'var(--space-xl)'; s.appendChild(l); reveal(l, t + SETTLE); }
    return s;
  },
};

function buildCaptions(spec) {
  const words = spec.captions || [];
  // Captions are for sound-off viewing. Graphic scenes already show their words, so by default
  // only media scenes without their own caption get them; `show_captions` overrides per scene.
  const on = spec.show_captions ?? (spec.type === 'media' && !spec.caption);
  if (!words.length || !on) return;
  const wrap = el('div', 'captions', '<div class="box"></div>'); document.getElementById('stage').appendChild(wrap);
  const box = wrap.firstChild, off = (spec.t0 || 0) * 1000;
  // Group words into short lines (max ~4 words or a pause) so captions read in one glance.
  const groups = []; let g = [];
  words.forEach((w, i) => {
    g.push(w);
    const gap = words[i + 1] ? words[i + 1].s - w.e : 9;
    if (g.length >= (spec.caption_words || 4) || gap > 0.35 || /[.,?;:]$/.test(w.w)) { groups.push(g); g = []; }
  });
  if (g.length) groups.push(g);
  tickers.push(ms => {
    const t = (ms + off) / 1000;
    const cur = groups.find(gr => t >= gr[0].s - 0.05 && t <= gr[gr.length - 1].e + 0.25);
    box.innerHTML = cur ? cur.map(w => `<span class="cw${t >= w.s && t <= w.e + 0.05 ? ' on' : ''}">${esc(w.w)}</span>`).join(' ') : '';
  });
}

window.__load = async (spec) => {
  document.documentElement.dataset.format = spec.format || '9x16';
  if (spec.mode && spec.mode !== 'weight-health') document.documentElement.dataset.mode = spec.mode;
  window.__LOGO_PATHS = spec.logo_paths || '';
  if (spec.theme) document.documentElement.dataset.theme = spec.theme;
  const build = (spec.theme === 'meta-live' && LIVE[spec.type]) || BUILD[spec.type];
  if (!build) throw new Error('unknown scene type: ' + spec.type);
  const s = build(spec);
  exitDivider(s, spec);
  if (spec.caption_pos === 'top') document.getElementById('stage').classList.add('has-caption-top');
  buildCaptions(spec);
  await document.fonts.ready;
  await Promise.all([...document.images].map(i => i.complete ? 0 : new Promise(r => { i.onload = i.onerror = r; })));
  document.getAnimations().forEach(a => a.pause());
  window.__seek(0);
  return true;
};

window.__seek = (ms) => {
  document.getAnimations().forEach(a => { a.currentTime = ms; });
  tickers.forEach(f => f(ms));
};
