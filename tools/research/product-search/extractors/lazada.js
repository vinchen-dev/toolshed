() => {
  const out = []; const seen = new Set();
  for (const a of document.querySelectorAll('a[href*="/products/"]')) {
    const href = a.getAttribute('href') || '';
    if (!href || seen.has(href.split('?')[0])) continue;
    let card = a; for (let i = 0; i < 6 && card.parentElement; i++) {
      card = card.parentElement; if (/₱\s?[\d,]+/.test(card.innerText || '')) break;
    }
    const text = card.innerText || '';
    const price = (text.match(/₱\s?[\d,]+(\.\d+)?/) || [null])[0];
    if (!price) continue;
    seen.add(href.split('?')[0]);
    const img = card.querySelector('img');
    const title = a.getAttribute('title') || (img && img.getAttribute('alt')) || a.innerText.trim();
    const sold = (text.match(/([\d.,]+[KM]?)\s*sold/i) || [null, null])[1];
    const rating = (text.match(/(\d\.\d)\s*(\(|\/|stars|$)/m) || [null, null])[1];
    const lines = text.split('\n').map(s => s.trim()).filter(Boolean);
    const location = lines.find(l => /^(Metro Manila|Overseas|[A-Z][a-zA-Z ]+(City|Province)?)$/.test(l) && l.length < 30 && !/sold|₱/.test(l)) || null;
    out.push({title, price, url: href, shop: null, rating, sold, location, image: img ? (img.getAttribute('src') || null) : null});
  }
  return out;
}
