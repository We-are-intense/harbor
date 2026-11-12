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
};

// Material 开启即时导航时页面不整页刷新，切换页面后要重新排版
document$.subscribe(() => {
  MathJax.startup.output.clearCache();
  MathJax.typesetClear();
  MathJax.texReset();
  MathJax.typesetPromise();
});
