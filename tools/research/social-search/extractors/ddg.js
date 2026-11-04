() => [...document.querySelectorAll('.result')].map(r => {
  const a = r.querySelector('a.result__a'); const s = r.querySelector('.result__snippet');
  if (!a) return null;
  return {title: a.innerText.trim(), href: a.getAttribute('href'), snippet: s ? s.innerText.trim() : ''};
}).filter(Boolean)
