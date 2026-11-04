() => {
  const text = document.body.innerText;
  const re = /(\d+)\s+(#\S+)\s+([^\n]+?)\s+([\d.,]+[KMB]?)\s+Posts\s+([\d.,]+[KMB]?)\s+Views/g;
  const out = []; let m;
  while ((m = re.exec(text)) !== null) {
    out.push({rank: Number(m[1]), hashtag: m[2], category: m[3].trim(), posts: m[4], views: m[5]});
  }
  return out;
}
