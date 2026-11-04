() => {
  const out = []; const seen = new Set();
  for (const a of document.querySelectorAll('a[href*="/video/"]')) {
    const href = (a.getAttribute('href') || '').split('?')[0];
    if (!href || seen.has(href)) continue; seen.add(href);
    const m = href.match(/\/@([^/]+)\/video\//);
    const card = a.closest('[data-e2e], [class*="DivItemContainer"], div') || a;
    const desc = card.querySelector('[data-e2e="search-card-desc"], [data-e2e="search-card-video-caption"]');
    const views = card.querySelector('[data-e2e="video-views"], strong');
    out.push({url: href, author: m ? m[1] : null, text: desc ? desc.innerText.trim() : card.innerText.trim().slice(0, 300),
      views: views ? views.innerText.trim() : null});
  }
  return out;
}
