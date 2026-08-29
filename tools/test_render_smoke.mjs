// render.js 冒烟测试：node 直跑（无需浏览器）
import { renderMarkdown } from "../extension/background/render.js";

const md = [
  "# 标题一",
  "",
  "段落带 **粗体**、*斜体*、`行内代码` 和 [链接](https://example.com/a)。",
  "",
  "```js",
  "const x = 1 < 2 && 3 > 2;",
  "```",
  "",
  "> 引用一行",
  "",
  "- 列表甲",
  "- 列表乙",
  "",
  "1. 有序甲",
  "2. 有序乙",
  "",
  "---",
  "",
  "![图片](https://img.example.com/x.png)",
].join("\n");

const { html } = renderMarkdown(md);
const checks = [
  ["<h1>标题一</h1>", html.includes("<h1>标题一</h1>")],
  ["粗体", html.includes("<strong>粗体</strong>")],
  ["斜体", html.includes("<em>斜体</em>")],
  ["行内代码", html.includes("<code>行内代码</code>")],
  ["链接", html.includes('<a href="https://example.com/a">链接</a>')],
  ["代码块转义", html.includes("const x = 1 &lt; 2 &amp;&amp; 3 &gt; 2;")],
  ["引用", html.includes("<blockquote>")],
  ["无序列表", html.includes("<ul>") && html.includes("<li>列表甲</li>")],
  ["有序列表", html.includes("<ol>") && html.includes("<li>有序乙</li>")],
  ["分隔线", html.includes("<hr>")],
  ["图片", html.includes('<img src="https://img.example.com/x.png"')],
  ["无未转义裸 <", !/<(?!h1|p|strong|em|code|pre|blockquote|ul|ol|li|hr|img|a|br|\/)/.test(html)],
];
let fail = 0;
for (const [name, ok] of checks) {
  console.log((ok ? "PASS" : "FAIL") + " " + name);
  if (!ok) fail++;
}
console.log(fail === 0 ? "渲染器冒烟测试全部通过" : `${fail} 项失败`);
process.exit(fail === 0 ? 0 : 1);
