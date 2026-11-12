-- pandoc Lua 过滤器：把 sync.py 预处理后的 LaTeX 转成 Python-Markdown（MkDocs Material）能渲染的 Markdown。
-- 自定义环境在 pandoc 里是带类名的 Div，这里逐个映射成 Material 的 admonition / 例题标题 / 图。

local figs = {}
for name in (os.getenv("BLOG_FIGS") or ""):gmatch("[^\n]+") do table.insert(figs, name) end

-- 原始块末尾多带一个换行：pandoc 相邻原始块之间只隔一个换行，Python-Markdown 需要空行才分得开
local function raw(s) return pandoc.RawBlock("html", s .. "\n") end
local function rawi(s) return pandoc.RawInline("html", s) end
local function md(s) return pandoc.RawBlock("markdown", s .. "\n") end
local function mdi(s) return pandoc.RawInline("markdown", s) end

local BOXES = {
  remember  = { '<div class="admonition tip" markdown>\n<p class="admonition-title">重点</p>', "</div>" },
  note      = { '<div class="admonition note" markdown>\n<p class="admonition-title">说明</p>', "</div>" },
  skippable = { '<details class="abstract" markdown>\n<summary>可跳过</summary>', "</details>" },
}
local UNWRAP = { center = true, minipage = true, flushleft = true, flushright = true, quote = true }

local function inlines_of(div)
  if not div then return pandoc.Inlines({}) end
  return pandoc.utils.blocks_to_inlines(div.content)
end

local function child(div, cls)
  for _, b in ipairs(div.content) do
    if b.t == "Div" and b.classes[1] == cls then return b end
  end
end

local function trim(ils)
  while #ils > 0 and (ils[1].t == "Space" or ils[1].t == "SoftBreak") do ils:remove(1) end
  while #ils > 0 and (ils[#ils].t == "Space" or ils[#ils].t == "SoftBreak") do ils:remove(#ils) end
  return ils
end

function Math(m)
  local t = m.text:gsub("^%s+", ""):gsub("%s+$", "")
  -- 公式里的 < > 换成 \lt{} \gt{}：在 md_in_html 区块（重点框等）里，「<M」会被 HTML 解析器当成标签开头，吞掉后文
  t = t:gsub("<", "\\lt{}"):gsub(">", "\\gt{}")
  m.text = t
  return m
end

-- 粗体、斜体直接写成 HTML 标签：博客开了 betterem smart 模式，「**证**稳定」这种结尾 ** 紧跟汉字的写法不会被识别
function Strong(s)
  local out = pandoc.Inlines({ rawi("<strong>") }); out:extend(s.content); out:insert(rawi("</strong>"))
  return out
end
function Emph(s)
  local out = pandoc.Inlines({ rawi("<em>") }); out:extend(s.content); out:insert(rawi("</em>"))
  return out
end

-- 颜色等样式 Span 只保留文字
function Span(s)
  if s.attributes.style then return s.content end
end

function Div(el)
  local c = el.classes[1]
  local box = BOXES[c]
  if box then
    local out = { raw(box[1]) }
    for _, b in ipairs(el.content) do table.insert(out, b) end
    table.insert(out, raw(box[2]))
    return out
  end
  if UNWRAP[c] then return el.content end
  if c == "blogex" then
    local n = pandoc.utils.stringify(child(el, "blogexn"))
    local title = trim(inlines_of(child(el, "blogext")))
    local conc = trim(inlines_of(child(el, "blogexc")))
    local note = trim(inlines_of(child(el, "blogexo")))
    local ils = pandoc.Inlines({ rawi('<span class="ex-badge">例 ' .. n .. "</span>"), pandoc.Space() })
    ils:insert(pandoc.Strong(title))
    if #note > 0 then
      ils:insert(pandoc.Space()); ils:insert(rawi("<small>")); ils:extend(note); ils:insert(rawi("</small>"))
    end
    if #conc > 0 then
      ils:insert(pandoc.Space()); ils:insert(rawi('<span class="ex-tag">')); ils:extend(conc); ils:insert(rawi("</span>"))
    end
    ils:insert(mdi("\n{: .ex-head }"))
    return pandoc.Para(ils)
  end
  if c == "blogcap" then
    local n = pandoc.utils.stringify(child(el, "blogcapn"))
    local rest = {}
    for _, b in ipairs(el.content) do
      if not (b.t == "Div" and b.classes[1] == "blogcapn") then table.insert(rest, b) end
    end
    local ils = pandoc.Inlines({ pandoc.Strong({ pandoc.Str("图 " .. n) }), pandoc.Space() })
    ils:extend(trim(pandoc.utils.blocks_to_inlines(rest)))
    ils:insert(mdi("\n{: .fig-caption }"))
    return pandoc.Para(ils)
  end
end

-- 段落里的块级公式拆成独立的 $$ 块（arithmatex 只认独占成块的 $$）；占位段落 BLOGFIGn 换成图片
local function split_para(el, ctor)
  if #el.content == 1 and el.content[1].t == "Str" then
    local n = el.content[1].text:match("^BLOGFIG(%d+)$")
    if n then
      local name = figs[tonumber(n)]
      return md("![](images/" .. name .. "){ .notes-fig loading=lazy }")
    end
  end
  local has = false
  for _, x in ipairs(el.content) do
    if x.t == "Math" and x.mathtype == "DisplayMath" then has = true end
  end
  if not has then return nil end
  local out, cur = {}, pandoc.Inlines({})
  local function flush()
    cur = trim(cur)
    if #cur > 0 then table.insert(out, ctor(cur)) end
    cur = pandoc.Inlines({})
  end
  for _, x in ipairs(el.content) do
    if x.t == "Math" and x.mathtype == "DisplayMath" then
      flush()
      table.insert(out, md("$$\n" .. x.text .. "\n$$"))
    else
      cur:insert(x)
    end
  end
  flush()
  return out
end

function Para(el) return split_para(el, pandoc.Para) end
function Plain(el) return split_para(el, pandoc.Plain) end

-- 小标题 blogpt 并进紧跟的段落开头（LaTeX 里 \point 是接着同一段往下写的）
function Blocks(blocks)
  local out = pandoc.Blocks({})
  local i = 1
  while i <= #blocks do
    local b = blocks[i]
    if b.t == "Div" and b.classes[1] == "blogpt" then
      local head = pandoc.Inlines({ rawi('<span class="pt-head">▸ '), pandoc.Strong(trim(inlines_of(b))), rawi("</span>") })
      local nxt = blocks[i + 1]
      if nxt and (nxt.t == "Para" or nxt.t == "Plain") then
        head:insert(pandoc.Space())
        head:extend(nxt.content)
        out:insert(pandoc.Para(head))
        i = i + 2
      else
        out:insert(pandoc.Para(head))
        i = i + 1
      end
    else
      out:insert(b)
      i = i + 1
    end
  end
  return out
end

return {
  { Math = Math, Span = Span },
  { Div = Div },
  { Blocks = Blocks },
  { Para = Para, Plain = Plain },
  { Strong = Strong, Emph = Emph },  -- 最后做：前几轮生成的例题标题、小标题、图号里也有 Strong
}
