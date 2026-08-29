"""Agent 接口（二期）：Python 原生宿主（org.synccaster.bridge）专用的机对机端点。

宿主循环：heartbeat → claim 领取待发任务 → 驱动扩展 create_post/publish_post
→ get_job_status 轮询 → 把结果回写到这里。鉴权：settings 里配置了
agent_bridge_token 时必须带 X-Agent-Token 头；未配置则仅靠本机部署边界
（Flask 只听 127.0.0.1 + 无 Origin 放行脚本直连）。
"""

import re
from datetime import datetime

from flask import Blueprint, request

from geo.core import distribution
from geo.models import db as database
from geo.web import ApiError, ok

bp = Blueprint("api_agent", __name__)

# 任务状态机里允许宿主写入的目标状态（对外 dispatched，库内 dispatching）
_ALLOWED_STATES = ("dispatched", "published", "failed")
# 宿主 版本协商（无强制，仅记录）
_VERSION_RE = re.compile(r"^[\w./\-]{0,60}$")


def _check_agent_token():
    expected = str(database.get_setting("agent_bridge_token", "") or "").strip()
    if not expected:
        return
    got = request.headers.get("X-Agent-Token") or ""
    if got != expected:
        raise ApiError("分发桥的令牌不对，请检查宿主配置里的 agent_bridge_token", 401)


@bp.route("/agent/heartbeat", methods=["POST"])
def heartbeat():
    """宿主每 30 秒一次：记录在线状态、版本与各平台登录快照（若有）。"""
    _check_agent_token()
    data = request.get_json(silent=True) or {}
    version = str(data.get("version") or "")
    if not _VERSION_RE.match(version):
        version = ""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    database.set_setting("agent_last_seen", now_str)
    if version:
        database.set_setting("agent_version", version)
    accounts = data.get("accounts")
    if isinstance(accounts, list):
        clean = []
        for a in accounts:
            if not isinstance(a, dict):
                continue
            clean.append({
                "platform": str(a.get("platform") or "")[:30],
                "nickname": str(a.get("nickname") or "")[:50],
                "enabled": bool(a.get("enabled")),
                "is_default": bool(a.get("is_default")),
                "status": str(a.get("status") or "")[:30],
            })
        database.set_setting("agent_accounts",
                             database.jdumps({"at": now_str, "accounts": clean}))
    return ok({"server_time": now_str,
               "heartbeat_ttl": distribution.AGENT_HEARTBEAT_TTL}, "在线")


@bp.route("/agent/claim", methods=["POST"])
def claim_tasks():
    """领取待发任务：pending → dispatching，返回稿件全文供宿主驱动扩展。

    payload: {"limit": 1~10, "platforms": ["zhihu", ...]}（platforms 缺省 = 全部支持平台）
    宿主应逐单驱动扩展（create_post → publish_post → 轮询）并回写。
    """
    _check_agent_token()
    data = request.get_json(silent=True) or {}
    try:
        limit = int(data.get("limit") or 5)
    except (TypeError, ValueError):
        limit = 5
    limit = max(1, min(limit, 10))
    platforms = [str(p).strip() for p in (data.get("platforms") or [])]
    bad = [p for p in platforms if p not in distribution.SUPPORTED_PLATFORMS]
    if bad:
        raise ApiError(f"不支持的平台：{'、'.join(bad)}")
    now = datetime.now()
    with database.session_scope() as s:
        q = (s.query(database.DistributionChannelTask)
             .filter(database.DistributionChannelTask.status == "pending")
             .order_by(database.DistributionChannelTask.id.asc()))
        if platforms:
            q = q.filter(database.DistributionChannelTask.platform.in_(platforms))
        rows = q.limit(limit).all()
        items = []
        for r in rows:
            draft = s.get(database.DistributionDraft, r.draft_id) if r.draft_id else None
            if not draft:
                r.status = "failed"
                r.error_msg = "关联稿件已被删除"
                r.updated_at = now
                continue
            r.status = "dispatching"
            r.updated_at = now
            items.append({
                "task_id": r.id,
                "platform": r.platform,
                "platform_name": distribution.SUPPORTED_PLATFORMS.get(r.platform, r.platform),
                "account_id": r.account_id or "",
                "draft": {
                    "draft_id": draft.id,
                    "title": draft.title or "",
                    "body_md": draft.body_md or "",
                    "summary": (draft.summary or "")[:distribution.SUMMARY_MAX_CHARS],
                    "tags": [t.strip() for t in (draft.tags or "").split(",") if t.strip()],
                },
            })
    return ok(items, f"领取 {len(items)} 个任务")


