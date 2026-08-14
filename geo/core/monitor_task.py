"""监测任务编排：后台线程执行、进度与预估耗时、单条失败不整轮失败、断点续跑。"""

import random
import threading
import time
from datetime import datetime

from geo import config
from geo.analyzers import alerting, competitor_analysis, mention, scoring, sources
from geo.engines import base as engine_base, get_adapter, get_web_adapter
from geo.models import db as database

SYSTEM_PROMPT = "你是一个乐于助人的中文AI助手。请用中文客观、详细地回答用户的问题。"

# 停止本轮监测：模块级取消标志（线程安全，避免频繁查库）
_cancel_lock = threading.Lock()
_cancelled_task_ids = set()


def _is_cancelled(task_id: int) -> bool:
    with _cancel_lock:
        return task_id in _cancelled_task_ids


def cancel_monitor_task(task_id: int):
    """停止一轮监测：校验后置取消标志，由运行线程在下一个检查点收尾保存。"""
    with database.session_scope() as s:
        task = s.get(database.MonitorTask, task_id)
        if not task:
            raise engine_base.EngineError("这轮监测找不到啦")
        if task.status not in ("pending", "running"):
            raise engine_base.EngineError("这轮监测还没开始或已经结束，不用停啦")
    with _cancel_lock:
        _cancelled_task_ids.add(task_id)


def _monitor_section():
    return config.get_section("monitor", {})


def get_engine_meta(code: str) -> dict:
    adapter = get_adapter(code)
    return {"code": code, "display_name": adapter.display_name, "note": adapter.note or ""}


def any_task_running(session) -> bool:
    return (session.query(database.MonitorTask)
            .filter(database.MonitorTask.status.in_(["pending", "running"]))
            .count()) > 0


def reap_stale_tasks() -> int:
    """回收僵尸任务：程序上次退出时中断的 pending/running 任务标记为 failed。

    服务进程退出时后台监测线程会随之死亡，任务状态永远停在 running，
    导致下次启动后 any_task_running 永久拦截新监测。程序启动时调用本函数
    把这些残留任务收尾（已问到的回答已落库保留，轮次按 failed 不参与统计）。
    返回回收的任务数。
    """
    with database.session_scope() as s:
        rows = (s.query(database.MonitorTask)
                .filter(database.MonitorTask.status.in_(["pending", "running"])).all())
        for t in rows:
            t.status = "failed"
            t.error_msg = "程序上次退出时中断了这轮监测，已自动结束；重新发起一轮即可"
            if t.finished_at is None:
                t.finished_at = datetime.now()
        return len(rows)


def enabled_auto_engines() -> list:
    """已启用 且 已填钥匙 的自动引擎（用于“全部”默认勾选）。

    遍历引擎注册表 AUTO_CODES：新增引擎（如 opencode）自动纳入，无需改这里。
    """
    from geo.engines import AUTO_CODES
    result = []
    for code in AUTO_CODES:
        try:
            adapter = get_adapter(code)
        except Exception:
            continue
        if adapter.is_enabled() and adapter.is_configured():
            result.append(code)
    return result


def web_auto_engines() -> list:
    """联网档：已启用 且 已填钥匙 的联网引擎（按 supports_web_search 过滤）。"""
    from geo.engines import AUTO_CODES
    result = []
    for code in AUTO_CODES:
        try:
            adapter = get_adapter(code)
        except Exception:
            continue
        if not adapter.supports_web_search:
            continue
        if adapter.is_enabled() and adapter.is_configured():
            result.append(code)
    return result


def build_messages(question_text: str) -> list:
    """构造发给 AI 的对话：中性系统提示词 + 问题本身。

    GEO 测试原则（2026-08-14 修订）：监测提问不向 AI 注入任何品牌档案信息。
    此前曾把品牌名/简介作为"背景参考"注入系统提示词并附上"可结合实际提及
    该品牌"的引导，导致 AI 被提示词诱导点名品牌、提及率虚高（实测连续 9 轮
    100%），评分失真。现改为纯中性提问：提及率只反映 AI 的真实知识/联网检索
    水平，与"问题里不得出现品牌名"的测试原则一致。
    """
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question_text},
    ]


