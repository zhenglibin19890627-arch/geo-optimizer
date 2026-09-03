"""内容分发接口：缺口简报 / 生成稿件 / 稿件库 / 官网直发（#D1~#D7）。

人工审阅制（一期）：generate 只产 draft，publish 由用户点击触发；
LLM 调用为同步长请求（简报约 10~30 秒、生成约 30~120 秒），前端.loading 提示。
"""

from datetime import datetime

from flask import Blueprint, request

from geo.core import distribution
from geo.engines.base import EngineError
from geo.models import db as database
from geo.web import ApiError, current_brand_id, get_json, ok

bp = Blueprint("api_distribution", __name__)


def _draft_or_404(s, draft_id: int, brand_id: int):
    row = s.get(database.DistributionDraft, draft_id)
    if not row:
        raise ApiError("这篇稿件不存在，可能已被删除")
    if (row.brand_id or 1) != brand_id:
        raise ApiError("这篇稿件属于其他品牌，请切换品牌后查看")
    return row


@bp.route("/distribution/overview", methods=["GET"])
def overview():
    """分发页首屏：官网配置状态 + 稿件统计 + 多平台任务统计 + 扩展在线状态。"""
    brand_id = current_brand_id()
    cfg = distribution.get_official_config()
    with database.session_scope() as s:
        rows = (s.query(database.DistributionDraft)
                .filter(database.DistributionDraft.brand_id == brand_id)
                .order_by(database.DistributionDraft.id.desc()).all())
        stats = {"total": len(rows), "draft": 0, "published": 0, "failed": 0}
        for r in rows:
            key = r.status if r.status in ("draft", "published", "failed") else "draft"
            stats[key] += 1
        channel_rows = (s.query(database.DistributionChannelTask.status)
                        .filter(database.DistributionChannelTask.brand_id == brand_id).all())
    channel_stats = {"pending": 0, "dispatching": 0, "published": 0, "failed": 0}
    for (st,) in channel_rows:
        channel_stats[st if st in channel_stats else "pending"] += 1
    return ok({
        "official": {"configured": cfg["configured"], "base_url": cfg["base_url"]},
        "stats": stats,
        "channel_stats": channel_stats,
        "agent_online": distribution.agent_online(),
        "agent": distribution.agent_status(),
        "platforms": [{"id": k, "name": v,
                       "allow_links": distribution.platform_allows_links(k)}
                      for k, v in distribution.SUPPORTED_PLATFORMS.items()],
    }, "获取成功")


@bp.route("/distribution/brief", methods=["POST"])
def create_brief():
    """按监测缺口生成创作简报（无分析钥匙时自动降级为规则简报）。"""
    brand_id = current_brand_id()
    get_json()  # 校验请求体格式（无需字段）
    try:
        brief = distribution.build_brief(brand_id)
    except EngineError as e:
        raise ApiError(e.message)
    return ok(brief, "简报已生成" if brief.get("mode") == "llm"
              else "已生成规则简报（无分析钥匙，先到设置页填写可获得智能选题）")


@bp.route("/distribution/generate", methods=["POST"])
def generate():
    """按简报生成文章草稿（draft 状态，人工审阅后才可发布）。"""
    brand_id = current_brand_id()
    data = get_json()
    user_instruction = str(data.get("user_instruction") or "").strip()
    if len(user_instruction) > 500:
        raise ApiError("用户指令太长了（最多 500 字）")
    source_round_id = data.get("source_round_id")
    brief = data.get("brief") if isinstance(data.get("brief"), dict) else None
    try:
        draft_id = distribution.generate_draft(
            brand_id, brief=brief, user_instruction=user_instruction,
            source_round_id=int(source_round_id) if source_round_id else None)
    except EngineError as e:
        raise ApiError(e.message)
    with database.session_scope() as s:
        row = _draft_or_404(s, draft_id, brand_id)
        return ok(row.to_dict(), "文章已生成，请审阅编辑后再发布")


