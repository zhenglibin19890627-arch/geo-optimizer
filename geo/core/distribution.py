"""内容分发闭环（一期）：监测缺口 → 创作简报 → AI 生成稿件 → 人工审阅 → 官网直发。

- 缺口快照（build_gap_snapshot）：近 N 个正常轮次里，AI 引用了哪些信源域名、
  我方已布点哪些域名（已发布稿件 + 官网域名）、哪些问题提及率最低、竞品动向
  与最新内容优化建议——这是「写什么」的依据；
- 创作简报（build_brief）：分析模型把缺口快照提炼成主题清单（标题方向 +
  蒸馏词 + 目标信源域）；无钥匙或调用失败自动降级为规则简报（零费用可用）；
- 生成稿件（generate_draft）：按简报 + 品牌档案生成 Markdown 文章，落库为
  draft 状态，人工审阅/编辑后才允许发布（一期人工审阅制）；
- 官网直发（publish_official）：POST {base_url}{publish_path}，X-Api-Token 鉴权；
  站点接口完全可配置（地址/路径/Token/分类/作者），可指向任意提供该约定的
  自有站点，系统本身不预置任何具体站点（token 等在分发页配置）。
"""

import re
from datetime import datetime
from urllib.parse import urlparse

import requests as requests_lib

from geo.analyzers import llm_client, sources as sources_mod
from geo.engines.base import EngineError
from geo.models import db as database

# 站点发布配置的通用缺省：地址/路径留空 = 未配置（不预置任何具体站点）
OFFICIAL_DEFAULT_PATH = "/api/articles/publish"
OFFICIAL_DEFAULT_CATEGORY = "公司动态"
BRIEF_MAX_ROUNDS = 30
WEAK_QUESTION_MIN_ANSWERS = 3
WEAK_QUESTION_TOP = 8
TOP_DOMAINS = 15
# 生成正文的安全长度：与官网摘要上限一致，tags 截断防超长
SUMMARY_MAX_CHARS = 500

# ---------------- 多平台分发（二期） ----------------
# 首批平台：key 与发布扩展平台注册表的 id 一致，value 为展示名；
# 扩展自身支持更多平台（约 19 个），后续按需扩充此清单即可。
SUPPORTED_PLATFORMS = {"zhihu": "知乎", "sohu": "搜狐号", "toutiao": "今日头条"}
# 平台主域：发布成功后并入「已布点」，信源排行按主域后缀匹配生效
PLATFORM_DOMAINS = {"zhihu": "zhihu.com", "sohu": "sohu.com", "toutiao": "toutiao.com"}
# 宿主心跳新鲜度（秒）：超过则分发页显示「扩展离线」
AGENT_HEARTBEAT_TTL = 70


# ---------------- 官网配置（settings 表存储，分发页可改） ----------------

def get_official_config() -> dict:
    """站点发布配置（settings 表存储，分发页可改；不预置任何具体站点）。

    兼容回退：早期版本用过 official_api_base/official_api_token 两个键，
    新键缺失时回落旧键（仅回退用户保存过的值，不带任何默认站点）。
    """
    base_url = (database.get_setting("site_base_url", None)
                or database.get_setting("official_api_base", None) or "")
    base_url = str(base_url).strip().rstrip("/")
    token = str(database.get_setting("site_api_token", None)
                or database.get_setting("official_api_token", None) or "").strip()
    path = str(database.get_setting("site_publish_path", None)
               or OFFICIAL_DEFAULT_PATH).strip()
    if not path.startswith("/"):
        path = "/" + path
    category = str(database.get_setting("site_category", None)
                   or OFFICIAL_DEFAULT_CATEGORY).strip()
    author = str(database.get_setting("site_author", None) or "").strip()
    return {
        "base_url": base_url,
        "publish_path": path,
        "category": category,
        "author": author,
        "token": token,
        "configured": bool(base_url and token),
    }


def official_domain() -> str:
    base_url = get_official_config()["base_url"]
    try:
        return sources_mod.normalize_domain(urlparse(base_url).hostname or "") if base_url else ""
    except Exception:
        return ""


# ---------------- 已布点域名（供信源卡「已布点」标注） ----------------