def normalize_models(engine_codes: list, models: dict) -> dict:
    """把前端传来的 {engine: [model,...]} 校验归一为任务模型清单。

    - models 缺省/为空 → 每家引擎用其当前档（[adapter.get_model()]）；
    - 只认 engine_codes 内的引擎；每家的模型名必须在设置页档位列表
      （含当前档）里，否则抛大白话错误；
    - 结果恒为 {code: [model, ...]}（去重、保序），供任务落库与执行循环使用。
    """
    result = {}
    for code in engine_codes or []:
        try:
            adapter = get_adapter(code)
        except Exception:
            continue
        picked = (models or {}).get(code) or []
        if isinstance(picked, str):
            picked = [picked]
        picked = [str(m).strip() for m in picked if str(m).strip()]
        if not picked:
            picked = [adapter.get_model()]
        # 校验：只允许设置页档位列表里的模型（含当前档）
        from geo.engines import adapter_meta
        try:
            meta = adapter_meta(code)
        except Exception:
            meta = {}
        allowed = {o.get("name") for o in (meta.get("model_options") or [])
                   if isinstance(o, dict) and o.get("name")}
        current = adapter.get_model()
        if current:
            allowed.add(current)
        for m in picked:
            if m not in allowed:
                raise engine_base.EngineError(
                    f"{adapter.display_name}没有「{m}」这个档位，请从档位列表里选")
        result[code] = list(dict.fromkeys(picked))
    return result


def start_monitor_task(question_ids: list, engine_codes: list,
                       task_type: str = "manual", brand_id: int = 1,
                       mode: str = "normal", models: dict = None) -> int:
    """校验并创建任务，后台线程执行。返回 task_id。

    models（可选）：{engine_code: [model, ...]}，同一把钥匙下同时监测多个模型
    （OpenCode 套餐等多档位引擎）；缺省每家引擎用当前档。
    """
    mode = str(mode or "normal").strip() or "normal"
    if mode not in ("normal", "web"):
        raise engine_base.EngineError("这个模式不认，请选择「常规提问」或「联网提问」")
    if mode == "web":
        models = None  # 联网档每家引擎固定一个联网模型
    model_map = normalize_models(engine_codes or [], models)
    with database.session_scope() as s:
        if any_task_running(s):
            raise engine_base.EngineError("已经有一轮监测在跑了，请等它完成后再发起新一轮")

        if not database.brand_exists(brand_id):
            raise engine_base.EngineError("这个品牌不存在，可能已被删除")

        qs = (s.query(database.QuestionBank)
              .filter(database.QuestionBank.brand_id == brand_id)
              .filter(database.QuestionBank.id.in_(question_ids or []))
              .filter(database.QuestionBank.enabled == True).all())
        if not qs:
            raise engine_base.EngineError("问题库是空的（或选中的问题已停用），请先到「问题库」页添加问题")

        missing = []
        for code in engine_codes or []:
            try:
                adapter = get_adapter(code)
            except Exception:
                continue
            if not adapter.is_configured():
                missing.append(adapter.display_name)
        if missing:
            names = "、".join(dict.fromkeys(missing))
            raise engine_base.EngineError(f"{names}的钥匙（API Key）还没填，请先到设置页填写")

        total = len(qs) * sum(len(model_map.get(code, [1])) for code in engine_codes or [])
        per = int(_monitor_section().get("estimated_seconds_per_call", 10) or 10)
        task = database.MonitorTask(
            type=task_type, status="pending", progress=0,
            brand_id=brand_id, mode=mode,
            total_calls=total, done_calls=0,
            estimated_seconds=total * per,
            question_ids=database.jdumps([q.id for q in qs]),
            engine_codes=database.jdumps(engine_codes or []),
            models=database.jdumps(model_map),
        )
        s.add(task)
        s.flush()
        task_id = task.id
    threading.Thread(target=run_monitor_task, args=(task_id,), daemon=True).start()
    return task_id


