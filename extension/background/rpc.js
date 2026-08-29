// Native Messaging 连接管理：保持与宿主 com.geo.bridge 的长连接
// Chrome 116+ 原生消息端口可保活 MV3 service worker，因此采用“常连 + 断线退避重连”策略。

import { handleAction } from "./actions.js";

const HOST_NAME = "com.geo.bridge";
const RECONNECT_MIN_MS = 3000;
const RECONNECT_MAX_MS = 60000;

let port = null;
let backoff = RECONNECT_MIN_MS;
let connecting = false;

function connect() {
  if (port || connecting) return;
  connecting = true;
  try {
    port = chrome.runtime.connectNative(HOST_NAME);
  } catch (err) {
    console.warn("connectNative 失败：", err.message);
    port = null;
    connecting = false;
    scheduleReconnect();
    return;
  }
  connecting = false;
  backoff = RECONNECT_MIN_MS;
  port.onMessage.addListener(onMessage);
  port.onDisconnect.addListener(() => {
    const err = chrome.runtime.lastError;
    console.warn("宿主连接断开：", err ? err.message : "正常关闭");
    port = null;
    scheduleReconnect();
  });
}

function onMessage(msg) {
  if (!msg || typeof msg.id === "undefined" || !msg.action) return;
  // 每个请求独立异步处理，响应带原 id 回传
  handleAction(msg)
    .then((data) => send({ id: msg.id, ok: true, data }))
    .catch((err) => send({
      id: msg.id,
      ok: false,
      error: { code: err.code || "internal_error", message: err.message || String(err), ...(err.extra ? { extra: err.extra } : {}) },
    }));
}

function send(obj) {
  if (!port) return;
  try {
    port.postMessage(obj);
  } catch (err) {
    console.warn("回包失败：", err.message);
  }
}

function scheduleReconnect() {
  const delay = backoff;
  backoff = Math.min(backoff * 2, RECONNECT_MAX_MS);
  setTimeout(() => connect(), delay);
  // SW 可能被回收，定时器随之失效；用 alarms 兜底唤醒
  chrome.alarms.create("rpc-reconnect", { delayInMinutes: Math.max(0.5, delay / 60000) });
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "rpc-reconnect") connect();
});

chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);

connect();
