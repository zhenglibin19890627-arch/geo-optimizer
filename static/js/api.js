/* ============================================================
   GEO 优化系统 - 公共工具（解包 / Toast / 确认弹窗 / 气泡 / 空状态 / 格式化）
   ============================================================ */

function esc(s) {
  if (s === null || s === undefined) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/* ---------------- 品牌上下文（02d 3.1.3：localStorage 持牌，缺省 1） ---------------- */

const BRAND_KEY = "geo_brand_id";

function getBrandId() {
  const v = parseInt(localStorage.getItem(BRAND_KEY), 10);
  return v > 0 ? v : 1;
}

function setBrandId(id) {
  localStorage.setItem(BRAND_KEY, String(id));
}

function clearBrandId() {
  localStorage.removeItem(BRAND_KEY);
}

/* ---------------- 请求封装 ---------------- */

function addQuery(path, key, value) {
  const sep = path.indexOf("?") >= 0 ? "&" : "?";
  return path + sep + key + "=" + encodeURIComponent(value);
}

async function geoApi(path, options) {
  options = options || {};
  const method = (options.method || "GET").toUpperCase();
  if (method === "GET") {
    path = addQuery(path, "brand_id", getBrandId());
  } else {
    /* POST/PUT/DELETE：brand_id 放进请求体 */
    let body = options.body;
    if (typeof body === "string") {
      try {
        body = JSON.parse(body);
      } catch (e) {
        body = null;
      }
    }
    if (body && typeof body === "object" && !(body instanceof FormData)) {
      if (body.brand_id === undefined) body.brand_id = getBrandId();
      options.body = JSON.stringify(body);
    } else if (body === undefined || body === null) {
      options.body = JSON.stringify({ brand_id: getBrandId() });
    }
  }
  let resp;
  try {
    resp = await fetch(path, options);
  } catch (e) {
    showToast("网络好像断了，请检查程序是否还在运行", "error");
    throw e;
  }
  let body;
  try {
    body = await resp.json();
  } catch (e) {
    showToast("服务器开小差了，请稍后再试一次", "error");
    throw new Error("bad json");
  }
  if (body.code !== 0) {
    showToast(body.message || "出了点小问题，请稍后再试", "error");
    const err = new Error(body.message || "request failed");
    err.code = body.code;
    throw err;
  }
  return body.data;
}

function apiPost(path, data) {
  return geoApi(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data || {}),
  });
}

function apiPut(path, data) {
  return geoApi(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data || {}),
  });
}

