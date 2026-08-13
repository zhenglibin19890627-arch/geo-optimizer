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
    with database.session_scope() as s:
        row = s.get(database.OptimizationRecord, record_id)
        if not row:
            return
        row.status = "running"
        brand_id = row.brand_id or 1
        brand = database.get_brand(brand_id)
        keywords = [k.text for k in s.query(database.Keyword)
                    .filter(database.Keyword.brand_id == brand_id)
                    .filter(database.Keyword.enabled == True).all()]

        try:
            if row.input_type == "url":
                fetched = fetcher.fetch_page(row.url)
                content = fetched["text"]
            else:
                content = row.content or ""

            row.content = content[:max_chars]

            if not llm_client.is_configured():
                raise llm_client.AnalysisError(
                    "分析用的模型还没填钥匙（API Key），请先到设置页填写后再试")

            # 调用 AI 前先提交，释放本连接写事务（row.status="running" 经 autoflush
            # 已落锁），避免与 log_api_call 的独立连接写 api_call_log 发生
            # SQLite 单写者锁冲突（缺陷 #14，与 04-11.1 监测主链路同法）
            s.commit()
            suggestions = content_advice.generate_suggestions(
                content, brand, keywords)
            geo_result = content_advice.geo_score(content, brand.get("brand_name") or "", keywords)
            row.suggestions = database.jdumps(suggestions)
            row.geo_score = geo_result["score"]
            row.status = "done"
            row.error_msg = ""
        except (fetcher.FetchError, llm_client.AnalysisError) as e:
            row.status = "failed"
            row.error_msg = e.message
        except Exception:
            row.status = "failed"
            row.error_msg = "分析中途出了点意外，请稍后再试一次"
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
