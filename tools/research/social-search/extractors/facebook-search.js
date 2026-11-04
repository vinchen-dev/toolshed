() => {
  const out = []; const seen = new Set();
  for (const art of document.querySelectorAll('[role="article"]')) {
    const link = [...art.querySelectorAll('a[href]')].map(a => a.getAttribute('href') || '')
      .find(h => /\/(posts|videos|reel|photos|photo|permalink)\//.test(h) || /story_fbid=|fbid=/.test(h));
    if (!link || seen.has(link)) continue; seen.add(link);
    const who = [...art.querySelectorAll('h2 a, h3 a, h4 a, strong a')].map(a => a.innerText.trim()).find(Boolean);
    const whenRe = new RegExp('\\b(\\d+\\s?[smhdwy]|\\d+ (minutes?|hours?|days?)( ago)?|Yesterday|'
      + '[A-Z][a-z]+ \\d{1,2}(, \\d{4})?)\\b');
    const whenMatch = art.innerText.match(whenRe);
    const body = art.querySelector('[data-ad-preview="message"]') || art.querySelector('[dir="auto"]') || art;
    const count = (word) => {
      const el = [...art.querySelectorAll('[aria-label]')]
        .find(e => (e.getAttribute('aria-label') || '').toLowerCase().includes(word));
      const m = el ? el.getAttribute('aria-label').match(/[\d,.]+\s?[KM]?/i) : null; return m ? m[0].trim() : null;
    };
    out.push({url: link, author: who || null, text: body.innerText.trim().slice(0, 1000),
      when: whenMatch ? whenMatch[1] : null, likes: count('like'), comments: count('comment'), shares: count('share')});
  }
  return out;
}
