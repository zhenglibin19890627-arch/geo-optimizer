// GEO 发布扩展 - 存储层（chrome.storage.local）
// 键：accounts / posts / jobs / config.defaultPublish
// 全部代码为本项目自研，不包含第三方扩展代码。

const DEFAULT_CONFIG = { defaultPublish: {} };

export async function get(key, fallback) {
  const out = await chrome.storage.local.get(key);
  return out[key] === undefined ? fallback : out[key];
}

export async function set(key, value) {
  await chrome.storage.local.set({ [key]: value });
}

// ---------- 账号 ----------

export async function listAccounts() {
  return (await get("accounts", [])).slice();
}

export async function saveAccounts(accounts) {
  await set("accounts", accounts);
}

export async function addAccount(platform, nickname) {
  const accounts = await listAccounts();
  const account = {
    id: crypto.randomUUID(),
    platform,
    nickname: nickname || `${platform} 账号`,
    enabled: true,
    createdAt: Date.now(),
  };
  accounts.push(account);
  await saveAccounts(accounts);
  // 每平台首个账号自动设为默认发布
  const config = await getConfig();
  if (!config.defaultPublish[platform]) {
    config.defaultPublish[platform] = account.id;
    await set("config", config);
  }
  return account;
}

export async function updateAccount(accountId, patch) {
  const accounts = await listAccounts();
  const account = accounts.find((a) => a.id === accountId);
  if (!account) throw new Error("账号不存在: " + accountId);
  Object.assign(account, patch);
  await saveAccounts(accounts);
  return account;
}

export async function removeAccount(accountId) {
  const accounts = (await listAccounts()).filter((a) => a.id !== accountId);
  await saveAccounts(accounts);
  const config = await getConfig();
  for (const platform of Object.keys(config.defaultPublish)) {
    if (config.defaultPublish[platform] === accountId) {
      delete config.defaultPublish[platform];
    }
  }
  await set("config", config);
}

export async function setDefaultPublish(platform, accountId) {
  const config = await getConfig();
  config.defaultPublish[platform] = accountId;
  await set("config", config);
}

export async function getConfig() {
  const config = await get("config", DEFAULT_CONFIG);
  return { defaultPublish: {}, ...config };
}

// ---------- 稿件 ----------

export async function createPost(post) {
  const now = Date.now();
  const record = {
    id: crypto.randomUUID(),
    title: String(post.title || "").trim(),
    body_md: String(post.body_md || ""),
    summary: typeof post.summary === "string" ? post.summary : undefined,
    tags: Array.isArray(post.tags) ? post.tags.filter((t) => typeof t === "string") : [],
    canonicalUrl: typeof post.canonicalUrl === "string" ? post.canonicalUrl : undefined,
    sourceUrl: typeof post.source_url === "string" ? post.source_url : undefined,
    createdAt: now,
    updatedAt: now,
  };
  if (!record.title) throw rpcError("validation_error", "标题不能为空");
  if (!record.body_md.trim()) throw rpcError("validation_error", "正文不能为空");
  const posts = await get("posts", {});
  posts[record.id] = record;
  await set("posts", posts);
  return record;
}

export async function getPost(postId) {
  const posts = await get("posts", {});
  return posts[postId] || null;
}

export async function updatePost(postId, patch) {
  const posts = await get("posts", {});
  const post = posts[postId];
  if (!post) throw rpcError("post_not_found", "稿件不存在: " + postId);
  for (const field of ["title", "body_md", "summary", "tags", "canonicalUrl"]) {
    if (patch[field] !== undefined) post[field] = patch[field];
  }
  post.updatedAt = Date.now();
  await set("posts", posts);
  return post;
}

// ---------- 发布任务 ----------

export async function createJob(postId, targets) {
  const job = {
    id: crypto.randomUUID(),
    postId,
    state: "pending",
    progress: 0,
    results: targets.map((t) => ({
      platform: t.platform,
      accountId: t.accountId,
      status: "pending",
    })),
    logs: [],
    createdAt: Date.now(),
    updatedAt: Date.now(),
  };
  const jobs = await get("jobs", {});
  jobs[job.id] = job;
  await set("jobs", jobs);
  return job;
}

export async function getJob(jobId) {
  const jobs = await get("jobs", {});
  return jobs[jobId] || null;
}

export async function updateJob(jobId, mutator) {
  const jobs = await get("jobs", {});
  const job = jobs[jobId];
  if (!job) throw rpcError("job_not_found", "任务不存在: " + jobId);
  mutator(job);
  job.updatedAt = Date.now();
  const done = job.results.filter((r) => r.status === "published" || r.status === "failed");
  const okCount = job.results.filter((r) => r.status === "published").length;
  if (done.length === job.results.length) {
    job.state = okCount > 0 ? "published" : "failed";
    job.progress = 100;
  } else {
    job.state = "running";
    job.progress = Math.round((done.length / Math.max(1, job.results.length)) * 90);
  }
  await set("jobs", jobs);
  return job;
}

export async function appendJobLog(jobId, step, message) {
  await updateJob(jobId, (job) => {
    job.logs.push({ step, message, timestamp: Date.now() });
  });
}

export async function cancelJob(jobId) {
  const jobs = await get("jobs", {});
  const job = jobs[jobId];
  if (!job) throw rpcError("job_not_found", "任务不存在: " + jobId);
  job.cancelled = true;
  job.state = "failed";
  job.progress = 100;
  for (const r of job.results) {
    if (r.status !== "published" && r.status !== "failed") {
      r.status = "failed";
      r.error = "已取消";
    }
  }
  job.updatedAt = Date.now();
  await set("jobs", jobs);
  return job;
}

// ---------- RPC 错误 ----------

export function rpcError(code, message, extra) {
  const err = new Error(message);
  err.code = code;
  if (extra !== undefined) err.extra = extra;
  return err;
}