@bp.route("/agent/tasks/<int:task_id>/status", methods=["POST"])
def report_status(task_id: int):
    """回写任务结果。

    payload: {"state": "dispatched"|"published"|"failed",
              "job_id"?, "platform_url"?, "error_msg"?}
    dispatched = publish_post 已被扩展受理（拿到 jobId）；published 必须带
    platform_url（拿不到落地页时允许传平台主页，但会记 warning 字段提示）。
    """
    _check_agent_token()
    data = request.get_json(silent=True) or {}
    state = str(data.get("state") or "").strip()
    if state not in _ALLOWED_STATES:
        raise ApiError("状态只允许 dispatched / published / failed")
    job_id = str(data.get("job_id") or "").strip()[:100]
    platform_url = str(data.get("platform_url") or "").strip()[:500]
    error_msg = str(data.get("error_msg") or "").strip()[:500]
    if state == "published" and not platform_url:
        raise ApiError("回写 published 时必须带 platform_url（没有就先回 failed 并写明原因）")
    now = datetime.now()
    with database.session_scope() as s:
        row = s.get(database.DistributionChannelTask, task_id)
        if not row:
            raise ApiError("这个分发任务不存在，可能已被清理")
        row.updated_at = now
        if job_id:
            row.job_id = job_id
        if state == "dispatched":
            row.status = "dispatching"
        elif state == "published":
            row.status = "published"
            row.platform_url = platform_url
            row.error_msg = ""
            row.published_at = now
            # 任一渠道首次发布成功：稿件同步标记已发布（官网路径之外的渠道发布口径）
            draft = s.get(database.DistributionDraft, row.draft_id) if row.draft_id else None
            if draft and draft.status not in ("published",):
                draft.status = "published"
                draft.published_url = platform_url
                draft.published_at = now
                draft.error_msg = ""
                draft.updated_at = now
        else:
            row.status = "failed"
            row.error_msg = error_msg or "扩展侧未返回失败原因"
    return ok({"task_id": task_id, "state": state}, "已回写")


@bp.route("/agent/queue", methods=["GET"])
def queue_stats():
    """队列概览：宿主启动自检 & 分发页复用。"""
    _check_agent_token()
    with database.session_scope() as s:
        rows = s.query(database.DistributionChannelTask.status).all()
    stats = {"pending": 0, "dispatching": 0, "published": 0, "failed": 0}
    for (st,) in rows:
        stats[st if st in stats else "pending"] += 1
    return ok({"stats": stats, "agent_online": distribution.agent_online(),
               "platforms": [{"id": k, "name": v}
                             for k, v in distribution.SUPPORTED_PLATFORMS.items()]}, "获取成功")


@bp.route("/agent/articles-sync/pending", methods=["GET"])
def articles_sync_pending():
    """宿主轮询：是否有待执行的文章同步请求（设置键 article_sync_request）。"""
    _check_agent_token()
    platform = str(database.get_setting("article_sync_request", "") or "").strip()
    return ok({"platform": platform})


@bp.route("/agent/platform-articles", methods=["POST"])
def platform_articles_ingest():
    """宿主回传扩展拉取的平台历史文章：按 URL 去重入库，并清掉同步请求。"""
    _check_agent_token()
    data = request.get_json(silent=True) or {}
    platform = str(data.get("platform") or "").strip()
    articles = data.get("articles") or []
    error = str(data.get("error") or "").strip()
    now = datetime.now()
    added = 0
    with database.session_scope() as s:
        if error and not articles:
            database.set_setting("article_sync_request", "")
            database.set_setting("article_sync_result",
                                  f"{platform}：同步失败——{error[:200]}")
            return ok({"added": 0}, "同步失败已记录")
    with database.session_scope() as s:
        for a in articles:
            if not isinstance(a, dict):
                continue
            url = str(a.get("url") or "").strip()
            title = str(a.get("title") or "").strip()
            if not url or not title:
                continue
            exists = (s.query(database.PlatformArticle)
                      .filter(database.PlatformArticle.platform == platform,
                              database.PlatformArticle.url == url).first())
            if exists:
                continue
            s.add(database.PlatformArticle(
                platform=platform, title=title[:300], url=url[:500],
                publish_time=str(a.get("publish_time") or "")[:20],
                imported_at=now))
            added += 1
        database.set_setting("article_sync_request", "")
        database.set_setting(
            "article_sync_result",
            f"{platform}:{added} 篇新入库（共收到 {len(articles)} 篇）")
    return ok({"added": added, "received": len(articles)}, f"已入库 {added} 篇")
