#!/usr/bin/env python3
"""把「信号与系统」LaTeX 听课笔记同步成博客的 Markdown 页面。

用法（在仓库根目录执行）：
    python3 tools/signal_notes/sync.py                 同步全部笔记
    python3 tools/signal_notes/sync.py 03 04           只同步文件名含 03 或 04 的
    python3 tools/signal_notes/sync.py --src <目录>    指定笔记源目录

每篇 .tex 的处理：
  1. 图：每个 figure 环境（整体，含多面板排版）和不在 figure 里的 tikzpicture 各占一页，
     用原文件的导言区编译一次，栅格化求包围盒后导出为裁好边的 SVG；
  2. 正文：先把自定义宏（例题、小标题、编号引用等）换成 pandoc 认识的写法，
     再用 pandoc + notes.lua 转成 Python-Markdown 能渲染的 Markdown；
  3. 同时拷贝编译好的 PDF，页面顶部给出下载链接。

依赖：pandoc、xelatex（TeX Live，宏包与源目录 build_pdf.py 要求的一致）、poppler（pdftoppm / pdftocairo）。
只用 Python 标准库。
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
DEFAULT_SRC = os.path.expanduser(
    "~/Documents/github/ImageProcessing-Playground/Documents/06-Signal/2022浙江大学信号与系统-胡浩基老师")
OUT_DIR = os.path.join(REPO, "docs", "Category", "SignalsAndSystems", "ZJU2022")
LUA = os.path.join(HERE, "notes.lua")
TEX_DIRS = ["/Library/TeX/texbin", "/opt/homebrew/bin", "/usr/local/bin"]
CIRCLED = "⓪①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


# ---------------------------------------------------------------- 文本解析小工具
def read_group(s, i, open_="{", close="}"):
    """s[i] 是 open_，返回 (组内文本, 组后下标)。跳过 \\{ \\} 转义。"""
    assert s[i] == open_, (s[i:i + 40], i)
    depth, j = 0, i
    while j < len(s):
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == open_:
            depth += 1
        elif c == close:
            depth -= 1
            if depth == 0:
                return s[i + 1:j], j + 1
        j += 1
    raise ValueError("括号不配对：" + s[i:i + 60])


def skip_ws(s, i):
    while i < len(s) and s[i] in " \t\n":
        i += 1
    return i


def read_args(s, i, n, optional=False):
    """从 s[i] 起读 n 个 {} 参数（optional=True 时先读一个可选的 [] 参数）。返回 (参数列表, 结束下标)。"""
    args = []
    if optional:
        k = skip_ws(s, i)
        if k < len(s) and s[k] == "[":
            a, i = read_group(s, k, "[", "]")
            args.append(a)
        else:
            args.append(None)
    for _ in range(n - (1 if optional else 0)):
        i = skip_ws(s, i)
        a, i = read_group(s, i)
        args.append(a)
    return args, i


def strip_comments(s):
    # TeX 的 % 吃掉本行余下部分、换行和下一行行首空白
    return re.sub(r"(?<!\\)%[^\n]*\n[ \t]*", "", s)


def replace_macro(s, name, nargs, fn):
    """把 \\name{a}{b}... 替换成 fn(args)。"""
    out, i = [], 0
    pat = re.compile(r"\\" + name + r"(?![a-zA-Z])")
    while True:
        m = pat.search(s, i)
        if not m:
            out.append(s[i:])
            return "".join(out)
        out.append(s[i:m.start()])
        args, i = read_args(s, m.end(), nargs) if nargs else ([], m.end())
        out.append(fn(args))


def find_env(s, env, start=0):
    """返回 [(起, 止)]，环境不嵌套。"""
    spans = []
    b, e = "\\begin{%s}" % env, "\\end{%s}" % env
    i = start
    while True:
        a = s.find(b, i)
        if a < 0:
            return spans
        z = s.find(e, a)
        spans.append((a, z + len(e)))
        i = z + len(e)


# ---------------------------------------------------------------- 第 1 步：拆分与宏定义
NEWCMD = re.compile(r"\\(re)?newcommand\s*\{?\\([a-zA-Z]+)\}?\s*(?:\[(\d)\])?\s*(\[[^\]]*\])?\s*")


def extract_defs(body):
    """从正文里摘出 \\newcommand / \\renewcommand 定义。返回 (去掉定义的正文, [定义原文], {宏名: (参数个数, 有可选参数)})。"""
    defs, macros, out, i = [], {}, [], 0
    while True:
        m = NEWCMD.search(body, i)
        if not m:
            out.append(body[i:])
            break
        out.append(body[i:m.start()])
        k = skip_ws(body, m.end())
        if body[k] != "{":  # \renewcommand\arraystretch{1.3} 一类
            _, end = read_group(body, k)
        else:
            _, end = read_group(body, k)
        defs.append(body[m.start():end])
        i = end
    return "".join(out), defs


def tikz_macros(text):
    """定义体里含 tikzpicture 的宏：{宏名: (参数个数, 有无可选参数)}。"""
    res = {}
    for m in NEWCMD.finditer(text):
        k = skip_ws(text, m.end())
        if k >= len(text) or text[k] != "{":
            continue
        defn, _ = read_group(text, k)
        if "tikzpicture" in defn:
            res[m.group(2)] = (int(m.group(3) or 0), bool(m.group(4)))
    return res


# ---------------------------------------------------------------- 第 2 步：编号与引用
def number_and_label(body):
    """给章节、例题、图编号，改写 \\section 标题（前面加编号）与 \\example，解析 \\label。
    返回 (改写后的正文, {label: 编号文本}, [每个 figure 的编号])。"""
    sec = sub = subsub = ex = fig = 0
    appendix = False
    labels, fig_numbers = {}, []
    current = None
    out, i = [], 0
    pat = re.compile(r"\\(section|subsection|subsubsection)(\*?)(?=\{)|\\example(?=\{)|\\caption(?=\{)"
                     r"|\\label(?=\{)|\\appendix\b|\\begin\{figure\}")

    def secstr():
        return chr(ord("A") + sec - 1) if appendix else str(sec)

    while True:
        m = pat.search(body, i)
        if not m:
            out.append(body[i:])
            break
        out.append(body[i:m.start()])
        tok = m.group(0)
        i = m.end()
        if m.group(1):
            level, star = m.group(1), m.group(2)
            title, i = read_group(body, i)
            if not star:
                if level == "section":
                    sec, sub, subsub, ex = sec + 1, 0, 0, 0
                    current = secstr()
                elif level == "subsection":
                    sub, subsub = sub + 1, 0
                    current = "%s.%d" % (secstr(), sub)
                else:
                    subsub += 1
                    current = "%s.%d.%d" % (secstr(), sub, subsub)
                title = current + " " + title
            out.append("\\%s{%s}" % (level, title))
        elif tok == "\\example":
            (t, c, note), i = read_args(body, i, 3)
            ex += 1
            current = str(ex)
            out.append("\n\n\\begin{blogex}\\begin{blogexn}%d\\end{blogexn}"
                       "\\begin{blogext}%s\\end{blogext}\\begin{blogexc}%s\\end{blogexc}"
                       "\\begin{blogexo}%s\\end{blogexo}\\end{blogex}\n\n" % (ex, t, c, note))
        elif tok == "\\caption":
            fig += 1
            current = str(fig)
            fig_numbers[-1] = fig
            out.append(tok)
        elif tok == "\\label":
            name, i = read_group(body, i)
            labels[name] = current
        elif tok == "\\appendix":
            appendix, sec = True, 0
        else:  # \begin{figure}
            fig_numbers.append(None)
            out.append(tok)
    return "".join(out), labels, fig_numbers


def resolve_refs(s, labels):
    def ref(args):
        if args[0] not in labels:
            raise KeyError("未定义的引用：" + args[0])
        return labels[args[0]]
    return replace_macro(s, "ref", 1, ref)


# ---------------------------------------------------------------- 第 3 步：拆出图
def extract_units(body, tmacros, fig_numbers):
    """把图换成占位段落 BLOGFIGn。返回 (正文, [(图的 LaTeX, 图号或 None, 图注 LaTeX 或 None)])。"""
    units = []
    spans = []  # (起, 止, 种类)
    for k, (a, z) in enumerate(find_env(body, "figure")):
        spans.append((a, z, ("figure", k)))
    figs = [(a, z) for a, z, _ in spans]

    def inside_fig(p):
        return any(a <= p < z for a, z in figs)

    for a, z in find_env(body, "tikzpicture"):
        if not inside_fig(a):
            spans.append((a, z, ("tikz", None)))
    for name, (n, opt) in tmacros.items():
        for m in re.finditer(r"\\" + name + r"(?![a-zA-Z])", body):
            if inside_fig(m.start()):
                continue
            _, end = read_args(body, m.end(), n, opt)
            spans.append((m.start(), end, ("tikz", None)))
    spans.sort()
    out, i = [], 0
    for a, z, (kind, k) in spans:
        if a < i:
            raise ValueError("图的范围重叠：" + body[a:a + 80])
        out.append(body[i:a])
        text = body[a:z]
        cap = num = None
        if kind == "figure":
            inner = re.sub(r"^\\begin\{figure\}(\[[^\]]*\])?|\\end\{figure\}$", "", text)
            m = re.search(r"\\caption(?=\{)", inner)
            if m:
                cap, e = read_group(inner, m.end())
                inner = inner[:m.start()] + inner[e:]
                num = fig_numbers[k]
            text = inner.replace("\\centering", "")
        units.append((text, num, cap))
        n = len(units)
        out.append("\n\nBLOGFIG%d\n\n" % n)
        if cap is not None:
            out.append("\\begin{blogcap}\\begin{blogcapn}%d\\end{blogcapn}%s\\end{blogcap}\n\n" % (num, cap))
        i = z
    out.append(body[i:])
    return "".join(out), units


# ---------------------------------------------------------------- 第 4 步：正文清理
def clean_body(body):
    body = replace_macro(body, "texorpdfstring", 2, lambda a: a[0])
    body = replace_macro(body, "textcircled", 1,
                         lambda a: CIRCLED[int(a[0])] if a[0].strip().isdigit() and int(a[0]) < len(CIRCLED) else "(%s)" % a[0])
    body = replace_macro(body, "doubtmark", 1, lambda a: "\\textbf{【存疑】}" + a[0])
    body = replace_macro(body, "point", 1, lambda a: "\n\n\\begin{blogpt}%s\\end{blogpt}\n\n" % a[0])
    body = replace_macro(body, "paragraph", 1, lambda a: "\n\n\\begin{blogpt}%s\\end{blogpt}\n\n" % a[0])
    body = replace_macro(body, "needspace", 1, lambda a: "")
    body = replace_macro(body, "addcontentsline", 3, lambda a: "")
    for cmd in ("phantomsection", "maketitle", "hfill", "medskip", "smallskip", "bigskip", "noindent", "centering"):
        body = re.sub(r"\\%s(?![a-zA-Z])" % cmd, "", body)
    body = re.sub(r"\\pagestyle\{[^}]*\}", "", body)
    body = re.sub(r"\\vspace\*?\{[^}]*\}", "", body)
    return body


# ---------------------------------------------------------------- 图：编译、求包围盒、导出 SVG
def render_units(preamble, defs, units, workdir, prefix, img_dir):
    pages = []
    for text, _, _ in units:
        pages.append("\\clearpage\n\\noindent\\begin{minipage}{\\linewidth}\\centering\n%s\n\\end{minipage}\n" % text)
    tex = (preamble +
           "\n\\geometry{paperheight=150cm}\n"
           "\\begin{document}\n\\pagestyle{empty}\n" +
           "\n".join(defs) + "\n" + "".join(pages) + "\\end{document}\n")
    path = os.path.join(workdir, "render.tex")
    with open(path, "w") as f:
        f.write(tex)
    r = subprocess.run(["xelatex", "-interaction=nonstopmode", "-halt-on-error", "render.tex"],
                       cwd=workdir, capture_output=True, text=True)
    pdf = os.path.join(workdir, "render.pdf")
    if r.returncode != 0 or not os.path.exists(pdf):
        log = open(os.path.join(workdir, "render.log"), errors="replace").read()
        raise RuntimeError("%s 的图编译失败：\n%s" % (prefix, log[-3000:]))
    npages = int(re.search(r"Output written on render.pdf \((\d+) page", r.stdout).group(1))
    if npages != len(units):
        raise RuntimeError("%s：图 %d 个，编出 %d 页，对不上" % (prefix, len(units), npages))

    dpi = 144
    subprocess.run(["pdftoppm", "-r", str(dpi), "-gray", pdf, os.path.join(workdir, "pg")], check=True)
    names = []
    for p in range(1, npages + 1):
        pgm = [f for f in os.listdir(workdir) if re.fullmatch(r"pg-0*%d\.pgm" % p, f)][0]
        x0, y0, x1, y1 = pgm_bbox(os.path.join(workdir, pgm))
        s = 72.0 / dpi
        m = 3.0  # 四周留 3pt
        box = (x0 * s - m, y0 * s - m, (x1 - x0 + 1) * s + 2 * m, (y1 - y0 + 1) * s + 2 * m)
        name = "%s-%02d.svg" % (prefix, p)
        tmp = os.path.join(workdir, name)
        subprocess.run(["pdftocairo", "-svg", "-f", str(p), "-l", str(p), pdf, tmp], check=True)
        crop_svg(tmp, os.path.join(img_dir, name), box)
        names.append(name)
    return names


def pgm_bbox(path):
    data = open(path, "rb").read()
    m = re.match(rb"P5\s+(\d+)\s+(\d+)\s+(\d+)\s", data)
    w, h = int(m.group(1)), int(m.group(2))
    px = data[m.end():]
    rows = [y for y in range(h) if min(px[y * w:(y + 1) * w]) < 250]
    if not rows:
        raise RuntimeError("空白的图：" + path)
    y0, y1 = rows[0], rows[-1]
    x0, x1 = w, 0
    for y in range(y0, y1 + 1):
        row = px[y * w:(y + 1) * w]
        dark = [x for x in range(w) if row[x] < 250]
        if dark:
            x0, x1 = min(x0, dark[0]), max(x1, dark[-1])
    return x0, y0, x1, y1


# 显示尺寸放大倍数：博客 extra.css 把全站字号放大到 150%，图按同样比例放大，图里文字与正文的大小关系才和 PDF 一致
DISPLAY_SCALE = 1.5


def crop_svg(src, dst, box):
    x, y, w, h = box
    svg = open(src).read()
    svg = re.sub(r'<svg([^>]*?) width="[^"]*" height="[^"]*" viewBox="[^"]*"',
                 r'<svg\1 width="%.2fpt" height="%.2fpt" viewBox="%.2f %.2f %.2f %.2f"'
                 % (w * DISPLAY_SCALE, h * DISPLAY_SCALE, x, y, w, h),
                 svg, count=1)
    # pdftocairo 会先铺一整页白底，裁边后保留即可（暗色主题下图仍是白底，和 PDF 一致）
    with open(dst, "w") as f:
        f.write(svg)


# ---------------------------------------------------------------- 主流程
def convert(src_tex, src_dir):
    base = os.path.splitext(os.path.basename(src_tex))[0]
    prefix = base.split("-", 1)[0]
    raw = open(src_tex, encoding="utf-8").read()
    pre_raw, rest = raw.split("\\begin{document}", 1)
    body_raw = rest.rsplit("\\end{document}", 1)[0]

    title_m = re.search(r"\\title\{", pre_raw)
    title_txt, _ = read_group(pre_raw, title_m.end() - 1)
    # 标题进导航和浏览器标签页，用 \texorpdfstring 的纯文本写法（第二个参数）
    title_txt = replace_macro(title_txt, "texorpdfstring", 2, lambda a: a[1])
    title, _, subtitle = title_txt.partition("\\\\")
    title = re.sub(r"\s+", " ", title.replace("\\ ", " ")).strip()
    subtitle = re.sub(r"\\(large|Large|small|normalsize)\s*", "", subtitle).strip()

    body = strip_comments(body_raw)
    body, defs = extract_defs(body)
    tmacros = tikz_macros(pre_raw + "\n" + "\n".join(defs))
    body, labels, fig_numbers = number_and_label(body)
    body, units = extract_units(body, tmacros, fig_numbers)
    body = resolve_refs(body, labels)
    units = [(resolve_refs(t, labels), n, c) for t, n, c in units]
    body = clean_body(body)

    img_dir = os.path.join(OUT_DIR, "images")
    os.makedirs(img_dir, exist_ok=True)
    for f in os.listdir(img_dir):
        if f.startswith(prefix + "-"):
            os.remove(os.path.join(img_dir, f))
    with tempfile.TemporaryDirectory() as work:
        names = render_units(pre_raw, defs, units, work, prefix, img_dir) if units else []
        src_md = os.path.join(work, "body.tex")
        with open(src_md, "w") as f:
            f.write(body)
        env = dict(os.environ, BLOG_FIGS="\n".join(names))
        r = subprocess.run(["pandoc", "-f", "latex", "-t",
                            # four_space_rule：列表续行缩进 4 格，Python-Markdown 才认作同一列表项
                            "markdown_strict+pipe_tables+tex_math_dollars+raw_html+footnotes+backtick_code_blocks"
                            "+four_space_rule",
                            "--wrap=none", "--markdown-headings=atx", "--shift-heading-level-by=1",
                            "--mathjax",  # 写成 HTML 表格时公式保留 TeX 原文（\(...\)），交给 MathJax
                            "--lua-filter", LUA, src_md],
                           capture_output=True, text=True, env=env)
        if r.returncode != 0:
            raise RuntimeError("%s 的 pandoc 转换失败：\n%s" % (base, r.stderr))
        if r.stderr.strip():
            print("[%s] pandoc 提示：%s" % (base, r.stderr.strip()[:2000]))
        md = postprocess(r.stdout)

    pdf_src = os.path.join(src_dir, base + ".pdf")
    head = ["# %s\n" % title.strip()]
    if subtitle:
        head.append('<p class="notes-subtitle">%s</p>\n' % subtitle)
    if os.path.exists(pdf_src):
        os.makedirs(os.path.join(OUT_DIR, "pdf"), exist_ok=True)
        shutil.copy2(pdf_src, os.path.join(OUT_DIR, "pdf", base + ".pdf"))
        head.append("[:material-file-pdf-box: 下载 PDF 版](pdf/%s.pdf){ .md-button }\n" % base)
    head.append("<!-- 本页由 tools/signal_notes/sync.py 从 LaTeX 笔记自动生成，请勿手改；改动请改源 .tex 后重新同步。 -->\n")
    with open(os.path.join(OUT_DIR, base + ".md"), "w", encoding="utf-8") as f:
        f.write("\n".join(head) + "\n" + md)
    return base, len(units)


def postprocess(md):
    lines = md.split("\n")
    for k, line in enumerate(lines):
        # 管道表格里公式中的 | 会被当成列分隔符，换成 \vert
        if line.lstrip().startswith("|"):
            # 用 \vert{} 而不是 "\vert "：arithmatex 不认结尾 $ 前有空格的行内公式
            lines[k] = re.sub(r"\$[^$]+\$", lambda m: m.group(0).replace("|", r"\vert{}"), line)
    md = "\n".join(lines)
    # 原始 HTML 块的收尾标签后必须空一行，否则下一行（常是图片）会被当成 HTML 块的一部分而不解析
    md = re.sub(r"^(\s*</(?:div|details)>)\n(?!\n)", r"\1\n\n", md, flags=re.M)

    # pandoc 写不成管道表格时会输出 HTML 表格（--mathjax 下公式是 <span class="math inline">\(...\)</span>）。
    # 原始 HTML 不经 arithmatex，MathJax 又只排版 class="arithmatex" 的元素，所以把公式 span 的类名换掉；
    # 表格本身不能加 class，否则 Material 的表格样式（只作用于无 class 的 table）会失效。
    def fix_table(m):
        return re.sub(r'<span class="math (?:inline|display)">', '<span class="arithmatex">', m.group(0))
    md = re.sub(r"<table>.*?</table>", fix_table, md, flags=re.S)
    return md.strip() + "\n"


def write_index(done):
    lines = ["# 浙江大学 2022《信号与系统》（胡浩基）\n",
             "听课笔记，按课程顺序排列。每篇都可以下载排版好的 PDF 版。\n"]
    for base in sorted(done):
        lines.append("- [%s](%s.md)" % (base, base))
    with open(os.path.join(OUT_DIR, "index.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("only", nargs="*", help="只同步文件名含这些字样的笔记")
    ap.add_argument("--src", default=DEFAULT_SRC)
    a = ap.parse_args()
    os.environ["PATH"] = os.pathsep.join(TEX_DIRS + [os.environ.get("PATH", "")])
    for tool in ("pandoc", "xelatex", "pdftoppm", "pdftocairo"):
        if not shutil.which(tool):
            sys.exit("缺少 %s，先安装再同步。" % tool)
    texs = sorted(f for f in os.listdir(a.src) if re.match(r"\d\d-.*\.tex$", f))
    if a.only:
        texs = [f for f in texs if any(o in f for o in a.only)]
    os.makedirs(OUT_DIR, exist_ok=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda f: convert(os.path.join(a.src, f), a.src), texs))
    for base, n in results:
        print("  %-28s 图 %d 张" % (base, n))
    all_md = [os.path.splitext(f)[0] for f in os.listdir(OUT_DIR) if re.match(r"\d\d-.*\.md$", f)]
    write_index(all_md)
    print("完成，输出在", os.path.relpath(OUT_DIR, REPO))


if __name__ == "__main__":
    main()