def _analysis_for(adapter, question, result, brand: dict,
                  competitors: list, brand_names: list) -> dict:
    """把一次 AI 调用结果解析成 MonitorResult 字段。

    result 为 ChatResult（含 text 与可选的结构化 sources）。
    信源回落：优先平台结构化引用（ChatResult.sources，{title,url,domain,category}），
    没有时从回答文本正则解析（sources.parse_sources），两者口径一致。

    情感口径（2026-08-14 修订）：只有提到我方品牌的回答才做情感判定；
    未提及的回答一律计中性——否则"夸竞品/夸行业"的好评会被算成我方正面，
    虚高净情感率。
    """
    answer_text = result.text
    is_mentioned = mention.mention_count(answer_text, brand_names) > 0
    pos = mention.brand_position(answer_text, brand_names, competitors) if is_mentioned else None
    source_list = result.sources if (result.sources or []) else sources.parse_sources(answer_text)
    return {
        "engine_code": adapter.code,
        "question_id": question.id,
        "question_text": question.text,
        "answer_text": answer_text,
        "is_mentioned": is_mentioned,
        "mention_count": mention.mention_count(answer_text, brand_names),
        "mention_position": pos,
        "sentiment": mention.sentiment(answer_text) if is_mentioned else "neutral",
        "sources": database.jdumps(source_list),
        "competitor_mentions": database.jdumps(
            mention.competitor_mentions(answer_text, competitors, brand_names)),
        "input_mode": "auto",
    }


def _round_metrics(s, round_id: int):
    """从结果计算本轮指标（提及率/净情感/评分明细）。"""
    results = (s.query(database.MonitorResult)
               .filter(database.MonitorResult.round_id == round_id).all())
    answered = [r for r in results if r.answer_text]
    total = len(answered)
    mentioned = [r for r in answered if r.is_mentioned]
    pos_count = len([r for r in answered if r.sentiment == "positive"])
    neg_count = len([r for r in answered if r.sentiment == "negative"])
    mention_rate = len(mentioned) / total if total else 0.0
    net_sentiment = (pos_count - neg_count) / total if total else 0.0
    position_scores = [scoring.position_partial(r.mention_position) for r in mentioned]
    engines_mentioned = len({r.engine_code for r in mentioned})
    engines_total = len({r.engine_code for r in answered})
    avg_depth = (sum(r.mention_count for r in mentioned) / len(mentioned)) if mentioned else 0.0
    scoring_result = scoring.compute_score(mention_rate, net_sentiment, position_scores,
                                           engines_mentioned, engines_total, avg_depth)
    return {
        "total": total,
        "mentioned_count": len(mentioned),
        "mention_rate": mention_rate,
        "net_sentiment": net_sentiment,
        "engines_mentioned": engines_mentioned,
        "engines_total": engines_total,
        "scoring": scoring_result,
    }


def task_status_map(s) -> dict:
    """任务 id -> status 映射：用于判断轮次是否正常完成（统计口径）。"""
    return {t.id: t.status for t in s.query(database.MonitorTask).all()}


def round_is_normal(status_map: dict, round_row) -> bool:
    """轮次是否正常完成：任务缺失视为正常；cancelled/failed 不算正常。"""
    return status_map.get(round_row.task_id) in (None, "done")


def _round_summary(answered_codes: dict) -> dict:
    per = {}
    for code, (total, mentioned) in answered_codes.items():
        per[code] = {
            "total": total,
            "mentioned": mentioned,
            "rate": round(mentioned / total, 3) if total else 0,
        }
    return {
        "per_engine": per,
        "total_answers": sum(v[0] for v in answered_codes.values()),
        "mentioned_answers": sum(v[1] for v in answered_codes.values()),
    }


def run_monitor_task(task_id: int):
    try:
        _run_monitor_task_inner(task_id)
    except Exception:
        import traceback
        traceback.print_exc()
        with database.session_scope() as s:
            task = s.get(database.MonitorTask, task_id)
            if task and task.status not in ("done", "failed", "cancelled"):
                task.status = "failed"
                task.error_msg = "监测中途出了点意外，请稍后再试一次"
                task.finished_at = datetime.now()


