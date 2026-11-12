"""构建后生成离线缓存用的 site/sw.js（模板见 hooks/sw.template.js）。

- build / gh-deploy：列出要预缓存的文件及各自的内容哈希写进 sw.js。
  手机上只重新下载哈希变了的文件；全部不变则 sw.js 不变，什么都不下载。
- serve：写一个自我注销的 sw.js，避免本地预览时被缓存挡住、看不到刚改的内容。
"""

import fnmatch
import hashlib
import json
import os
from urllib.parse import quote

_command = "build"

# 安装时就缓存的文件（相对站点根目录）。按需加载的公式字体数据、PDF 不在这里，用到时再缓存。
PRECACHE_PATTERNS = [
    "*.html",
    "Category/*.svg",
    "Category/*.png",
    "Category/*.jpg",
    "Category/*.jpeg",
    "Category/*.gif",
    "assets/stylesheets/*.css",
    "assets/javascripts/bundle.*.js",
    "assets/javascripts/workers/*.js",
    "assets/images/*",
    "search/search_index.json",
    "stylesheets/*",
    "javascripts/*",
    "vendor/mathjax/tex-mml-chtml.js",
    # MathJax 每页都会加载的读屏语音模块（约 1MB），放进预缓存，免得每次打开页面都后台重新下载
    "vendor/mathjax/sre/speech-worker.js",
    "vendor/mathjax/sre/mathmaps/base.json",
    "vendor/mathjax/sre/mathmaps/en.json",
    "vendor/mathjax-newcm-font/chtml/woff2/*",
]
EXCLUDE = ["google*.html"]

# 浏览器给 URL 路径做百分号编码时保留的字符（WHATWG path percent-encode set 之外的）
_URL_SAFE = "/:@!$&'()*+,;=-._~"

KILL_SWITCH = """// 本地预览（mkdocs serve）不做离线缓存：注销自己并清空缓存
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(
  caches.keys().then((ks) => Promise.all(ks.map((k) => caches.delete(k))))
    .then(() => self.registration.unregister())
));
"""


def on_startup(command, dirty):
    global _command
    _command = command


def _url(rel):
    # Category/x/index.html -> Category/x/（和站点里的链接、地址栏一致）
    if rel == "index.html":
        rel = ""
    elif rel.endswith("/index.html"):
        rel = rel[: -len("index.html")]
    return "./" + quote(rel, safe=_URL_SAFE)


def on_post_build(config):
    site = config["site_dir"]
    out = os.path.join(site, "sw.js")
    if _command == "serve":
        with open(out, "w", encoding="utf-8") as f:
            f.write(KILL_SWITCH)
        return

    files = []
    for root, _, names in os.walk(site):
        for name in names:
            rel = os.path.relpath(os.path.join(root, name), site).replace(os.sep, "/")
            if any(fnmatch.fnmatch(rel, p) for p in EXCLUDE):
                continue
            # fnmatch 的 * 会跨目录：*.html 覆盖所有层级的页面
            if any(fnmatch.fnmatch(rel, p) for p in PRECACHE_PATTERNS):
                files.append(rel)
    files.sort()

    entries, total = [], hashlib.sha256()
    for rel in files:
        with open(os.path.join(site, rel), "rb") as f:
            rev = hashlib.sha256(f.read()).hexdigest()[:10]
        entries.append([_url(rel), rev])
        total.update(f"{rel}:{rev}\n".encode())

    template = os.path.join(os.path.dirname(__file__), "sw.template.js")
    with open(template, encoding="utf-8") as f:
        js = f.read()
    js = js.replace("__VERSION__", total.hexdigest()[:12])
    js = js.replace("__PRECACHE__", json.dumps(entries, ensure_ascii=False, separators=(",", ":")))
    with open(out, "w", encoding="utf-8") as f:
        f.write(js)
