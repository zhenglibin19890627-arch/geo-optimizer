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
    if engine_codes is not None and not isinstance(engine_codes, list):
        raise ApiError("引擎选择格式不对，请刷新页面后重试")

    # 模式（2026-08-19 起支持一轮多模式）：优先读 modes 数组（可同时勾
    # 常规+联网）；旧前端单 mode 字符串继续兼容
    raw_modes = data.get("modes")
    if raw_modes is not None and not isinstance(raw_modes, list):
        raise ApiError("监测模式格式不对，请刷新页面后重试")
    if raw_modes is not None and not [m for m in raw_modes
                                      if str(m).strip() in ("normal", "web")]:
        raise ApiError("请至少选择一种监测模式（常规提问或联网提问）")
    if raw_modes:
        modes = list(dict.fromkeys(
            str(m).strip() for m in raw_modes if str(m).strip() in ("normal", "web")))
    else:
        mode = str(data.get("mode") or "normal").strip() or "normal"
        if mode not in ("normal", "web"):
            raise ApiError("这个模式不认，请选择「常规提问」或「联网提问」")
        modes = [mode]
    if not modes:
        raise ApiError("请至少选择一种监测模式（常规提问或联网提问）")

    # 模型选择：新版嵌套 {normal: {engine: [...]}, web: {...}}；
    # 旧版扁平 {engine: [...]}（跟随单一 mode）。两者都没有 = 各引擎用当前档
    models = data.get("models")
    if models is not None and not isinstance(models, dict):
        raise ApiError("模型选择格式不对，请刷新页面后重试")
    nested = None
    flat = None
    if isinstance(models, dict) and any(
            isinstance(models.get(k), dict) for k in ("normal", "web")):
        nested = models
    elif models is not None:
        flat = models

    with database.session_scope() as s:
        if not question_ids:
            question_ids = [q.id for q in s.query(database.QuestionBank)
                            .filter(database.QuestionBank.brand_id == brand_id)
                            .filter(database.QuestionBank.enabled == True).all()]

    web_codes = [c for c in AUTO_CODES if _supports_web(c)]
    specs = []
    for m in modes:
        per = (nested or {}).get(m) if nested is not None else flat
        if per is not None and not isinstance(per, dict):
            raise ApiError("模型选择格式不对，请刷新页面后重试")
        per = per or {}
        if m == "web":
            cand = [c for c in (engine_codes or web_codes) if c in web_codes]
            mode_label = "联网提问"
        else:
            cand = list(engine_codes or AUTO_CODES)
            mode_label = "常规提问"
        if per:
            # 有模型选择时：选中口径以具体模型为准（没勾模型的引擎不参加该模式）
            engines = [c for c in cand if per.get(c)]
            models_m = {c: per[c] for c in engines}
        elif nested is not None or flat is not None:
            # 传了模型选择但该模式一个都没勾（如嵌套里 web: {}）→ 明确拦截，
            # 不能静默回落全队默认档（那会意外发起一轮真实计费监测）
            raise ApiError(f"{mode_label}还没有勾选任何模型，请至少勾选一个")
        else:
            # 完全没传模型选择（旧调用）：候选引擎整队参加，各用当前档
            engines = list(cand)
            models_m = None
        if not engines:
            if m == "web":
                raise ApiError("联网提问需要 DeepSeek、豆包、通义千问、腾讯元宝里"
                               "至少一家勾选了模型（OpenCode 订阅 API 暂不支持联网）")
            raise ApiError(f"{mode_label}还没有勾选任何模型，请至少勾选一个")
        specs.append((m, engines, models_m))

    try:
        task_ids = monitor_task.start_serial_monitor_task(
            question_ids, specs, task_type="manual", brand_id=brand_id)
    except engine_base.EngineError as e:
        raise ApiError(e.message)
    with database.session_scope() as s:
        tasks = [s.get(database.MonitorTask, tid) for tid in task_ids]
        return ok({
            "task_ids": task_ids,
            "task_id": task_ids[0],  # 旧前端兼容
            "modes": [t.mode for t in tasks],
            "total_calls": sum(t.total_calls or 0 for t in tasks),
            "estimated_seconds": sum(t.estimated_seconds or 0 for t in tasks),
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
    # 竞品设置已取消（2026-08-15）：手动粘贴无自动提取流程，竞品名单为空
    competitors = []

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
            raise ApiError("该轮监测不存在，可能已被清理")
        if (round_row.brand_id or 1) != brand_id:
            raise ApiError("该轮数据属于其他品牌，请切换品牌后查看")
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

        # 竞品对比：自己 vs 本轮自动提取的竞品（2026-08-15 起竞品不再来自品牌设置）
        brand = database.get_brand(round_row.brand_id or 1)
        brand_names = mention.build_brand_names(brand)
        auto = database.jloads(round_row.auto_competitors, []) or []
        competitor_compare = _competitor_compare(result_list, brand, brand_names, auto)

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


def _competitor_compare(result_list: list, brand: dict, brand_names: list,
                        auto: list = None) -> list:
    answered = [r for r in result_list if r.get("answer_text")]
    total = max(len(answered), 1)
    entities = [brand.get("brand_name") or ""] + list(auto or [])
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
