// RPC 动作路由：与 bridge 宿主契约保持一致
// health / list_platforms / list_accounts / create_post / update_post /
// publish_post / get_job_status / cancel_job / render_wechat_html

import * as store from "./store.js";
import { listPlatforms, getPlatform } from "./platforms/registry.js";
import { detectLogin } from "./platforms/login_detect.js";
import { runJob } from "./publish.js";
import { renderMarkdown } from "./render.js";
import { MANIFEST_VERSION } from "./version.js";

export async function handleAction(msg) {
  const payload = msg.payload || {};
  switch (msg.action) {
    case "health":
      return { status: "ok", version: MANIFEST_VERSION, connected: true };

    case "list_platforms":
      return listPlatforms();

    case "list_accounts":
      return listAccountsWithLogin();

    case "create_post": {
      const post = await store.createPost(payload);
      return { postId: post.id };
    }

    case "update_post": {
      const postId = requireString(payload.postId, "postId");
      await store.updatePost(postId, payload);
      return { postId };
    }

    case "publish_post":
      return publishPost(payload);

    case "get_job_status": {
      const job = await store.getJob(requireString(payload.jobId, "jobId"));
      if (!job) throw store.rpcError("job_not_found", "任务不存在");
      return {
        jobId: job.id,
        state: job.state,
        progress: job.progress,
        results: job.results.map((r) => ({
          platform: r.platform,
          status: r.status,
          ...(r.url ? { url: r.url } : {}),
          ...(r.error ? { error: r.error } : {}),
        })),
      };
    }

    case "cancel_job": {
      const jobId = requireString(payload.jobId, "jobId");
      await store.cancelJob(jobId);
      return { jobId, cancelled: true };
    }

    case "render_wechat_html":
      return renderMarkdown(payload.markdown || "", payload.options || {});

    default:
      throw store.rpcError("validation_error", "不支持的动作: " + String(msg.action));
  }
}

async function listAccountsWithLogin() {
  const accounts = await store.listAccounts();
  const config = await store.getConfig();
  const out = [];
  for (const account of accounts) {
    const det = await detectLogin(account.platform);
    out.push({
      platform: account.platform,
      accountId: account.id,
      nickname: account.nickname,
      enabled: account.enabled,
      status: det.loggedIn ? "active" : "logged_out",
      isDefaultPublish: config.defaultPublish[account.platform] === account.id,
    });
  }
  return out;
}

async function publishPost(payload) {
  const postId = requireString(payload.postId, "postId");
  const post = await store.getPost(postId);
  if (!post) throw store.rpcError("post_not_found", "稿件不存在: " + postId);
  if (!Array.isArray(payload.targets) || payload.targets.length === 0) {
    throw store.rpcError("validation_error", "targets 必须为非空数组");
  }
  const accounts = await store.listAccounts();
  const config = await store.getConfig();
  const seen = new Set();
  const targets = payload.targets.map((t) => {
    if (!t || typeof t.platform !== "string") {
      throw store.rpcError("validation_error", "target.platform 必填");
    }
    if (seen.has(t.platform)) {
      throw store.rpcError("validation_error", "平台目标重复: " + t.platform);
    }
    seen.add(t.platform);
    getPlatform(t.platform); // 校验平台支持
    // 账号解析：显式指定 > 平台默认发布 > 首个已启用账号
    let account = null;
    if (t.accountId) {
      account = accounts.find((a) => a.id === t.accountId && a.platform === t.platform);
    } else if (config.defaultPublish[t.platform]) {
      account = accounts.find((a) => a.id === config.defaultPublish[t.platform] && a.platform === t.platform);
    }
    if (!account) {
      account = accounts.find((a) => a.platform === t.platform && a.enabled);
    }
    if (!account) {
      throw store.rpcError(
        "account_not_configured",
        `平台 ${t.platform} 没有可用账号：请先在扩展选项页添加并启用账号`,
        { platform: t.platform },
      );
    }
    return { platform: t.platform, accountId: account.id, ...(t.config ? { config: t.config } : {}) };
  });
  // 幂等键透传（旧宿主无此字段 → undefined，去重自动退化为现状行为）：
  // store.createJob 按它复用未终态/已发布 job，宿主崩溃重试不再重复发文
  const job = await store.createJob(
    postId, targets, typeof payload.externalId === "string" ? payload.externalId : "");
  runJob(job.id); // 后台异步执行，状态走 get_job_status 轮询
  return { jobId: job.id };
}

export function requireString(value, name) {
  if (typeof value !== "string" || !value.trim()) {
    throw store.rpcError("validation_error", name + " 必填");
  }
  return value.trim();
}
