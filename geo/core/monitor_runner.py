"""监测任务执行器：后台线程运行一轮监测并落库（从 monitor_task 拆出的职责）。

任务生命周期管理（发起/停止/进度查询入口/僵尸回收）仍在 geo/core/monitor_task.py；
本模块只负责"一个 task_id 从 running 到收尾"的执行细节：
- 逐引擎 × 逐模型 × 逐问题调用 AI，单条失败不整轮失败，断点续跑；
- 每条回答即时分析落库（提及/顺位/情感/竞品明细/信源）；
- 收尾算指标、写快照、评预警，异步触发竞品提取与深度分析。
- 事务口径（2026-09 评审 R5）：数据库事务只出现在「准备 / 每条结果 / 收尾」
  三个短窗口，外部 AI 调用一律不在事务内——不再依赖调 AI 前人肉 commit
  释放 SQLite 单写者锁。
"""

import random
import threading
import time
from datetime import datetime

from geo.analyzers import alerting, competitor_analysis, mention, scoring, sources
from geo.core.model_selection import build_messages
from geo.engines import base as engine_base, get_adapter, get_web_adapter
from geo.models import db as database


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


class _QuestionRef:
    """轻量问题引用：只带 id/text 出事务（_analysis_for 只用这两个属性）。"""

    def __init__(self, qid: int, text: str):
        self.id = qid
        self.text = text


def _prepare_task(task_id: int):
    """阶段一（短事务）：任务置 running、建/取轮次、算断点续跑去重集合。

    2026-09 评审 R5 重构：整轮监测（可达 2 小时）此前从头到尾持有同一个
    session，靠「每次调 AI 前手动 s.commit()」释放 SQLite 单写者锁——任何
    维护者在调用点之前多写一行未提交的 ORM 变更，就会重新引入 database is
    locked（该坑已在 api_optimize 被迫复制同款注释）。现在改为结构保证：
    事务只出现在三个短窗口（准备 / 每条结果 / 收尾），AI 调用一律在事务外。
    返回执行上下文 dict；任务不存在返回 None。
    """
    with database.session_scope() as s:
        task = s.get(database.MonitorTask, task_id)
        if not task:
            return None
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
        existing = {(r.question_id, r.engine_code, r.model or "") for r in
                    s.query(database.MonitorResult)
                    .filter(database.MonitorResult.round_id == round_row.id).all()}
        # 只带 id→text 出事务，问题行不跨事务持有
        questions = {q.id: q.text for q in s.query(database.QuestionBank)
                     .filter(database.QuestionBank.id.in_(question_ids)).all()}
        return {
            "round_id": round_row.id,
            "brand_id": brand_id,
            "mode": mode,
            "question_ids": question_ids,
            "engine_codes": engine_codes,
            "questions": questions,
            "existing": existing,
            "models_map": database.jloads(task.models, {}) or {},
            "total": task.total_calls,
            "done": task.done_calls or 0,
        }


def _save_result(task_id: int, round_id: int, brand_id: int,
                 fields: dict, done: int, total: int):
    """阶段二助手（短事务）：单条结果 + 进度独立落库提交。

    每条回答一个独立短事务：写完即提交，写锁持有以毫秒计；外部 AI 调用绝不
    在这个事务里。done_calls/progress 同事务更新，断点续跑与进度查询即时可见。
    """
    with database.session_scope() as s:
        s.add(database.MonitorResult(round_id=round_id, brand_id=brand_id, **fields))
        task = s.get(database.MonitorTask, task_id)
        if task:
            task.done_calls = done
            task.progress = round(done / total * 100) if total else 0


def _finalize_task(mt, task_id: int, round_id: int, brand_id: int, mode: str,
                   brand: dict, answered_codes: dict, errors: list,
                   cancelled: bool) -> bool:
    """阶段三（短事务）：算指标、写评分快照、评预警、落任务终态。

    预警（alerting.evaluate_round）复用调用方 session、与轮次落库同事务的
    口径不变——只是这个事务的生命周期从「整轮监测」缩短为「收尾这几条写」。
    返回（可能被兜底复查改写的）cancelled。
    """
    with database.session_scope() as s:
        task = s.get(database.MonitorTask, task_id)
        round_row = s.get(database.MonitorRound, round_id)
        if task is None or round_row is None:
            return cancelled

        metrics = _round_metrics(s, round_id)
        round_row.mention_rate = metrics["mention_rate"]
        round_row.net_sentiment = metrics["net_sentiment"]
        round_row.overall_score = metrics["scoring"]["total"]
        round_row.summary = database.jdumps(_round_summary(answered_codes))
        round_row.finished_at = datetime.now()

        # 收尾前兜底复查：覆盖“最后一次调用已复查完但取消标志此刻才写入”的微窗口，
        # 保证命中时走 cancelled 收尾且不产 score_snapshot/预警
        if not cancelled and mt._is_cancelled(task_id):
            cancelled = True

        if not cancelled and metrics["total"] > 0:
            s.add(database.ScoreSnapshot(
                round_id=round_id, brand_id=brand_id,
                score=metrics["scoring"]["total"],
                breakdown=database.jdumps(metrics["scoring"]["breakdown"])))
            # 预警：基线 = 该品牌该模式本轮之前最近的正常轮次（cancelled/failed 不计入；
            # 常规/联网轮次各自独立统计，02d 5.2）
            recent = []
            status_map = mt.task_status_map(s)
            for r in (s.query(database.MonitorRound)
                      .filter(database.MonitorRound.id < round_id)
                      .filter(database.MonitorRound.brand_id == brand_id)
                      .filter(database.MonitorRound.mode == mode)
                      .order_by(database.MonitorRound.id.asc()).all()):
                if not mt.round_is_normal(status_map, r) or r.mention_rate is None:
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

    # 取消标志在收尾事务提交后清理（标志只影响本线程的检查点，时序无差）
    with mt._cancel_lock:
        mt._cancelled_task_ids.discard(task_id)
    return cancelled


