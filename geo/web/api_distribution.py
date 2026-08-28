"""内容分发接口：缺口简报 / 生成稿件 / 稿件库 / 官网直发（#D1~#D7）。

人工审阅制（一期）：generate 只产 draft，publish 由用户点击触发；
LLM 调用为同步长请求（简报约 10~30 秒、生成约 30~120 秒），前端.loading 提示。
"""

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
    """分发页首屏：官网配置状态 + 稿件统计。"""
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
    return ok({
        "official": {"configured": cfg["configured"], "base_url": cfg["base_url"]},
        "stats": stats,
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
        return ok(_draft_or_404(s, draft_id, brand_id).to_dict(), "获取成功")


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
    if len(title) > 100:
        raise ApiError("标题太长了（最多 100 字）")
    if not body_md:
        raise ApiError("正文不能为空")
    if len(body_md) > 100000:
        raise ApiError("正文太长了（最多 10 万字）")
    if len(summary) > distribution.SUMMARY_MAX_CHARS:
        raise ApiError(f"摘要太长了（最多 {distribution.SUMMARY_MAX_CHARS} 字）")
    from datetime import datetime
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
    """官网发布配置（Token 掩码；站点完全由用户配置，无预置站点）。"""
    brand_id = current_brand_id()
    cfg = distribution.get_official_config()
    from geo.web.api_config import _mask_key
    return ok({"base_url": cfg["base_url"], "publish_path": cfg["publish_path"],
               "category": cfg["category"], "author": cfg["author"],
               "configured": cfg["configured"],
               "token_masked": _mask_key(cfg["token"]),
               "official_domain": distribution.official_domain()}, "获取成功")


@bp.route("/distribution/config", methods=["POST"])
def save_official_config():
    """保存官网接口配置（地址/路径/分类/作者；Token 留空 = 不修改）。"""
    data = get_json()
    from geo.web.api_config import _mask_key
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
        database.set_setting("site_api_token", token)
    if base_url:
        database.set_setting("site_base_url", base_url)
    if publish_path:
        database.set_setting("site_publish_path", publish_path)
    if data.get("category") is not None and category:
        database.set_setting("site_category", category)
    if data.get("author") is not None:
        database.set_setting("site_author", author)
    cfg = distribution.get_official_config()
    return ok({"base_url": cfg["base_url"], "publish_path": cfg["publish_path"],
               "category": cfg["category"], "author": cfg["author"],
               "configured": cfg["configured"],
               "token_masked": _mask_key(cfg["token"])},
              "官网发布配置已保存，立即生效")
