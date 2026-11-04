() => {
  const seen = new Set(); const out = [];
  const count = (box, word) => {
    const svg = [...box.querySelectorAll('svg[aria-label]')]
      .find(e => (e.getAttribute('aria-label') || '').toLowerCase().startsWith(word));
    if (!svg) return null;
    const btn = svg.closest('[role="button"]') || svg.parentElement;
    const m = btn ? (btn.innerText || '').match(/[\d,.]+\s?[KM]?/i) : null; return m ? m[0].trim() : null;
  };
  for (const a of document.querySelectorAll('a[href*="/post/"]')) {
    const href = (a.getAttribute('href') || '').replace(/\/media$/, '');
    if (!href || seen.has(href)) continue;
    const box = a.closest('[data-pressable-container]') || a.parentElement; if (!box) continue;
    seen.add(href);
    const m = href.match(/^\/@([^/]+)\/post\//); const author = m ? m[1] : null;
    const timeEl = box.querySelector('time');
    const skip = new Set([author, timeEl ? timeEl.innerText.trim() : '']);
    const text = [...box.querySelectorAll('span')].map(s => s.innerText.trim())
      .filter(t => t && !skip.has(t) && !/^\d+[smhdw]$/.test(t) && !/^\d{1,2}\/\d{1,2}\/\d{2}$/.test(t))
      .sort((x, y) => y.length - x.length)[0] || '';
    out.push({url: href, author, text, time: timeEl ? timeEl.getAttribute('datetime') : null,
      likes: count(box, 'like'), comments: count(box, 'repl'), reposts: count(box, 'repost')});
  }
  return out;
}
