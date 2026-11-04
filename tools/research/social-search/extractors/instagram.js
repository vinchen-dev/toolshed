() => {
  const out = []; const seen = new Set();
  for (const a of document.querySelectorAll('a[href*="/p/"], a[href*="/reel/"]')) {
    const href = (a.getAttribute('href') || '').split('?')[0];
    if (!href || seen.has(href)) continue; seen.add(href);
    const img = a.querySelector('img');
    out.push({url: href, text: img ? (img.getAttribute('alt') || '') : ''});
  }
  return out;
}
