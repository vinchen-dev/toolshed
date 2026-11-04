() => [...document.querySelectorAll('shreddit-post')].map(p => ({
  permalink: p.getAttribute('permalink'), title: p.getAttribute('post-title'), author: p.getAttribute('author'),
  subreddit: p.getAttribute('subreddit-prefixed-name'), score: p.getAttribute('score'),
  comments: p.getAttribute('comment-count'), created: p.getAttribute('created-timestamp')}))