def deployed_domains(brand_id: int) -> set:
    """我方内容已覆盖的域名：已发布稿件的落地页域名 + 官网域名 + 已发布平台主域。"""
    domains = set()
    official = official_domain()
    if official:
        domains.add(official)
    with database.session_scope() as s:
        rows = (s.query(database.DistributionDraft)
                .filter(database.DistributionDraft.brand_id == brand_id,
                        database.DistributionDraft.status == "published").all())
        for r in rows:
            d = _url_domain(r.published_url)
            if d:
                domains.add(d)
        # 多平台任务：发布成功的平台主域直接计入（platform_url 子域由后缀匹配覆盖）
        rows = (s.query(database.DistributionChannelTask)
                .filter(database.DistributionChannelTask.brand_id == brand_id,
                        database.DistributionChannelTask.status == "published").all())
        for r in rows:
            d = PLATFORM_DOMAINS.get(r.platform)
            if d:
                domains.add(d)
            d = _url_domain(r.platform_url)
            if d:
                domains.add(d)
    return domains


def agent_online() -> bool:
    """宿主心跳是否新鲜（分发页「扩展在线」状态灯）。"""
    from datetime import datetime, timedelta
    last = str(database.get_setting("agent_last_seen", "") or "")
    if not last:
        return False
    try:
        dt = datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return False
    return datetime.now() - dt <= timedelta(seconds=AGENT_HEARTBEAT_TTL)


# 宿主领取后超过这么久仍无下文，视为分发桥中断（宿主被杀/断电等）
CHANNEL_DISPATCH_STALE_MINUTES = 30


def agent_status() -> dict:
    """分发页用的扩展综合状态：在线灯 + 各平台登录快照。"""
    snap = database.jloads(database.get_setting("agent_accounts", "") or "", None) or {}
    accounts = snap.get("accounts") if isinstance(snap, dict) else None
    return {"online": agent_online(), "accounts": accounts or [],
            "accounts_at": snap.get("at") if isinstance(snap, dict) else None}


def reap_stale_channel_tasks(max_age_minutes: int = CHANNEL_DISPATCH_STALE_MINUTES) -> int:
    """回收卡在 dispatching 的僵尸任务（宿主中断时来不及回写）。

    不自动重新排队：宿主断连前扩展可能已把文章发出去，盲目重发会在
    平台上产生重复内容。落为 failed 并写明原因，由人在分发页决定重试。
    返回回收条数。GEO 启动时自动调用一次。
    """
    from datetime import datetime, timedelta
    cutoff = datetime.now() - timedelta(minutes=max_age_minutes)
    with database.session_scope() as s:
        rows = (s.query(database.DistributionChannelTask)
                .filter(database.DistributionChannelTask.status == "dispatching",
                        database.DistributionChannelTask.updated_at < cutoff).all())
        for r in rows:
            r.status = "failed"
            r.error_msg = "分发桥中断，发布结果未知；请到平台确认后再决定是否重试"
            r.updated_at = datetime.now()
        return len(rows)


