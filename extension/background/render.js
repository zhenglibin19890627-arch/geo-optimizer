// 轻量 markdown → HTML 渲染器（自研，覆盖 GEO 稿件常用语法）
// 支持：标题、段落、粗斜体、行内代码、代码块、引用、有序/无序列表、链接、图片、分隔线

function escapeHtml(text) {
  return text
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderInline(text) {
  let out = escapeHtml(text);
  out = out.replace(/`([^`]+)`/g, "<code>$1</code>");
  out = out.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, '<img src="$2" alt="$1">');
  out = out.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, '<a href="$2">$1</a>');
  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  out = out.replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>");
  return out;
}

export function renderMarkdown(markdown) {
  const lines = String(markdown).replace(/\r\n/g, "\n").split("\n");
  const html = [];
  let inCode = false, codeLang = "", codeBuf = [];
  let listType = null, listBuf = [], paraBuf = [];

  const flushPara = () => {
    if (paraBuf.length) { html.push("<p>" + paraBuf.map(renderInline).join("<br>") + "</p>"); paraBuf = []; }
  };
  const flushList = () => {
    if (!listBuf.length) return;
    html.push(`<${listType}>` + listBuf.map((i) => "<li>" + renderInline(i) + "</li>").join("") + `</${listType}>`);
    listBuf = []; listType = null;
  };
  const flushAll = () => { flushPara(); flushList(); };

  for (const raw of lines) {
    const line = raw.replace(/\s+$/, "");
    const fence = line.match(/^```(\w*)\s*$/);
    if (fence) {
      if (inCode) {
        html.push(`<pre><code>${escapeHtml(codeBuf.join("\n"))}</code></pre>`);
        codeBuf = []; inCode = false;
      } else {
        flushAll(); inCode = true; codeLang = fence[1];
      }
      continue;
    }
    if (inCode) { codeBuf.push(raw); continue; }

    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) { flushAll(); html.push(`<h${heading[1].length}>` + renderInline(heading[2]) + `</h${heading[1].length}>`); continue; }
    if (/^\s*(---+|\*\*\*+)\s*$/.test(line)) { flushAll(); html.push("<hr>"); continue; }
    const quote = line.match(/^>\s?(.*)$/);
    if (quote) { flushAll(); html.push("<blockquote><p>" + renderInline(quote[1]) + "</p></blockquote>"); continue; }
    const ul = line.match(/^\s*[-*+]\s+(.*)$/);
    if (ul) { flushPara(); if (listType !== "ul") flushList(); listType = "ul"; listBuf.push(ul[1]); continue; }
    const ol = line.match(/^\s*\d+[.、]\s+(.*)$/);
    if (ol) { flushPara(); if (listType !== "ol") flushList(); listType = "ol"; listBuf.push(ol[1]); continue; }
    if (!line.trim()) { flushAll(); continue; }
    flushList(); paraBuf.push(line.trim());
  }
  if (inCode) html.push(`<pre><code>${escapeHtml(codeBuf.join("\n"))}</code></pre>`);
  flushAll();
  return {
    html: html.join("\n"),
    css: "body{font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;line-height:1.75;color:#333}pre{background:#f6f8fa;padding:12px;border-radius:6px;overflow:auto}code{background:#f6f8fa;padding:2px 5px;border-radius:3px}pre code{background:none;padding:0}blockquote{border-left:4px solid #dfe2e5;margin:0;padding:0 12px;color:#6a737d}img{max-width:100%}table{border-collapse:collapse}th,td{border:1px solid #dfe2e5;padding:6px 12px}",
    meta: { codeLangUsed: codeLang || undefined },
  };
}