function apiDelete(path, data) {
  if (data) {
    return geoApi(path, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
  }
  return geoApi(path, { method: "DELETE" });
}

/* ---------------- Toast ---------------- */

function showToast(msg, type) {
  let wrap = document.getElementById("toast-wrap");
  if (!wrap) {
    wrap = document.createElement("div");
    wrap.id = "toast-wrap";
    document.body.appendChild(wrap);
  }
  const el = document.createElement("div");
  el.className = "toast" + (type ? " " + type : "");
  el.textContent = msg;
  wrap.appendChild(el);
  setTimeout(() => {
    el.style.opacity = "0";
    el.style.transition = "opacity 0.3s";
    setTimeout(() => el.remove(), 300);
  }, 3000);
}

/* ---------------- 确认弹窗 ---------------- */

function confirmDialog(message, onConfirm, opts) {
  opts = opts || {};
  const mask = document.createElement("div");
  mask.className = "modal-mask";
  mask.innerHTML =
    '<div class="modal modal-confirm">' +
    '<div class="modal-head">' + esc(opts.title || "请确认") + "</div>" +
    '<div class="modal-body">' + esc(message) + "</div>" +
    '<div class="modal-foot">' +
    '<button class="btn btn-secondary" data-act="cancel">取消</button>' +
    '<button class="btn ' + (opts.danger ? "btn-danger-solid" : "btn-primary") + '" data-act="ok">' +
    esc(opts.okText || "确认删除") + "</button>" +
    "</div></div>";
  document.body.appendChild(mask);
  mask.addEventListener("click", function (e) {
    if (e.target === mask) close();
  });
  mask.querySelector('[data-act="cancel"]').addEventListener("click", close);
  mask.querySelector('[data-act="ok"]').addEventListener("click", function () {
    close();
    onConfirm();
  });
  function close() {
    mask.remove();
  }
}

/* ---------------- 概念气泡 ---------------- */

function bindConcepts(container) {
  (container || document).querySelectorAll(".concept").forEach(function (box) {
    const q = box.querySelector(".q-mark");
    const bubble = box.querySelector(".bubble");
    if (!q || !bubble) return;
    q.addEventListener("click", function (e) {
      e.stopPropagation();
      const isShow = bubble.classList.contains("show");
      document.querySelectorAll(".concept .bubble.show").forEach(function (b) {
        b.classList.remove("show");
      });
      if (!isShow) bubble.classList.add("show");
    });
  });
  document.addEventListener("click", function () {
    document.querySelectorAll(".concept .bubble.show").forEach(function (b) {
      b.classList.remove("show");
    });
  });
}

function conceptBubble(text) {
  return '<span class="concept"><span class="q-mark">?</span>' +
    '<span class="bubble">' + esc(text) + "</span></span>";
}

/* ---------------- 空状态 ---------------- */

function emptyState(title, desc, btnText, onBtn, extraBtns) {
  const div = document.createElement("div");
  div.className = "empty";
  div.innerHTML =
    '<div class="empty-icon">?</div>' +
    '<div class="empty-title">' + esc(title) + "</div>" +
    '<div class="empty-desc">' + esc(desc) + "</div>";
  if (btnText) {
    const btn = document.createElement("button");
    btn.className = "btn btn-primary";
    btn.textContent = btnText;
    btn.addEventListener("click", onBtn);
    div.appendChild(btn);
  }
  (extraBtns || []).forEach(function (b) {
    const btn = document.createElement("button");
    btn.className = b.primary ? "btn btn-primary" : "btn btn-secondary";
    btn.textContent = b.text;
    btn.style.marginLeft = "8px";
    btn.addEventListener("click", b.onClick);
    div.appendChild(btn);
  });
  return div;
}

/* ---------------- 格式化与映射 ---------------- */

function fmtTime(str) {
  if (!str) return "";
  return str;
}

function fmtDuration(seconds) {
  if (seconds === null || seconds === undefined) return "";
  const m = Math.max(Math.ceil(seconds / 60), 1);
  return m + " 分钟";
}

function scoreColor(score) {
  if (score === null || score === undefined) return "#9CA3AF";
  if (score >= 90) return "#16A34A";
  if (score >= 70) return "#2563EB";
  if (score >= 50) return "#F59E0B";
  if (score >= 30) return "#EA580C";
  return "#DC2626";
}

function scoreBand(score) {
  if (score === null || score === undefined) {
    return { text: "", color: "#9CA3AF" };
  }
  if (score >= 90) return { text: "优秀", color: "#16A34A" };
  if (score >= 70) return { text: "良好", color: "#2563EB" };
  if (score >= 50) return { text: "一般", color: "#F59E0B" };
  if (score >= 30) return { text: "偏弱", color: "#EA580C" };
  return { text: "几乎未被认识", color: "#DC2626" };
}

function scoreSentence(score) {
  if (score >= 90) return "优秀！AI 回答中经常提及你，且评价正面、顺位靠前";
  if (score >= 70) return "表现良好，AI 已认识你，仍有提升空间";
  if (score >= 50) return "表现一般，AI 偶尔提及你，建议按优化建议加强内容布局";
  if (score >= 30) return "表现偏弱，AI 较少提及你，建议尽快进行内容优化";
  return "AI 几乎未认识你，建议从「内容优化」开始逐步改进";
}

function sentimentText(code) {
  if (code === "positive") return "正向";
  if (code === "negative") return "负面";
  return "中性";
}

function sentimentTag(code) {
  const map = { positive: "正向", negative: "负面", neutral: "中性" };
  const cls = { positive: "tag-green", negative: "tag-red", neutral: "tag-gray" };
  return '<span class="tag ' + (cls[code] || "tag-gray") + '">' + (map[code] || "中性") + "</span>";
}

function sourceText(source) {
  if (source === "preset") return "预置";
  if (source === "expanded") return "扩展";
  if (source === "manual") return "手动";
  return "手动";
}

function priorityTag(priority) {
  const cls = { "高": "tag-orange", "中": "tag-primary", "低": "tag-gray" };
  return '<span class="tag ' + (cls[priority] || "tag-gray") + '">优先级:' + esc(priority || "中") + "</span>";
}

/* ---------------- 轮询（2 秒，带断线重试） ---------------- */

function startPolling(fetchFn, onData, onStop) {
  let stopped = false;
  let retryCount = 0;
  let inFlight = false;

  async function tick() {
    if (stopped) return;
    if (inFlight) {
      setTimeout(tick, 2000);
      return;
    }
    inFlight = true;
    try {
      const data = await fetchFn();
      retryCount = 0;
      inFlight = false;
      if (stopped) return;
      onData(data);
    } catch (e) {
      inFlight = false;
      if (stopped) return;
      retryCount += 1;
      if (retryCount > 3) {
        stop();
        if (onStop) onStop();
        return;
      }
      showToast("连接好像断了，正在自动重试…", "error");
      setTimeout(tick, 2000);
      return;
    }
    setTimeout(tick, 2000);
  }

  function stop() {
    stopped = true;
  }

  tick();
  return { stop: stop };
}

/* ---------------- 状态徽章 ---------------- */

function statusTag(status) {
  const map = {
    done: '<span class="tag tag-green">已完成</span>',
    running: '<span class="tag tag-primary">进行中</span>',
    pending: '<span class="tag tag-primary">等待中</span>',
    failed: '<span class="tag tag-red">未成功</span>',
    cancelled: '<span class="tag tag-gray">已停止</span>',
  };
  return map[status] || '<span class="tag tag-gray">' + esc(status) + "</span>";
}