@bp.route("/distribution/drafts", methods=["GET"])
def drafts():
    brand_id = current_brand_id()
    status = str(request.args.get("status") or "").strip()
    with database.session_scope() as s:
        q = (s.query(database.DistributionDraft)
             .filter(database.DistributionDraft.brand_id == brand_id))
        if status in ("draft", "published", "failed"):
            q = q.filter(database.DistributionDraft.status == status)
        rows = q.order_by(database.DistributionDraft.id.desc()).limit(100).all()
        items = []
        for r in rows:
            d = r.to_dict()
            d.pop("body_md", None)  # 列表不带正文，详情页再取
            d.pop("brief", None)
            items.append(d)
    return ok(items, "获取成功")


@bp.route("/distribution/drafts/<int:draft_id>", methods=["GET"])
def draft_detail(draft_id: int):
    brand_id = current_brand_id()
    with database.session_scope() as s:
        d = _draft_or_404(s, draft_id, brand_id).to_dict()
        d["channels"] = [r.to_dict() for r in
                         (s.query(database.DistributionChannelTask)
                          .filter(database.DistributionChannelTask.draft_id == draft_id)
                          .order_by(database.DistributionChannelTask.id.asc()).all())]
        return ok(d, "获取成功")


def _channels_payload(tasks):
    return [{"id": t.id, "platform": t.platform, "status": t.status,
             "platform_url": t.platform_url or "", "error_msg": t.error_msg or ""}
            for t in tasks]


def _run_site_publish(task_id: int, draft_id: int, brand_id: int):
    """官网渠道同步发布：成功→published+url，失败→failed+原因。返回 (是否成功, 说明)。"""
    try:
        result = distribution.publish_official(draft_id, brand_id)
    except EngineError as e:
        with database.session_scope() as s:
            r = s.get(database.DistributionChannelTask, task_id)
            if r:
                r.status = "failed"
                r.error_msg = e.message
                r.updated_at = datetime.now()
        return False, e.message
    url = result.get("url") or ""
    with database.session_scope() as s:
        r = s.get(database.DistributionChannelTask, task_id)
        if r:
            r.status = "published"
            r.platform_url = url
            r.error_msg = ""
            r.updated_at = datetime.now()
    return True, url


@bp.route("/distribution/drafts/<int:draft_id>/channels", methods=["POST"])
def create_channels(draft_id: int):
    """审阅后勾选平台 → 建多平台待发任务（人工审阅制：不会自动发布，
    任务由本机宿主领取后经扩展执行）。官网渠道特殊：同步直发，立即出结果。
    payload: {"platforms": ["zhihu", ...]}"""
    brand_id = current_brand_id()
    data = get_json()
    platforms = [str(p).strip() for p in (data.get("platforms") or [])]
    if not platforms:
        raise ApiError("请至少勾选一个平台")
    valid = dict(distribution.SUPPORTED_PLATFORMS)
    valid["site"] = "官网"
    bad = [p for p in platforms if p not in valid]
    if bad:
        raise ApiError(f"暂不支持的平台：{'、'.join(bad)}（当前支持："
                       f"{'、'.join(valid.values())}）")
    now = datetime.now()
    site_task_ids = []
    with database.session_scope() as s:
        draft = _draft_or_404(s, draft_id, brand_id)
        if not (draft.title or "").strip() or not (draft.body_md or "").strip():
            raise ApiError("标题和正文都不能为空，请先补全再勾选平台")
        # 同平台已有进行中/已发布的任务就不重复建；失败的任务重试走 retry
        existing = (s.query(database.DistributionChannelTask)
                    .filter(database.DistributionChannelTask.draft_id == draft_id,
                            database.DistributionChannelTask.status.in_(
                                ("pending", "dispatching", "published"))).all())
        have = {r.platform for r in existing}
        created = []
        for p in dict.fromkeys(platforms):
            if p in have:
                continue
            # 官网任务置 dispatching：宿主只领 pending，不会误抢；下面同步执行
            row = database.DistributionChannelTask(
                brand_id=brand_id, draft_id=draft_id, platform=p,
                status="dispatching" if p == "site" else "pending", created_at=now)
            s.add(row)
            created.append(row)
        s.flush()
        site_task_ids = [(r.id, r.draft_id) for r in created if r.platform == "site"]
    site_note = ""
    for task_id, d_id in site_task_ids:
        ok_flag, info = _run_site_publish(task_id, d_id, brand_id)
        site_note = "官网" + ("已发布" + (f"（{info}）" if info else "")
                             if ok_flag else f"发布失败（{info}）")
    return ok(_channels_payload(created),
              f"已排队 {len(created)} 个平台"
              + ("；" + site_note if site_note else "")
              + ("（其余平台已有进行中的任务）" if len(created) < len(set(platforms)) else ""))


