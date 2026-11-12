// MathJax 配置：只排版 arithmatex 标出的公式（pymdownx.arithmatex generic 模式）。
// 宏与「信号与系统」LaTeX 笔记导言区里的定义保持一致。
window.MathJax = {
  tex: {
    inlineMath: [["\\(", "\\)"]],
    displayMath: [["\\[", "\\]"]],
    processEscapes: true,
    processEnvironments: true,
    macros: {
      Sa: "\\operatorname{Sa}",
      d: "\\,\\mathrm{d}",
    },
  },
  options: {
    ignoreHtmlClass: ".*|",
    // md-ellipsis：标题里有公式时，目录和侧边导航的文字（最内层的 span）里会出现 \(...\) 原文，也一并排版。
    // 必须写最内层的类：ignoreHtmlClass 对每一层带类名的子元素都会重新生效
    processHtmlClass: "arithmatex|md-ellipsis",
  },
  // 行内公式超出栏宽时自动折行（MathJax 4 才支持）。不折行的话，手机上一个长公式就会把整页撑宽，
  // 浏览器只能把整页缩小显示。块级公式不折行，由 Material 的滚动容器横向滚动，多行推导保持原样对齐。
  output: {
    linebreaks: { inline: true },
  },
  startup: {
    // 首次排版完成后做一次超宽兜底（见 fitWideInlineMath）
    pageReady: () => MathJax.startup.defaultPageReady().then(fitWideInlineMath),
  },
};

// 兜底：折行后仍比所在段落宽的行内公式（可断开的位置太少），只把这一个公式改成可横向滑动的小块。
// 不对所有行内公式统一加 overflow，因为那会改变它们的基线、和文字对不齐。
function fitWideInlineMath() {
  document.querySelectorAll('mjx-container:not([display="true"])').forEach((m) => {
    m.classList.remove("mjx-scroll");
    // 和最近的块级祖先比宽度（行内元素的 clientWidth 恒为 0，不能用）
    let box = m.parentElement;
    while (box && getComputedStyle(box).display.startsWith("inline")) box = box.parentElement;
    if (box && box.clientWidth > 0 && m.getBoundingClientRect().width > box.clientWidth + 1) {
      m.classList.add("mjx-scroll");
    }
  });
}
let fitTimer;
window.addEventListener("resize", () => {
  clearTimeout(fitTimer);
  fitTimer = setTimeout(fitWideInlineMath, 200);
});

// Material 开启即时导航时页面不整页刷新，切换页面后要重新排版
document$.subscribe(() => {
  if (!MathJax.typesetPromise) return; // 首次加载由 MathJax 自己排版（startup.pageReady）
  MathJax.startup.output.clearCache?.();
  MathJax.typesetClear();
  MathJax.texReset();
  MathJax.typesetPromise().then(fitWideInlineMath);
});