def _run_monitor_task_inner(task_id: int):
    with database.session_scope() as s:
        task = s.get(database.MonitorTask, task_id)
        if not task:
            return
        brand_id = task.brand_id or 1
        mode = task.mode or "normal"
        question_ids = database.jloads(task.question_ids, []) or []
        engine_codes = database.jloads(task.engine_codes, []) or []
        task.status = "running"
        task.started_at = task.started_at or datetime.now()

        round_row = (s.query(database.MonitorRound)
                     .filter(database.MonitorRound.task_id == task_id).first())
        if round_row is None:
            round_row = database.MonitorRound(task_id=task_id, brand_id=brand_id, mode=mode)
            s.add(round_row)
            s.flush()
        round_id = round_row.id
        existing = {(r.question_id, r.engine_code, r.model or "") for r in
                    s.query(database.MonitorResult)
                    .filter(database.MonitorResult.round_id == round_id).all()}

        brand = database.get_brand(brand_id)
        brand_names = mention.build_brand_names(brand)
        competitors = brand.get("competitors") or []
        questions = {q.id: q for q in s.query(database.QuestionBank)
                     .filter(database.QuestionBank.id.in_(question_ids)).all()}
        q_objects = [questions[qid] for qid in question_ids if qid in questions]

        # 任务模型清单（同 key 多模型）：{code: [models]}；旧任务缺省当前档
        models_map = database.jloads(task.models, {}) or {}
        for code in engine_codes:
            if not models_map.get(code):
                try:
                    models_map[code] = [get_adapter(code).get_model()]
                except Exception:
                    models_map[code] = []

        total = task.total_calls
        done = task.done_calls or 0
        errors = []
        answered_codes = {}

        cancelled = _is_cancelled(task_id)
        for code in engine_codes:
            if cancelled:
                break
            try:
                adapter = (get_web_adapter(code) if mode == "web" else get_adapter(code))
            except Exception:
                continue
            for model in (models_map.get(code) or [""]):
                if cancelled:
                    break
                for q in q_objects:
                    if (q.id, code, model or "") in existing:
                        continue
                    mon = _monitor_section()
                    lo = float(mon.get("min_interval", 1.5) or 1.5)
                    hi = float(mon.get("max_interval", 3) or 3)
                    time.sleep(random.uniform(lo, hi))
                    if _is_cancelled(task_id):
                        cancelled = True
                        break
                    # 调用 AI 前先提交，释放本连接写事务，避免与 log_api_call
                    # 的独立连接写 api_call_log 发生 SQLite 单写者锁冲突
                    s.commit()
                    try:
                        result = adapter.chat(build_messages(q.text),
                                              web_search=(mode == "web"),
                                              timeout=engine_base.get_call_timeout(),
                                              model=(model or None))
                    except engine_base.EngineError as e:
                        errors.append((adapter.display_name, e.message))
                        s.add(database.MonitorResult(
                            round_id=round_id, brand_id=brand_id, engine_code=code,
                            model=(model or None),
                            question_id=q.id, question_text=q.text, answer_text=None,
                            is_mentioned=False, mention_count=0, sentiment="neutral",
                            input_mode="auto", error_msg=e.message))
                    else:
                        analysis = _analysis_for(adapter, q, result, brand,
                                                 competitors, brand_names)
                        analysis["brand_id"] = brand_id
                        analysis["model"] = model or None
                        s.add(database.MonitorResult(round_id=round_id, **analysis))
                        answered_codes.setdefault(code, [0, 0])
                        answered_codes[code][0] += 1
                        if analysis["is_mentioned"]:
                            answered_codes[code][1] += 1
                    done += 1
                    task.done_calls = done
                    task.progress = round(done / total * 100) if total else 0
                    s.commit()
                    # 每次调用返回后复查取消标志（含最后一次调用）：取消若落在
                    # 调用在途窗口，命中即收尾为 cancelled；已答回答照常保留
                    if _is_cancelled(task_id):
                        cancelled = True
                        break

        metrics = _round_metrics(s, round_id)
        round_row.mention_rate = metrics["mention_rate"]
        round_row.net_sentiment = metrics["net_sentiment"]
        round_row.overall_score = metrics["scoring"]["total"]
        round_row.summary = database.jdumps(_round_summary(answered_codes))
        round_row.finished_at = datetime.now()

        # 收尾前兜底复查：覆盖“最后一次调用已复查完但取消标志此刻才写入”的微窗口，
        # 保证命中时走 cancelled 收尾且不产 score_snapshot/预警
        if not cancelled and _is_cancelled(task_id):
            cancelled = True

        if not cancelled and metrics["total"] > 0:
            s.add(database.ScoreSnapshot(
                round_id=round_id, brand_id=brand_id,
                score=metrics["scoring"]["total"],
                breakdown=database.jdumps(metrics["scoring"]["breakdown"])))
            # 预警：基线 = 该品牌该模式本轮之前最近的正常轮次（cancelled/failed 不计入；
            # 常规/联网轮次各自独立统计，02d 5.2）
            recent = []
            status_map = task_status_map(s)
            for r in (s.query(database.MonitorRound)
                      .filter(database.MonitorRound.id < round_id)
                      .filter(database.MonitorRound.brand_id == brand_id)
                      .filter(database.MonitorRound.mode == mode)
                      .order_by(database.MonitorRound.id.asc()).all()):
                if not round_is_normal(status_map, r) or r.mention_rate is None:
                    continue
                mc = 0
                rows = (s.query(database.MonitorResult)
                        .filter(database.MonitorResult.round_id == r.id).all())
                mc = len([x for x in rows if x.is_mentioned])
                recent.append({
                    "mention_rate": r.mention_rate, "net_sentiment": r.net_sentiment,
                    "score": r.overall_score, "mentioned_count": mc,
                })
            current = {
                "mention_rate": metrics["mention_rate"],
                "net_sentiment": metrics["net_sentiment"],
                "score": metrics["scoring"]["total"],
                "mentioned_count": metrics["mentioned_count"],
                "total_answers": metrics["total"],
            }
            try:
                alerting.evaluate_round(s, recent, current, round_id,
                                        brand_id=brand_id,
                                        brand_name=(brand.get("brand_name") or ""))
            except Exception:
                import traceback
                traceback.print_exc()

        if cancelled:
            task.status = "cancelled"
            task.error_msg = "用户主动停止"
        else:
            task.status = "done"
            if errors:
                brief = {}
                for name, msg in errors:
                    brief.setdefault(name, set()).add(msg)
                parts = [f"{name}：{'；'.join(list(msgs)[:2])}" for name, msgs in brief.items()]
                task.error_msg = "有部分问题没问到（已跳过，不影响其他结果）：" + "；".join(parts[:5])
        task.finished_at = datetime.now()
        with _cancel_lock:
            _cancelled_task_ids.discard(task_id)
        s.commit()

    # 收尾异步触发竞品深度分析（02d 3.3.3）：仅 done 且非取消分支；条件不满足
    # 或钥匙缺失时自行降级（不生成/unavailable），绝不中断本轮监测收尾
    if not cancelled:
        try:
            competitor_analysis.trigger_if_due(round_id, brand_id)
        except Exception:
            import traceback
            traceback.print_exc()
        # 收尾异步自动提取本轮回答中出现的品牌（纳入竞品分析）；零阻断
        try:
            threading.Thread(
                target=competitor_analysis.extract_auto_brands,
                args=(round_id, brand_id), daemon=True).start()
        except Exception:
            import traceback
            traceback.print_exc()