@bp.route("/distribution/drafts/<int:draft_id>/channels", methods=["GET"])
def list_channels(draft_id: int):
    brand_id = current_brand_id()
    with database.session_scope() as s:
        _draft_or_404(s, draft_id, brand_id)
        items = (s.query(database.DistributionChannelTask)
                 .filter(database.DistributionChannelTask.draft_id == draft_id)
                 .order_by(database.DistributionChannelTask.id.asc()).all())
        return ok(_channels_payload(items), "获取成功")


@bp.route("/distribution/channels/<int:task_id>/retry", methods=["POST"])
def retry_channel(task_id: int):
    """失败任务单独重试：failed → pending，宿主下一轮领取。"""
    brand_id = current_brand_id()
    with database.session_scope() as s:
        row = s.get(database.DistributionChannelTask, task_id)
        if not row:
            raise ApiError("这个分发任务不存在，可能已被清理")
        if (row.brand_id or 1) != brand_id:
            raise ApiError("这个任务属于其他品牌，请切换品牌后操作")
        if row.status not in ("failed", "published"):
            raise ApiError("只有失败（或已发布需重发）的任务可以重试")
        is_site = row.platform == "site"
        draft_id_for_site = row.draft_id
        row.status = "dispatching" if is_site else "pending"
        row.error_msg = ""
        row.platform_url = ""
        row.job_id = ""
        row.updated_at = datetime.now()
    if is_site:
        ok_flag, info = _run_site_publish(task_id, draft_id_for_site, brand_id)
        return ok({"ok": ok_flag},
                  "官网" + ("已发布" + (f"：{info}" if info else "") if ok_flag
                            else f"发布失败：{info}"))
    return ok(None, "已重新排队，等待分发桥领取")


@bp.route("/distribution/channels/<int:task_id>/manual", methods=["POST"])
def manual_publish(task_id: int):
    """人工确认发布：自动化发不出、用户在保留的编辑器页面手动发出后，
    在稿件库点「手动已发」把这条渠道记为已发布（统计口径与自动发布一致）。"""
    brand_id = current_brand_id()
    data = get_json()
    platform_url = str(data.get("platform_url") or "").strip()[:500]
    now = datetime.now()
    with database.session_scope() as s:
        row = s.get(database.DistributionChannelTask, task_id)
        if not row or row.brand_id != brand_id:
            raise ApiError("这条分发任务不存在")
        if row.status == "published":
            # 幂等：重复点「手动已发」视为补填/更新链接，不报错
            if platform_url and platform_url != (row.platform_url or ""):
                row.platform_url = platform_url
                row.updated_at = now
                return ok({"task_id": task_id}, "该渠道已记录过，发布链接已更新")
            return ok({"task_id": task_id}, "该渠道已经是已发布状态，无需重复记录")
        row.status = "published"
        if platform_url:
            row.platform_url = platform_url
        row.error_msg = "（手动确认发布）"
        row.published_at = now
        row.updated_at = now
        draft = s.get(database.DistributionDraft, row.draft_id) if row.draft_id else None
        if draft and draft.status != "published":
            draft.status = "published"
            if platform_url:
                draft.published_url = platform_url
            draft.published_at = now
            draft.error_msg = ""
            draft.updated_at = now
    return ok({"task_id": task_id}, "已记录为手动发布成功")


