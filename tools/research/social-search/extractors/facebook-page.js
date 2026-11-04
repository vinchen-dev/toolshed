() => {
  const out = []; const seen = new Set();
  const h1 = document.querySelector('h1'); const name = h1 ? h1.innerText.trim() : null;
  for (const art of document.querySelectorAll('[role="article"]')) {
    const link = [...art.querySelectorAll('a[href]')].map(a => a.getAttribute('href') || '')
      .filter(h => !/comment_id=|reply_comment_id=/.test(h))
      .find(h => /\/(posts|videos|reel|photos|photo)\//.test(h) || /story_fbid=|fbid=/.test(h));
    if (!link) continue;
    const key = link.split('?')[0]; if (seen.has(key)) continue; seen.add(key);
    const whenRe = new RegExp('\\b(\\d+\\s?[smhdwy]|\\d+ (minutes?|hours?|days?)( ago)?|Yesterday|'
      + '[A-Z][a-z]+ \\d{1,2}(, \\d{4})?)\\b');
    const whenMatch = art.innerText.match(whenRe);
    const body = art.querySelector('[data-ad-preview="message"]') || art.querySelector('[dir="auto"]') || art;
    const text = body.innerText.trim().slice(0, 1000);
    out.push({url: link, author: name, text, when: whenMatch ? whenMatch[1] : null});
  }
  return out;
}
