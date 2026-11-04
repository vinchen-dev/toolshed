() => {
  const out = []; const seen = new Set();
  const count = (art, id) => { const el = art.querySelector('[data-testid="' + id + '"]');
    const m = el ? (el.getAttribute('aria-label') || el.innerText).match(/[\d,.]+\s?[KM]?/i) : null; return m ? m[0].trim() : null; };
  for (const art of document.querySelectorAll('article[data-testid="tweet"]')) {
    const a = [...art.querySelectorAll('a[href*="/status/"]')].find(x => x.querySelector('time'));
    const href = a ? a.getAttribute('href') : null; if (!href || seen.has(href)) continue; seen.add(href);
    const m = href.match(/^\/([^/]+)\/status\//); const t = a.querySelector('time');
    const body = art.querySelector('[data-testid="tweetText"]');
    out.push({url: href, author: m ? m[1] : null, text: body ? body.innerText.trim() : '', time: t ? t.getAttribute('datetime') : null,
      likes: count(art, 'like'), comments: count(art, 'reply'), shares: count(art, 'retweet')});
  }
  return out;
}