@bp.route("/distribution/drafts/<int:draft_id>/advice", methods=["POST"])
def draft_advice(draft_id: int):
    """审阅环节的 GEO 优化建议：规则评分 + AI 建议（原内容优化页能力并入分发流程）。

    allow_links（默认 True）：勾选了不允许外链的平台时前端传 False，
    评分按「无外链依赖」口径。"""
    brand_id = current_brand_id()
    allow_links = bool((request.get_json(silent=True) or {}).get("allow_links", True))
    with database.session_scope() as s:
        row = _draft_or_404(s, draft_id, brand_id)
        content = (row.title or "") + "\n\n" + (row.body_md or "")
    from geo.analyzers import content_advice
    brand = database.get_brand(brand_id)
    keywords = []
    with database.session_scope() as s:
        keywords = [k.text for k in s.query(database.Keyword)
                    .filter(database.Keyword.brand_id == brand_id)
                    .filter(database.Keyword.enabled == True).all()]  # noqa: E712
    score = content_advice.geo_score(content, brand.get("brand_name") or "我的品牌",
                                     keywords, allow_links=allow_links)
    try:
        suggestions = content_advice.generate_suggestions(content, brand, keywords)
    except Exception:
        suggestions = []
    return ok({"score": score, "suggestions": suggestions}, "优化建议已生成")


@bp.route("/distribution/drafts/<int:draft_id>/optimize", methods=["POST"])
def draft_optimize(draft_id: int):
    """审阅环节的一键 GEO 优化：按优化维度重写稿件，并回显优化前后评分。

    只重写不落库：新稿回填编辑器，人工确认后点「保存修改」才生效，保持审阅制。
    """
    brand_id = current_brand_id()
    allow_links = bool((request.get_json(silent=True) or {}).get("allow_links", True))
    with database.session_scope() as s:
        row = _draft_or_404(s, draft_id, brand_id)
        title = (row.title or "").strip()
        body = (row.body_md or "").strip()
    if not body:
        raise ApiError("这篇稿件还没有正文，没法优化")
    from geo.analyzers import content_advice
    from geo.analyzers.llm_client import AnalysisError
    brand = database.get_brand(brand_id)
    keywords = []
    with database.session_scope() as s:
        keywords = [k.text for k in s.query(database.Keyword)
                    .filter(database.Keyword.brand_id == brand_id)
                    .filter(database.Keyword.enabled == True).all()]  # noqa: E712
    brand_name = brand.get("brand_name") or "我的品牌"
    score_before = content_advice.geo_score(title + "\n\n" + body, brand_name,
                                            keywords, allow_links=allow_links)
    try:
        result = content_advice.apply_geo_optimization(title, body, brand, keywords,
                                                       allow_links=allow_links)
    except AnalysisError as e:
        # 落一行日志便于排查（HTTP 仍是 200，访问日志里看不到失败原因）
        print(f"[optimize] 稿件 {draft_id} 一键优化失败：{e.message}")
        raise ApiError(e.message)
    score_after = content_advice.geo_score(
        result["title"] + "\n\n" + result["body_md"], brand_name, keywords,
        allow_links=allow_links)
    return ok({"title": result["title"], "body_md": result["body_md"],
               "changes": result["changes"],
               "score_before": score_before, "score_after": score_after},
              "优化稿已生成，请确认后保存")


@bp.route("/distribution/drafts/<int:draft_id>", methods=["PUT"])
def update_draft(draft_id: int):
    """人工审阅编辑：标题/正文/摘要/标签。"""
    brand_id = current_brand_id()
    data = get_json()
    title = str(data.get("title") or "").strip()
    body_md = str(data.get("body_md") or "").strip()
    summary = str(data.get("summary") or "").strip()
    tags = str(data.get("tags") or "").strip()
    if not title:
        raise ApiError("标题不能为空")
    title = title[:30]  # 平台标题上限 30 字：超长静默截断（编辑器输入框已限长）
    if not body_md:
        raise ApiError("正文不能为空")
    if len(body_md) > 100000:
        raise ApiError("正文太长了（最多 10 万字）")
    if len(summary) > distribution.SUMMARY_MAX_CHARS:
        raise ApiError(f"摘要太长了（最多 {distribution.SUMMARY_MAX_CHARS} 字）")
    with database.session_scope() as s:
        row = _draft_or_404(s, draft_id, brand_id)
        row.title = title
        row.body_md = body_md
        row.summary = summary
        row.tags = tags
        row.updated_at = datetime.now()
        d = row.to_dict()
    return ok({k: d[k] for k in ("id", "title", "summary", "tags", "status",
                                 "updated_at")}, "稿件已保存")


