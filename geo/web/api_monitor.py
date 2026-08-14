"""监测接口：发起监测 / 进度 / 手动粘贴 / 轮次列表与详情（#14~#18）。"""

from flask import Blueprint, request

from geo.analyzers import mention, sources
from geo.engines import AUTO_CODES, base as engine_base, get_adapter
from geo.models import db as database
from geo.core import monitor_task
from geo.web import ApiError, current_brand_id, get_json, ok

bp = Blueprint("api_monitor", __name__)


# ---------------- #14 发起监测 ----------------
@bp.route("/monitor/start", methods=["POST"])
def monitor_start():
    data = get_json()
    brand_id = current_brand_id()
    question_ids = data.get("question_ids")
    engine_codes = data.get("engine_codes")
    mode = str(data.get("mode") or "normal").strip() or "normal"
    if mode not in ("normal", "web"):
        raise ApiError("这个模式不认，请选择「常规提问」或「联网提问」")

    if mode == "web":
        # 联网档：只留支持联网的引擎（opencode 无联网能力自动排除）
        web_codes = [c for c in AUTO_CODES if _supports_web(c)]
        if engine_codes:
            engine_codes = [c for c in engine_codes if c in web_codes]
            if not engine_codes:
                raise ApiError("联网提问只有 DeepSeek、豆包、Kimi、通义千问和腾讯元宝能参加"
                               "（OpenCode 暂不支持联网），请重新勾选")
        else:
            engine_codes = [c for c in web_codes if _configured_enabled(c)]
        if not engine_codes:
            raise ApiError("联网提问需要 DeepSeek、豆包、Kimi、通义千问、腾讯元宝里至少一家"
                           "填好钥匙（API Key），请先到设置页填写")

    with database.session_scope() as s:
        if not question_ids:
            question_ids = [q.id for q in s.query(database.QuestionBank)
                            .filter(database.QuestionBank.brand_id == brand_id)
                            .filter(database.QuestionBank.enabled == True).all()]

    if mode != "web" and not engine_codes:
        engine_codes = monitor_task.enabled_auto_engines()
        if not engine_codes:
            raise ApiError("还没有任何一家 AI 引擎填好钥匙（API Key），"
                           "请先到设置页填写至少一家的钥匙，再发起监测")

    # 同 key 多模型（仅常规档）：{engine: [model, ...]}，联网档忽略
    models = data.get("models") if mode != "web" else None
    if models is not None and not isinstance(models, dict):
        raise ApiError("模型选择格式不对，请刷新页面后重试")

    try:
        task_id = monitor_task.start_monitor_task(
            question_ids, engine_codes, task_type="manual", brand_id=brand_id,
            mode=mode, models=models)
    except engine_base.EngineError as e:
        raise ApiError(e.message)
    with database.session_scope() as s:
        task = s.get(database.MonitorTask, task_id)
        return ok({
            "task_id": task_id,
            "estimated_seconds": task.estimated_seconds,
            "total_calls": task.total_calls,
            "mode": mode,
        }, "监测已开始，正在挨家 AI 提问，请稍等")


def _supports_web(code: str) -> bool:
    try:
        return bool(monitor_task.get_adapter(code).supports_web_search)
    except Exception:
        return False


def _configured_enabled(code: str) -> bool:
    try:
        adapter = monitor_task.get_adapter(code)
        return adapter.is_enabled() and adapter.is_configured()
    except Exception:
        return False


def _task_owned(s, task_id: int, brand_id: int):
    """取任务并校验归属当前品牌，防止跨品牌操作（M12/M13）。"""
    task = s.get(database.MonitorTask, task_id)
    if not task:
        return None
    if (task.brand_id or 1) != brand_id:
        raise ApiError("这轮监测不属于当前品牌，切换品牌后再试吧")
    return task


# ---------------- #15 监测进度 ----------------
@bp.route("/monitor/tasks/<int:task_id>/progress", methods=["GET"])
def monitor_progress(task_id):
    brand_id = current_brand_id()
    with database.session_scope() as s:
        if not _task_owned(s, task_id, brand_id):
            raise ApiError("这轮监测找不到啦，可能已被清理")
    try:
        return ok(monitor_task.get_progress(task_id), "获取成功")
    except engine_base.EngineError as e:
        raise ApiError(e.message)


# ---------------- 停止本轮监测（04c 裁决事项 1） ----------------
@bp.route("/monitor/tasks/<int:task_id>/cancel", methods=["POST"])
def monitor_cancel(task_id):
    brand_id = current_brand_id()
    with database.session_scope() as s:
        if not _task_owned(s, task_id, brand_id):
            raise ApiError("这轮监测找不到啦，可能已被清理")
    try:
        monitor_task.cancel_monitor_task(task_id)
    except engine_base.EngineError as e:
        raise ApiError(e.message)
    return ok({"cancelled": True}, "已停止本轮监测，已问到的回答已保存")