def get_progress(task_id: int) -> dict:
    with database.session_scope() as s:
        task = s.get(database.MonitorTask, task_id)
        if not task:
            raise engine_base.EngineError("这轮监测找不到啦，可能已被清理")
        total = task.total_calls or 0
        done = task.done_calls or 0
        remain = None
        if task.status in ("pending", "running") and total > 0:
            if task.started_at:
                elapsed = (datetime.now() - task.started_at).total_seconds()
                per = elapsed / max(done, 1)
                remain = int(per * max(total - done, 0))
            else:
                remain = int((task.estimated_seconds or 0) * (total - done) / max(total, 1))
        return {
            "status": task.status,
            "progress": task.progress or 0,
            "done_calls": done,
            "total_calls": total,
            "remain_seconds": remain,
            "current_desc": _current_desc(s, task, done),
            "error_msg": task.error_msg or "",
        }


def _current_desc(s, task, done: int) -> str:
    if task.status in ("done", "failed", "cancelled"):
        return "本轮监测已结束"
    if not task.started_at:
        return "正在准备问题……"
    codes = database.jloads(task.engine_codes, []) or []
    qids = database.jloads(task.question_ids, []) or []
    if not codes or not qids:
        return "正在准备问题……"
    models_map = database.jloads(task.models, {}) or {}
    # 按 (引擎 × 模型 × 问题) 顺序定位当前进度
    idx = done
    for code in codes:
        models_for = models_map.get(code) or [""]
        calls_for_engine = len(models_for) * len(qids)
        if idx >= calls_for_engine:
            idx -= calls_for_engine
            continue
        model = models_for[idx // len(qids)] if models_for else ""
        q_i = idx % len(qids)
        try:
            adapter = get_adapter(code)
            suffix = f"（模型 {model}）" if model else ""
            return f"正在问 {adapter.display_name}{suffix} 第{q_i + 1}个问题"
        except Exception:
            return "正在监测中……"
    return "正在做最后的整理……"