@bp.route("/distribution/drafts/<int:draft_id>", methods=["DELETE"])
def delete_draft(draft_id: int):
    brand_id = current_brand_id()
    with database.session_scope() as s:
        row = _draft_or_404(s, draft_id, brand_id)
        # 级联清理多平台分发任务（避免孤儿任务被宿主领取）
        (s.query(database.DistributionChannelTask)
         .filter(database.DistributionChannelTask.draft_id == draft_id).delete())
        s.delete(row)
    return ok(None, "稿件已删除")


@bp.route("/distribution/drafts/<int:draft_id>/publish", methods=["POST"])
def publish(draft_id: int):
    """发布到自有官网（同步调用；失败会落 error_msg，可修稿后重试）。"""
    brand_id = current_brand_id()
    try:
        result = distribution.publish_official(draft_id, brand_id)
    except EngineError as e:
        raise ApiError(e.message)
    return ok(result, "已发布到官网" + (f"：{result['url']}" if result.get("url") else ""))


@bp.route("/distribution/config", methods=["GET"])
def get_official_config():
    """官网发布配置（按品牌独立；Token 掩码；站点完全由用户配置，无预置站点）。"""
    brand_id = current_brand_id()
    cfg = distribution.get_official_config(brand_id)
    from geo.web.api_config import _mask_key
    return ok({"base_url": cfg["base_url"], "publish_path": cfg["publish_path"],
               "category": cfg["category"], "author": cfg["author"],
               "configured": cfg["configured"],
               "brand_id": brand_id,
               "token_masked": _mask_key(cfg["token"]),
               "official_domain": distribution.official_domain()}, "获取成功")


@bp.route("/distribution/config", methods=["POST"])
def save_official_config():
    """保存官网接口配置（按品牌独立存储；地址/路径/分类/作者；Token 留空 = 不修改）。"""
    data = get_json()
    brand_id = current_brand_id()
    from geo.web.api_config import _mask_key
    suffix = f"_b{brand_id}" if brand_id else ""
    base_url = str(data.get("base_url") or "").strip().rstrip("/")
    publish_path = str(data.get("publish_path") or "").strip()
    category = str(data.get("category") or "").strip()
    author = str(data.get("author") or "").strip()
    token = str(data.get("token") or "").strip()
    if base_url:
        if not base_url.startswith(("http://", "https://")):
            raise ApiError("接口地址必须以 http:// 或 https:// 开头")
        if len(base_url) > 300:
            raise ApiError("接口地址太长了")
    if publish_path:
        if not publish_path.startswith("/"):
            publish_path = "/" + publish_path
        if len(publish_path) > 200:
            raise ApiError("接口路径太长了")
    if len(category) > 50:
        raise ApiError("分类太长了（最多 50 字）")
    if len(author) > 100:
        raise ApiError("作者太长了（最多 100 字）")
    if token:
        if len(token) > 200:
            raise ApiError("Token 太长了，请检查是否复制完整")
        database.set_setting("site_api_token" + suffix, token)
    if base_url:
        database.set_setting("site_base_url" + suffix, base_url)
    if publish_path:
        database.set_setting("site_publish_path" + suffix, publish_path)
    if data.get("category") is not None and category:
        database.set_setting("site_category" + suffix, category)
    if data.get("author") is not None:
        database.set_setting("site_author" + suffix, author)
    cfg = distribution.get_official_config(brand_id)
    return ok({"base_url": cfg["base_url"], "publish_path": cfg["publish_path"],
               "category": cfg["category"], "author": cfg["author"],
               "configured": cfg["configured"],
               "token_masked": _mask_key(cfg["token"])},
              "官网发布配置已保存，立即生效")