# ---------------- #16 手动粘贴 ----------------
@bp.route("/monitor/paste", methods=["POST"])
def monitor_paste():
    data = get_json()
    brand_id = current_brand_id()
    engine_code = str(data.get("engine_code") or "manual").strip()
    question_text = str(data.get("question_text") or "").strip()
    answer_text = str(data.get("answer_text") or "").strip()

    if engine_code not in AUTO_CODES and engine_code != "manual":
        raise ApiError("没找到这家引擎，请刷新页面后再试")
    if not question_text:
        raise ApiError("请填一下你问 AI 的问题（问题内容）")
    if not answer_text:
        raise ApiError("请把 AI 的回答粘贴进来（回答内容）")

    adapter = get_adapter(engine_code)
    brand = database.get_brand(brand_id)
    brand_names = mention.build_brand_names(brand)
    competitors = brand.get("competitors") or []

    question_id = None
    with database.session_scope() as s:
        q = (s.query(database.QuestionBank)
             .filter(database.QuestionBank.brand_id == brand_id)
             .filter(database.QuestionBank.text == question_text).first())
        if q:
            question_id = q.id

    is_mentioned = mention.mention_count(answer_text, brand_names) > 0
    pos = mention.brand_position(answer_text, brand_names, competitors) if is_mentioned else None
    # 情感口径：未提及品牌的回答一律计中性（夸竞品/夸行业不算我方正面）
    sentiment = mention.sentiment(answer_text) if is_mentioned else "neutral"
    with database.session_scope() as s:
        row = database.MonitorResult(
            round_id=None,
            brand_id=brand_id,
            engine_code=engine_code,
            question_id=question_id,
            question_text=question_text,
            answer_text=answer_text,
            is_mentioned=is_mentioned,
            mention_count=mention.mention_count(answer_text, brand_names),
            mention_position=pos,
            sentiment=sentiment,
            sources=database.jdumps(sources.parse_sources(answer_text)),
            competitor_mentions=database.jdumps(
                mention.competitor_mentions(answer_text, competitors, brand_names)),
            input_mode="paste",
        )
        s.add(row)
        s.flush()
        result = row.to_dict()

    result["display_name"] = adapter.display_name
    return ok({"result_id": result["id"], "result": result},
              "分析完成：这家 AI 是否提到你、评价如何、引用了哪些信源，请看下方结果")


# ---------------- #17 轮次列表 ----------------
@bp.route("/monitor/rounds", methods=["GET"])
def rounds_list():
    brand_id = current_brand_id()
    page = max(int(request.args.get("page") or 1), 1)
    page_size = 20
    with database.session_scope() as s:
        base = s.query(database.MonitorRound).filter(database.MonitorRound.brand_id == brand_id)
        total = base.count()
        rows = (base.order_by(database.MonitorRound.id.desc())
                .offset((page - 1) * page_size).limit(page_size).all())
        items = []
        for r in rows:
            d = r.to_dict()
            task = s.get(database.MonitorTask, r.task_id) if r.task_id else None
            d["task_type"] = task.type if task else ""
            d["task_status"] = task.status if task else ""
            items.append(d)
        return ok({"items": items, "total": total, "page": page, "page_size": page_size},
                  "获取成功")


# ---------------- #18 轮次详情 ----------------
@bp.route("/monitor/rounds/<int:round_id>", methods=["GET"])
def round_detail(round_id):
    brand_id = current_brand_id()
    with database.session_scope() as s:
        round_row = s.get(database.MonitorRound, round_id)
        if not round_row:
            raise ApiError("这轮监测不存在，可能已被清理")
        if (round_row.brand_id or 1) != brand_id:
            raise ApiError("这轮数据属于其他品牌，切换品牌后再查看吧")
        summary = round_row.to_dict()

        results = (s.query(database.MonitorResult)
                   .filter(database.MonitorResult.round_id == round_id)
                   .order_by(database.MonitorResult.id.asc()).all())
        result_list = []
        for r in results:
            d = r.to_dict()
            try:
                d["display_name"] = get_adapter(r.engine_code).display_name
            except Exception:
                d["display_name"] = r.engine_code
            result_list.append(d)

        # 竞品对比：自己 vs 各家竞品（用该轮归属品牌的档案，历史轮次永不串品牌）
        brand = database.get_brand(round_row.brand_id or 1)
        brand_names = mention.build_brand_names(brand)
        competitor_compare = _competitor_compare(result_list, brand, brand_names)

        # 引用信源排行
        sources_top = _sources_top(result_list)

        notes = []
        if any(r.engine_code == "yuanbao" for r in results):
            try:
                notes.append(get_adapter("yuanbao").note or "")
            except Exception:
                pass

        return ok({
            "summary": summary,
            "results": result_list,
            "competitor_compare": competitor_compare,
            "sources_top": sources_top,
            "notes": notes,
        }, "获取成功")


def _competitor_compare(result_list: list, brand: dict, brand_names: list) -> list:
    answered = [r for r in result_list if r.get("answer_text")]
    total = max(len(answered), 1)
    entities = [brand.get("brand_name") or ""] + (brand.get("competitors") or [])
    items = []
    for name in entities:
        name = str(name).strip()
        if not name:
            continue
        count = len([r for r in answered if name in r.get("answer_text", "")])
        items.append({"name": name, "mention_count": count,
                      "mention_rate": round(count / total, 3),
                      "is_self": name == (brand.get("brand_name") or "")})
    return items


def _sources_top(result_list: list, limit: int = 10) -> list:
    from geo.analyzers import sources as sources_mod
    agg = {}
    for r in result_list:
        for src in r.get("sources") or []:
            url = src.get("url", "")
            if not url:
                continue
            domain = sources_mod.normalize_domain(src.get("domain") or "")
            if not domain:
                continue
            item = agg.setdefault(domain, {"url": url, "domain": domain, "count": 0})
            item["count"] += 1
    items = []
    for domain, item in sorted(agg.items(), key=lambda kv: -kv[1]["count"]):
        items.append({
            "url": item["url"], "domain": item["domain"],
            "site_name": sources_mod.site_name(item["domain"]),
            "category": sources_mod.classify_domain(item["domain"]),
            "count": item["count"],
        })
    return items[:limit]
