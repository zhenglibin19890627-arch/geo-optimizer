// 发布任务执行器：按目标逐平台调用适配器，更新任务进度与结果

import * as zhihu from "./platforms/zhihu.js";
import * as sohu from "./platforms/sohu.js";
import * as toutiao from "./platforms/toutiao.js";

// MV3 service worker 禁止动态 import()，适配器必须静态导入
const ADAPTERS = { zhihu, sohu, toutiao };

import { getPlatform } from "./platforms/registry.js";
import * as store from "./store.js";

const RUNNING = new Set(); // jobId 防并发重复执行

export async function runJob(jobId) {
  if (RUNNING.has(jobId)) return;
  const job = await store.getJob(jobId);
  if (!job || (job.state !== "pending" && job.state !== "running")) return;
  RUNNING.add(jobId);
  try {
    for (const result of job.results) {
      const current = await store.getJob(jobId);
      if (current.cancelled) break; // 已取消：剩余目标保持 failed/已取消
      if (result.status === "published" || result.status === "failed") continue;
      await runTarget(current, result);
    }
  } finally {
    RUNNING.delete(jobId);
  }
}

async function runTarget(job, result) {
  const log = (message) => store.appendJobLog(job.id, result.platform, message);
  try {
    result.status = "running";
    await store.updateJob(job.id, (j) => {});
    const platform = getPlatform(result.platform);
    const accounts = await store.listAccounts();
    const account = accounts.find((a) => a.id === result.accountId);
    if (!account) throw store.rpcError("account_not_found", "账号不可用: " + result.accountId);
    const post = await store.getPost(job.postId);
    if (!post) throw store.rpcError("post_not_found", "稿件不存在: " + job.postId);
    await log("开始发布");
    const adapter = await getAdapter(result.platform);
    const landing = await adapter.publish({ platform, account, post, log });
    result.status = "published";
    result.url = landing.url;
    await log("发布成功: " + landing.url);
  } catch (err) {
    result.status = "failed";
    result.error = err.message || String(err);
    await log("发布失败: " + result.error);
  }
  await store.updateJob(job.id, (j) => {
    const target = j.results.find((r) => r.platform === result.platform);
    Object.assign(target, { status: result.status, url: result.url, error: result.error });
  });
}

// 适配器获取：静态表（MV3 SW 禁动态 import）
function getAdapter(platformId) {
  const adapter = ADAPTERS[platformId];
  if (!adapter) throw store.rpcError("unsupported_platform", "未注册的适配器: " + platformId);
  return adapter;
}