def _url_domain(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    try:
        if not re.match(r"^https?://", url):
            url = "https://" + url
        return _root_domain(urlparse(url).hostname or "")
    except Exception:
        return ""


def _root_domain(host: str) -> str:
    """主域 = 去掉子域后的最后两段（blog.csdn.net → csdn.net）。

    已布点按主域口径：发布在知乎/CSDN 任何子域都算覆盖该站，
    与信源排行里的具体子域（zhuanlan.zhihu.com 等）做后缀匹配。
    """
    host = sources_mod.normalize_domain(host or "")
    parts = [p for p in host.split(".") if p]
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


def domain_covered(host: str, deployed: set) -> bool:
    """host 是否已被布点覆盖：等于某主域，或是其子域。"""
    host = (host or "").strip().lower()
    if not host:
        return False
    for d in deployed:
        if host == d or host.endswith("." + d):
            return True
    return False


# ---------------- 缺口快照 ----------------

def _recent_normal_rounds(brand_id: int, limit: int = BRIEF_MAX_ROUNDS) -> list:
    """最近 limit 个正常轮（cancelled/failed 不计入），与报告页口径一致。"""
    from geo.core import monitor_task
    with database.session_scope() as s:
        status_map = monitor_task.task_status_map(s)
        rows = [r for r in (s.query(database.MonitorRound)
                            .filter(database.MonitorRound.brand_id == brand_id)
                            .order_by(database.MonitorRound.id.desc()).all())
                if monitor_task.round_is_normal(status_map, r)]
        return list(reversed(rows[:limit]))


def build_gap_snapshot(brand_id: int, limit_rounds: int = BRIEF_MAX_ROUNDS) -> dict:
    """从监测数据提取缺口快照（纯规则法，零费用，无钥匙也可用）。"""
    brand = database.get_brand(brand_id)
    rounds = _recent_normal_rounds(brand_id, limit_rounds)
    rids = [r.id for r in rounds]

    domain_counts = {}
    question_stats = {}
    latest_scores = []
    with database.session_scope() as s:
        if rids:
            results = (s.query(database.MonitorResult)
                       .filter(database.MonitorResult.round_id.in_(rids)).all())
            for r in results:
                if not r.answer_text:
                    continue
                # 信源域名聚合（与报告页信源排行同源：normalize_domain 合并子域）
                for src in database.jloads(r.sources, []) or []:
                    d = sources_mod.normalize_domain(src.get("domain") or "")
                    if d:
                        domain_counts[d] = domain_counts.get(d, 0) + 1
                st = question_stats.setdefault(r.question_text or "", {"answered": 0, "mentioned": 0})
                st["answered"] += 1
                if r.is_mentioned:
                    st["mentioned"] += 1
        snaps = (s.query(database.ScoreSnapshot)
                 .filter(database.ScoreSnapshot.brand_id == brand_id)
                 .order_by(database.ScoreSnapshot.id.desc()).limit(limit_rounds).all())
        latest_scores = [x.score for x in reversed(snaps)]

        # 竞品动向：最近的汇总深度分析（round_id=NULL）
        comp_row = (s.query(database.CompetitorAnalysis)
                    .filter(database.CompetitorAnalysis.round_id.is_(None),
                            database.CompetitorAnalysis.brand_id == brand_id,
                            database.CompetitorAnalysis.status == "done").first())
        comp_data = (database.jloads(comp_row.data, {}) or {}) if comp_row else {}
        # 最新内容优化建议（done）
        opt_row = (s.query(database.OptimizationRecord)
                   .filter(database.OptimizationRecord.brand_id == brand_id,
                           database.OptimizationRecord.status == "done")
                   .order_by(database.OptimizationRecord.id.desc()).first())

    deployed = deployed_domains(brand_id)
    top_domains = sorted(domain_counts.items(), key=lambda kv: -kv[1])[:TOP_DOMAINS]
    missing_domains = [{"domain": d, "count": c} for d, c in top_domains if d not in deployed]
    weak_questions = []
    for q, st in question_stats.items():
        if st["answered"] < WEAK_QUESTION_MIN_ANSWERS:
            continue
        weak_questions.append({
            "question": q,
            "answered": st["answered"],
            "mentioned": st["mentioned"],
            "mention_rate": round(st["mentioned"] / st["answered"], 3),
        })
    weak_questions.sort(key=lambda x: (x["mention_rate"], -x["answered"]))
    weak_questions = weak_questions[:WEAK_QUESTION_TOP]

    return {
        "brand": brand,
        "rounds_used": len(rids),
        "avg_score": (round(sum(latest_scores) / len(latest_scores), 1)
                      if latest_scores else None),
        "latest_score": (latest_scores[-1] if latest_scores else None),
        "top_domains": [{"domain": d, "count": c} for d, c in top_domains],
        "missing_domains": missing_domains,
        "deployed_domains": sorted(deployed),
        "weak_questions": weak_questions,
        "competitors": [
            {"name": c.get("name"), "mentioned": c.get("mentioned"),
             "summary": (c.get("summary") or "")[:120]}
            for c in (comp_data.get("competitors") or [])[:5]
        ],
        "competitor_advice": (comp_data.get("advice") or [])[:3],
        "optimization_suggestions": [
            {"title": x.get("title"), "detail": (x.get("detail") or "")[:120]}
            for x in (database.jloads(opt_row.suggestions, []) or [])[:5]
        ] if opt_row else [],
    }


# ---------------- 创作简报 ----------------

def build_brief(brand_id: int) -> dict:
    """创作简报：LLM 提炼缺口快照；无钥匙/失败降级为规则简报（mode=rule）。"""
    snap = build_gap_snapshot(brand_id)
    if llm_client.is_configured():
        try:
            return _llm_brief(snap)
        except llm_client.AnalysisError:
            pass  # 降级为规则简报，不阻断闭环
    return _rule_brief(snap)


def _llm_brief(snap: dict) -> dict:
    brand = snap["brand"]
    self_name = brand.get("brand_name") or "我方品牌"
    lines = [
        f"品牌：{self_name}（产品：{brand.get('product_name') or '无'}）；"
        f"简介：{(brand.get('brand_description') or '无')[:200]}",
        f"最近 {snap['rounds_used']} 轮监测：最新评分 {snap['latest_score']}，平均 {snap['avg_score']}。",
        "",
        "【AI 引用的信源域名 Top】" + ("、".join(
            f"{d['domain']}({d['count']}次)" for d in snap["top_domains"]) or "暂无"),
        "【我方尚未布点的信源域名】" + ("、".join(
            d["domain"] for d in snap["missing_domains"]) or "暂无（都已布点）"),
        "【我方已布点域名】" + ("、".join(snap["deployed_domains"]) or "暂无"),
        "",
        "【提及率最低的问题（短板）】",
    ]
    for w in snap["weak_questions"]:
        lines.append(f"- {w['question']}（{w['mentioned']}/{w['answered']} 轮提到，"
                     f"提及率 {int(w['mention_rate'] * 100)}%）")
    if snap["competitors"]:
        lines.append("")
        lines.append("【竞品动向】")
        for c in snap["competitors"]:
            lines.append(f"- {c['name']}：{'被提及' if c['mentioned'] else '未被提及'}"
                         f"（{(c['summary'] or '')[:80]}）")
    if snap["optimization_suggestions"]:
        lines.append("")
        lines.append("【既有优化建议】")
        for o in snap["optimization_suggestions"]:
            lines.append(f"- {o['title']}：{o['detail']}")

    lines += [
        "",
        "请基于以上缺口，产出一份「内容创作简报」：规划 3 篇最值得写的文章主题，",
        "优先补 AI 引用多但我方没布点的信源类型，以及提及率低的问题方向。",
        "只输出一个 JSON 对象，不要任何解释：",
        '{"summary": "一句话策略", "topics": [{"title": "文章标题方向(8~25字)",',
        '  "angle": "切入角度(30字内)", "distillation_words": ["必须自然融入正文的关键词3~6个"],',
        '  "target_domains": ["适合投稿/分发的信源域名"]}]}',
    ]
    raw = llm_client.chat("\n".join(lines), temperature=0.3, timeout=120, purpose="create")
    obj = _extract_json(raw)
    if not isinstance(obj, dict) or not (obj.get("topics") or []):
        raise llm_client.AnalysisError("分析结果缺少主题清单，请稍后再试")
    topics = []
    for t in obj["topics"][:5]:
        if not isinstance(t, dict):
            continue
        title = str(t.get("title") or "").strip()
        if not title:
            continue
        topics.append({
            "title": title,
            "angle": str(t.get("angle") or "").strip(),
            "distillation_words": [str(w).strip() for w in (t.get("distillation_words") or [])
                                   if str(w).strip()][:6],
            "target_domains": [str(d).strip() for d in (t.get("target_domains") or [])
                               if str(d).strip()][:4],
        })
    if not topics:
        raise llm_client.AnalysisError("分析结果缺少主题清单，请稍后再试")
    return {"mode": "llm", "summary": str(obj.get("summary") or "").strip(),
            "topics": topics, "snapshot": _snapshot_for_archive(snap)}


def _rule_brief(snap: dict) -> dict:
    """规则法简报（无钥匙/LLM 失败时兜底）：短板问题直转选题。"""
    brand = snap["brand"]
    self_name = brand.get("brand_name") or "我方品牌"
    product = brand.get("product_name") or self_name
    topics = []
    for w in snap["weak_questions"][:3]:
        topics.append({
            "title": f"{w['question']}",
            "angle": f"AI 提及率仅 {int(w['mention_rate'] * 100)}%，"
                     f"写一篇客观详实的内容补足这一问题的信息覆盖",
            "distillation_words": list(dict.fromkeys(
                [self_name, product] + [w for w in [brand.get("product_name") or ""] if w]))[:4],
            "target_domains": [d["domain"] for d in snap["missing_domains"][:2]],
        })
    if not topics and snap["missing_domains"]:
        d = snap["missing_domains"][0]["domain"]
        topics.append({
            "title": f"{self_name}的应用场景与产品能力详解",
            "angle": f"AI 常引用 {d} 但我方在该类站点尚无内容，优先补齐",
            "distillation_words": [self_name, product],
            "target_domains": [d],
        })
    if not topics:
        topics.append({
            "title": f"{self_name}的产品优势与典型应用",
            "angle": "暂无监测数据，先铺一篇品牌基础内容",
            "distillation_words": [self_name, product],
            "target_domains": [],
        })
    return {
        "mode": "rule",
        "summary": "规则简报（无分析钥匙或调用失败）：按提及率最低的问题直转选题，"
                   "填好分析模型钥匙可获得更智能的选题规划。",
        "topics": topics,
        "snapshot": _snapshot_for_archive(snap),
    }


def _snapshot_for_archive(snap: dict) -> dict:
    """简报留档的快照精简版（不存品牌全档案，控制体积）。"""
    return {
        "rounds_used": snap["rounds_used"],
        "latest_score": snap["latest_score"],
        "avg_score": snap["avg_score"],
        "missing_domains": [d["domain"] for d in snap["missing_domains"][:8]],
        "weak_questions": [w["question"] for w in snap["weak_questions"][:5]],
    }


# ---------------- 生成稿件 ----------------

def generate_draft(brand_id: int, brief: dict = None, user_instruction: str = "",
                   source_round_id: int = None) -> int:
    """按简报生成文章并落库为 draft（人工审阅后才发布）。返回稿件 id。"""
    if not llm_client.is_configured():
        raise EngineError("生成文章需要分析模型的钥匙（API Key），请先到设置页填写")
    brief = brief or build_brief(brand_id)
    topics = brief.get("topics") or []
    if not topics:
        raise EngineError("创作简报里没有可用主题，请先生成简报")
    brand = database.get_brand(brand_id)
    self_name = brand.get("brand_name") or "我方品牌"

    topic_lines = []
    for i, t in enumerate(topics, 1):
        words = "、".join(t.get("distillation_words") or [])
        domains = "、".join(t.get("target_domains") or [])
        topic_lines.append(f"{i}. {t.get('title')}｜角度：{t.get('angle') or '无'}"
                           f"｜需自然融入的关键词：{words or '无'}｜目标信源：{domains or '不限'}")
    user_instruction = (user_instruction or "").strip()

    prompt = "\n".join([
        f"你是「{self_name}」的内容创作者。请根据创作简报写一篇可公开发布的中文文章。",
        "",
        "【品牌档案】",
        f"- 品牌名：{self_name}；产品：{brand.get('product_name') or '无'}",
        f"- 简介：{(brand.get('brand_description') or '无')[:400]}",
        "",
        "【创作简报】" + (f"（策略：{brief.get('summary') or '无'}）" if brief.get("summary") else ""),
        "\n".join(topic_lines),
        "",
    ] + ([f"【用户指令】\n{user_instruction}\n"] if user_instruction else []) + [
        "写作要求：",
        "1. 只输出一个 JSON 对象，不要任何解释或 markdown 包裹：",
        '   {"title": "文章标题(8~30字)", "body": "Markdown 正文",',
        '    "summary": "摘要(150字内，客观概括全文)", "tags": "标签,逗号分隔,3~5个"}',
        "2. 正文 800~1500 字，用 ## 作为章节标题分层，信息详实、口径客观，",
        "   像第三方介绍一样自然提及品牌与产品，简报里的关键词自然融入，不要堆砌；",
        "3. 正文里出现的引号一律用中文引号（如“示例”），不要用英文双引号，",
        '   否则 JSON 会解析失败；正文内不要出现 JSON 结构或转义符；',
        "4. 不要夸大功效、不承诺效果、不编造具体数据与第三方评价。",
    ])

    raw = llm_client.chat(prompt, temperature=0.5, timeout=180, purpose="create")
    title, body, summary, tags = "", "", "", ""
    try:
        obj = _extract_json(raw)
        if isinstance(obj, dict):
            title = str(obj.get("title") or "").strip()
            body = str(obj.get("body") or "").strip()
            summary = str(obj.get("summary") or "").strip()
            tags = str(obj.get("tags") or "").strip()
    except llm_client.AnalysisError:
        pass
    if not body:
        # JSON 解析失败兜底：把整段回复当正文（剥掉可能的包裹）
        body = re.sub(r"^```[a-zA-Z]*\s*", "", raw.strip())
        body = re.sub(r"\s*```$", "", body).strip()
        title = title or (topics[0].get("title") if topics else f"{self_name}品牌内容")
    if not title:
        title = topics[0].get("title") if topics else f"{self_name}品牌内容"
    summary = summary[:SUMMARY_MAX_CHARS]

    with database.session_scope() as s:
        row = database.DistributionDraft(
            brand_id=brand_id, title=title, body_md=body,
            summary=summary, tags=tags,
            brief_json=database.jdumps(brief), source_round_id=source_round_id,
            status="draft", updated_at=datetime.now())
        s.add(row)
        s.flush()
        return row.id


# ---------------- 官网直发 ----------------

def publish_official(draft_id: int, brand_id: int) -> dict:
    """把稿件发布到自有官网（同步调用；站点接口在分发页配置，无预置站点）。"""
    cfg = get_official_config()
    if not cfg["base_url"]:
        raise EngineError("还没配置官网接口地址，请在本页「官网发布配置」里填写")
    if not cfg["token"]:
        raise EngineError("还没配置官网接口 Token，请在本页「官网发布配置」里填写")
    with database.session_scope() as s:
        row = s.get(database.DistributionDraft, draft_id)
        if not row:
            raise EngineError("这篇稿件找不到啦，可能已被删除")
        if (row.brand_id or 1) != brand_id:
            raise EngineError("这篇稿件属于其他品牌，请切换品牌后操作")
        if not (row.title or "").strip() or not (row.body_md or "").strip():
            raise EngineError("标题和正文都不能为空，请先补全再发布")
        brand = database.get_brand(brand_id)
        payload = {
            "title": row.title.strip(),
            "content": row.body_md,
            "category": cfg["category"],
            "author": cfg["author"] or (brand.get("brand_name") or ""),
            "summary": (row.summary or "")[:SUMMARY_MAX_CHARS],
            "tags": (row.tags or "").strip(),
        }
        row.status = "publishing"
        row.updated_at = datetime.now()
    url = cfg["base_url"] + cfg["publish_path"]
    try:
        resp = requests_lib.post(url, json=payload, timeout=30,
                                 headers={"X-Api-Token": cfg["token"],
                                          "Content-Type": "application/json",
                                          "Accept": "application/json",
                                          "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
    except requests_lib.exceptions.RequestException as e:
        _mark_publish_failed(draft_id, f"连不上官网接口：{e}")
        raise EngineError("连不上官网接口，请检查网络后重试")
    if resp.status_code != 200:
        detail = (resp.text or "")[:200]
        _mark_publish_failed(draft_id, f"官网接口返回 HTTP {resp.status_code}：{detail}")
        raise EngineError(f"官网接口返回 {resp.status_code}：{detail[:120] or '无响应体'}"
                          "——请核对 Token 是否为站点校验的那个值、接口路径是否完整、"
                          "以及站点防火墙是否放行")
    data = {}
    try:
        data = resp.json() or {}
    except Exception:
        data = {}
    if isinstance(data, dict) and data.get("code") not in (None, 0, "0"):
        msg = str(data.get("message") or data.get("error") or "官网拒绝了这次发布")[:200]
        _mark_publish_failed(draft_id, msg)
        raise EngineError(f"官网发布失败：{msg}")
    published_url = ""
    if isinstance(data, dict):
        inner = data.get("data") if isinstance(data.get("data"), dict) else {}
        published_url = str(data.get("url") or inner.get("url")
                            or data.get("slug") or inner.get("slug") or "")
    with database.session_scope() as s:
        row = s.get(database.DistributionDraft, draft_id)
        if row:
            row.status = "published"
            row.published_url = published_url
            row.published_at = datetime.now()
            row.error_msg = ""
            row.updated_at = datetime.now()
    return {"url": published_url, "status": "published"}


def _mark_publish_failed(draft_id: int, message: str):
    with database.session_scope() as s:
        row = s.get(database.DistributionDraft, draft_id)
        if row:
            row.status = "failed"
            row.error_msg = message[:500]
            row.updated_at = datetime.now()


# ---------------- JSON 提取（与竞品分析同一口径） ----------------

def _extract_json(text: str):
    from geo.analyzers.competitor_analysis import _extract_json as _impl
    return _impl(text)
