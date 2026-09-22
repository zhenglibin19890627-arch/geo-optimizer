"""内容优化接口：发起优化（异步）/ 详情进度 / 历史记录（#11~#13）。"""

import threading
from datetime import datetime

from flask import Blueprint, request

from geo import config
from geo.analyzers import content_advice, llm_client
from geo.core import fetcher
from geo.models import db as database
from geo.web import ApiError, current_brand_id, get_json, ok

bp = Blueprint("api_optimize", __name__)


def _run_optimize(record_id: int):
    try:
        _run_optimize_inner(record_id)
    except Exception:
        import traceback
        traceback.print_exc()
        with database.session_scope() as s:
            row = s.get(database.OptimizationRecord, record_id)
            if row and row.status not in ("done", "failed"):
                row.status = "failed"
                row.error_msg = "分析中途出了点意外，请稍后再试一次"
                row.finished_at = datetime.now()


def _run_optimize_inner(record_id: int):
    fetch_cfg = config.get_section("fetch", {})
    max_chars = int(fetch_cfg.get("max_chars", 50000) or 50000)

    # 短事务一：落 running 并取齐输入（2026-09 评审 R5：与监测主链路同法，
    # 外部调用一律不持事务——此前靠「调 AI 前人肉 s.commit()」释放 SQLite
    # 单写者锁（缺陷 #14），现在由结构保证，不再依赖提交纪律）
    with database.session_scope() as s:
        row = s.get(database.OptimizationRecord, record_id)
        if not row:
            return
        row.status = "running"
        brand_id = row.brand_id or 1
        input_type = row.input_type
        url = row.url
        text_content = row.content or ""
        keywords = [k.text for k in s.query(database.Keyword)
                    .filter(database.Keyword.brand_id == brand_id)
                    .filter(database.Keyword.enabled == True).all()]

    brand = database.get_brand(brand_id)  # 自带独立短事务，返回普通 dict

    # ---- 外部调用窗口（无事务在手）：抓网页 + 调 AI ----
    content = text_content
    fetched = False
    suggestions = None
    geo_score = None
    try:
        if input_type == "url":
            content = fetcher.fetch_page(url)["text"]
            fetched = True

        if not llm_client.is_configured():
            raise llm_client.AnalysisError(
                "分析模型尚未填写 API 钥匙，请先到设置页填写后重试")

        suggestions = content_advice.generate_suggestions(
            content, brand, keywords)
        geo_score = content_advice.geo_score(
            content, brand.get("brand_name") or "", keywords)["score"]
        status, error_msg = "done", ""
    except (fetcher.FetchError, llm_client.AnalysisError) as e:
        status, error_msg = "failed", e.message
    except Exception:
        status, error_msg = "failed", "分析中途出了点意外，请稍后再试一次"

    # 短事务二：结果落库
    with database.session_scope() as s:
        row = s.get(database.OptimizationRecord, record_id)
        if not row:
            return
        if status == "done" or fetched:
            row.content = content[:max_chars]
        if status == "done":
            row.suggestions = database.jdumps(suggestions)
            row.geo_score = geo_score
            row.error_msg = ""
        else:
            row.error_msg = error_msg
        row.status = status
        row.finished_at = datetime.now()


@bp.route("/optimize", methods=["POST"])
def optimize():
    data = get_json()
    brand_id = current_brand_id()
    input_type = str(data.get("type") or "text").strip()
    url = str(data.get("url") or "").strip()
    content = str(data.get("content") or "").strip()

    if input_type not in ("url", "text"):
        raise ApiError("输入方式不对，请选择「网页链接」或「粘贴文字」")
    if input_type == "url" and not url:
        raise ApiError("请填一下网页链接（以 http:// 或 https:// 开头）")
    if input_type == "url" and not url.startswith(("http://", "https://")):
        raise ApiError("链接格式不对，请填完整的网址（以 http:// 或 https:// 开头），"
                       "例如 https://www.example.com/page")
    if input_type == "text" and not content:
        raise ApiError("请把要分析的文字粘贴进来")
    if input_type == "text" and len(content) < 20:
        raise ApiError("内容太短了（至少 20 个字），分析出来没有意义")

    fetch_cfg = config.get_section("fetch", {})
    max_chars = int(fetch_cfg.get("max_chars", 50000) or 50000)
    with database.session_scope() as s:
        row = database.OptimizationRecord(
            input_type=input_type, url=url or None,
            content=(content[:max_chars] if input_type == "text" else None),
            status="pending", brand_id=brand_id)
        s.add(row)
        s.flush()
        record_id = row.id

    threading.Thread(target=_run_optimize, args=(record_id,), daemon=True).start()
    return ok({"record_id": record_id}, "分析已开始，请稍等片刻就能看到优化建议")


@bp.route("/optimize/<int:record_id>", methods=["GET"])
def optimize_detail(record_id):
    brand_id = current_brand_id()
    with database.session_scope() as s:
        row = s.get(database.OptimizationRecord, record_id)
        if not row:
            raise ApiError("这条优化记录不存在，可能已被清理")
        if (row.brand_id or 1) != brand_id:
            raise ApiError("这条优化记录不属于当前品牌")
        return ok({
            "id": row.id,
            "status": row.status,
            "input_type": row.input_type,
            "url": row.url or "",
            "suggestions": database.jloads(row.suggestions, []) or [],
            "geo_score": row.geo_score,
            "error_msg": row.error_msg or "",
            "created_at": row.created_at.strftime("%Y-%m-%d %H:%M:%S") if row.created_at else None,
        }, "获取成功")


@bp.route("/optimize/history", methods=["GET"])
def optimize_history():
    brand_id = current_brand_id()
    page = max(int(request.args.get("page") or 1), 1)
    page_size = 20
    with database.session_scope() as s:
        base = (s.query(database.OptimizationRecord)
                .filter(database.OptimizationRecord.brand_id == brand_id))
        total = base.count()
        rows = (base.order_by(database.OptimizationRecord.id.desc())
                .offset((page - 1) * page_size).limit(page_size).all())
        items = [{
            "id": r.id,
            "input_type": r.input_type,
            "url": r.url or "",
            "geo_score": r.geo_score,
            "status": r.status,
            "error_msg": r.error_msg or "",
            "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else None,
        } for r in rows]
        return ok({"items": items, "total": total, "page": page, "page_size": page_size},
                  "获取成功")