def _run_monitor_task_inner(task_id: int):
    # 任务停止标志/统计口径助手在 monitor_task：函数内延迟导入，避免模块级循环依赖
    from geo.core import monitor_task as mt

    # ---- 阶段一：短事务准备（置 running / 建轮次 / 断点续跑去重集合） ----
    ctx = _prepare_task(task_id)
    if ctx is None:
        return
    round_id = ctx["round_id"]
    brand_id = ctx["brand_id"]
    mode = ctx["mode"]
    engine_codes = ctx["engine_codes"]
    question_ids = ctx["question_ids"]
    questions = ctx["questions"]
    existing = ctx["existing"]
    models_map = ctx["models_map"]
    total = ctx["total"]
    done = ctx["done"]

    # 任务模型清单（同 key 多模型）：{code: [models]}；旧任务缺省当前档
    # （联网档旧任务缺省联网档模型）——纯配置读取，不占事务
    for code in engine_codes:
        if not models_map.get(code):
            try:
                if mode == "web":
                    models_map[code] = [get_web_adapter(code).get_web_model()]
                else:
                    models_map[code] = [get_adapter(code).get_model()]
            except Exception:
                models_map[code] = []

    brand = database.get_brand(brand_id)  # 自带独立短事务，返回普通 dict
    brand_names = mention.build_brand_names(brand)
    # 竞品设置已取消（2026-08-15）：分析时不传档案竞品；
    # 本轮竞品由收尾自动提取后回算顺位/竞品明细（见 competitor_analysis.finalize_competitors）
    competitors = []
    errors = []
    answered_codes = {}

    # ---- 阶段二：逐引擎 × 逐模型 × 逐问题调用（事务外），每条结果独立短事务 ----
    cancelled = mt._is_cancelled(task_id)
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
            for qid in question_ids:
                if qid not in questions:
                    continue  # 问题已被删除，跳过（与原 q_objects 过滤口径一致）
                if (qid, code, model or "") in existing:
                    continue
                mon = mt._monitor_section()
                lo = float(mon.get("min_interval", 1.5) or 1.5)
                hi = float(mon.get("max_interval", 3) or 3)
                time.sleep(random.uniform(lo, hi))
                if mt._is_cancelled(task_id):
                    cancelled = True
                    break
                # 此刻手上没有任何事务（R5：原「调 AI 前先 s.commit() 释放写事务，
                # 避免与 log_api_call 的独立连接撞 SQLite 单写者锁」已由结构保证，
                # 不再依赖人肉 commit 纪律）
                try:
                    result = adapter.chat(build_messages(questions[qid]),
                                          web_search=(mode == "web"),
                                          timeout=engine_base.get_call_timeout(),
                                          model=(model or None))
                except engine_base.EngineError as e:
                    errors.append((adapter.display_name, e.message))
                    fields = {
                        "engine_code": code, "model": (model or None),
                        "question_id": qid, "question_text": questions[qid],
                        "answer_text": None, "is_mentioned": False,
                        "mention_count": 0, "sentiment": "neutral",
                        "input_mode": "auto", "error_msg": e.message,
                    }
                else:
                    fields = _analysis_for(
                        adapter, _QuestionRef(qid, questions[qid]), result, brand,
                        competitors, brand_names)
                    fields["model"] = model or None
                    answered_codes.setdefault(code, [0, 0])
                    answered_codes[code][0] += 1
                    if fields["is_mentioned"]:
                        answered_codes[code][1] += 1
                done += 1
                _save_result(task_id, round_id, brand_id, fields, done, total)
                existing.add((qid, code, model or ""))
                # 每次调用返回后复查取消标志（含最后一次调用）：取消若落在
                # 调用在途窗口，命中即收尾为 cancelled；已答回答照常保留
                if mt._is_cancelled(task_id):
                    cancelled = True
                    break

    # ---- 阶段三：短事务收尾（指标/快照/预警/任务终态） ----
    cancelled = _finalize_task(mt, task_id, round_id, brand_id, mode, brand,
                               answered_codes, errors, cancelled)

    # 收尾异步：自动提取本轮竞品 → 回算顺位/竞品明细 → 触发竞品深度分析
    # （2026-08-15 起竞品不再来自品牌设置，全部由回答自动提取）
    # 仅 done 且非取消分支；条件不满足或钥匙缺失时自行降级，绝不中断本轮监测收尾
    if not cancelled:
        try:
            threading.Thread(
                target=competitor_analysis.finalize_competitors,
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
