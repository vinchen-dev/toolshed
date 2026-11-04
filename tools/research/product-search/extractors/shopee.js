() => {
  const out = []; const seen = new Set();
  for (const a of document.querySelectorAll('a[href*="-i."]')) {
    const href = a.getAttribute('href') || '';
    const key = href.split('?')[0];
    if (!href || seen.has(key)) continue;
    const text = a.innerText || '';
    const price = (text.match(/₱\s?[\d,]+(\.\d+)?/) || [null])[0];
    if (!price) continue;
    seen.add(key);
    const img = a.querySelector('img');
    const lines = text.split('\n').map(s => s.trim()).filter(Boolean);
    const sold = (text.match(/([\d.,]+[KM]?)\s*sold/i) || [null, null])[1];
    const rating = (text.match(/(\d\.\d)\b/) || [null, null])[1];
    out.push({title: lines[0] || (img ? img.getAttribute('alt') : ''), price, url: href, shop: null, rating, sold,
      location: lines[lines.length - 1] || null, image: img ? (img.getAttribute('src') || null) : null});
  }
  return out;
}
