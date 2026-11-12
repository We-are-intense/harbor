// 离线缓存 Service Worker。由 hooks/offline.py 在构建时生成 site/sw.js，勿直接部署本模板。
// 目的：地铁等弱网、断网时仍能打开文章。
//   - 页面（HTML）：先走网络拿最新内容，3 秒没响应或断网就用缓存；
//   - 其它文件：预缓存的直接用缓存；其余（按需字体数据等）先用缓存，同时后台更新。
// 安装时预先缓存全部页面、图片和必需的样式脚本，没打开过的文章离线也能看。
// 每个文件带内容哈希（rev）：重新部署后只下载变了的文件，没变的从旧缓存直接拷过来。

const VERSION = "__VERSION__";
const PRECACHE = __PRECACHE__; // [[相对 URL, 内容哈希], ...]
const SHELL = `shell-${VERSION}`; // 随每次部署更换，旧版本在 activate 时删除
const RUNTIME = "runtime";        // 预缓存清单以外按需缓存的文件
const NETWORK_TIMEOUT = 3000;

// 预缓存的缓存键带 ?__rev=哈希，内容变了键就变；这里记下 文件 URL -> 缓存键
const KEYS = new Map(PRECACHE.map(([url, rev]) => {
  const abs = new URL(url, location.href);
  return [abs.href, `${abs.href}?__rev=${rev}`];
}));

self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(SHELL);
    const old = (await caches.keys()).filter((k) => k.startsWith("shell-") && k !== SHELL);
    // 逐个处理：某个文件失败不影响其它文件（addAll 只要一个失败就全部作废）
    await Promise.all([...KEYS].map(async ([url, key]) => {
      if (await cache.match(key)) return;
      for (const name of old) {
        const hit = await (await caches.open(name)).match(key);
        if (hit) return cache.put(key, hit);
      }
      try {
        // cache: "reload" 绕过浏览器 HTTP 缓存，确保拿到这次部署的版本
        const res = await fetch(new Request(url, { cache: "reload" }));
        if (res.ok) await cache.put(key, res);
      } catch (e) { /* 失败的文件运行时再缓存 */ }
    }));
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== SHELL && k !== RUNTIME).map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET" || new URL(req.url).origin !== location.origin) return;
  if (req.mode === "navigate") {
    event.respondWith(networkFirst(req));
  } else {
    event.respondWith(staleWhileRevalidate(req, event));
  }
});

async function networkFirst(req) {
  const network = fetch(req).then(async (res) => {
    if (res.ok) await store(req, res.clone());
    return res;
  });
  const timeout = new Promise((resolve) => setTimeout(resolve, NETWORK_TIMEOUT));
  try {
    const res = await Promise.race([network, timeout]);
    if (res) return res;
  } catch (e) { /* 断网，落到缓存 */ }
  const cached = await lookup(req);
  if (cached) return cached;
  // 缓存里也没有：网络慢但还在加载，就继续等网络
  return network.catch(() => offlinePage());
}

async function staleWhileRevalidate(req, event) {
  // 预缓存的文件内容由哈希锁定，有新版本时会随新的 sw.js 一起更新，不必每次后台重新下载（省流量）
  const key = KEYS.get(bare(req));
  if (key) {
    const hit = await (await caches.open(SHELL)).match(key);
    if (hit) return hit;
  }
  const cached = await lookup(req);
  const network = fetch(req).then(async (res) => {
    if (res.ok) await store(req, res.clone());
    return res;
  });
  if (cached) {
    event.waitUntil(network.catch(() => {}));
    return cached;
  }
  return network;
}

function bare(req) {
  const u = new URL(req.url);
  u.search = ""; u.hash = "";
  return u.href;
}

async function lookup(req) {
  const key = KEYS.get(bare(req));
  if (key) {
    const hit = await (await caches.open(SHELL)).match(key);
    if (hit) return hit;
  }
  return (await caches.open(RUNTIME)).match(req, { ignoreSearch: true });
}

// 预缓存清单里的文件写回 SHELL（同一个键），其它写到 RUNTIME
async function store(req, res) {
  const key = KEYS.get(bare(req));
  if (key) return (await caches.open(SHELL)).put(key, res);
  return (await caches.open(RUNTIME)).put(req, res);
}

function offlinePage() {
  return new Response(
    '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' +
    '<title>离线</title><p style="font:16px/1.6 -apple-system,sans-serif;padding:2em">当前没有网络，这一页还没有缓存。联网后打开一次，之后离线也能看。</p>',
    { status: 503, headers: { "Content-Type": "text/html; charset=utf-8" } }
  );
}
