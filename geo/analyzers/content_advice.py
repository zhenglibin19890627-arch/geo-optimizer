"""GEO 内容优化建议生成：规则法 GEO 友好度评分 + 大模型具体建议。"""

import json
import re

from geo.analyzers import llm_client
from geo.models import db as database


def _extract_json_list(text: str) -> list:
    """从模型回答里稳妥地取出 JSON 数组。"""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
    except Exception:
        pass
    m = re.search(r"\[.*\]", text, re.S)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []


def geo_score(content: str, brand_name: str, keywords: list,
              allow_links: bool = True) -> dict:
    """规则法 GEO 友好度评分（0-100，参考值）+ 分项说明。

    allow_links=False：目标平台不允许外部链接（如搜狐/头条），外链项换成
    「无外链依赖」口径——没有外链得 10 分，有外链 0 分并提示发布前移除。
    """
    score = 0
    parts = []
    if not content:
        return {"score": 0, "breakdown": [{"title": "没有可分析的内容", "score": 0}]}

    n = len(content)
    if n >= 500:
        score += 15
        parts.append({"title": "内容长度足够（500 字以上）", "score": 15})
    if n >= 2000:
        score += 15
        parts.append({"title": "内容比较充实（2000 字以上）", "score": 15})

    if brand_name and content.count(brand_name) >= 2:
        score += 10
        parts.append({"title": "品牌名出现 2 次以上", "score": 10})

    kw_hits = 0
    for kw in keywords:
        if kw and kw in content:
            kw_hits += 1
    kw_score = min(kw_hits * 5, 15)
    if kw_score:
        score += kw_score
        parts.append({"title": f"命中 {kw_hits} 个关键词", "score": kw_score})

    if re.search(r"<h[1-3]", content, re.I) or content.count("\n\n") >= 5:
        score += 10
        parts.append({"title": "有清晰的小标题/分段结构", "score": 10})

    if allow_links:
        if len(re.findall(r"https?://", content)) >= 1:
            score += 10
            parts.append({"title": "有外部引用链接", "score": 10})
    elif re.search(r"https?://", content):
        parts.append({"title": "含外部链接：目标平台不允许外链，发布前需移除或改为文字表述",
                      "score": 0})
    else:
        score += 10
        parts.append({"title": "无外部链接依赖（符合目标平台规则）", "score": 10})

    if re.search(r"(公司|企业|电话|邮箱|邮箱|地址|关于我们|联系方式)", content):
        score += 10
        parts.append({"title": "有公司/联系方式等信息", "score": 10})

    if re.search(r"(目录|摘要|引言|结论|总结|FAQ|常见问题)", content):
        score += 5
        parts.append({"title": "有结构化信息（目录/摘要/结论等）", "score": 5})

    total = min(score, 100)
    return {"score": total, "breakdown": parts}


def _extract_json_object(text: str) -> dict:
    """从模型回答里稳妥地取出 JSON 对象（兼容 ``` 包裹与夹带说明文字）。"""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def apply_geo_optimization(title: str, body: str, brand: dict, keywords: list,
                           allow_links: bool = True) -> dict:
    """按 GEO 优化维度重写整篇稿件（打完分之后的针对性优化）。

    只重写、不落库不发布：结果回填编辑器由人工确认后手动保存，保持审阅制。
    allow_links=False：目标平台不允许外链，改写时不得出现任何链接。
    返回 {title, body_md, changes: [{title, detail}]}；模型无有效输出抛 AnalysisError。
    """
    brand_name = brand.get("brand_name") or "我的品牌"
    product = brand.get("product_name") or ""
    desc = brand.get("brand_description") or ""
    kw_text = "、".join([k for k in keywords if k]) or "（未填写）"
    if allow_links:
        link_rule = ("4. 保留原文已有的外部链接；如果原文完全没有外部引用，可补充 1-2 条"
                     "真实存在的权威站点或百科词条链接，不得编造不存在的页面；\n")
    else:
        link_rule = ("4. 这篇稿件将发布到不允许外部链接的平台：正文里不要出现任何"
                     " http(s) 链接，原文中的链接改为文字表述（如「来源：某某百科」），"
                     "也不要虚构来源；\n")

    prompt = (
        "你是一名中文 GEO（生成式引擎优化）顾问。请把下面这篇品牌稿件改写得更容易被"
        "豆包、DeepSeek、通义千问等 AI 引擎在回答用户问题时引用和推荐：\n"
        "1. 保持原文事实与原意，不编造原文没有的数据、案例、承诺和联系方式；\n"
        "2. 增加清晰的小标题分段，并补充「常见问题（FAQ）」和「结论/总结」小节；\n"
        f"3. 让品牌名「{brand_name}」自然出现至少 2 次，关键词（{kw_text}）自然融入正文；\n"
        f"{link_rule}"
        "5. 正文尽量充实（目标 2000 字以上），语言保持中文，输出 Markdown 格式；\n"
        "6. 标题也一并优化：具体、带信息量，可含品牌名或核心关键词，控制在 30 个字以内。\n"
        f"品牌名：{brand_name}\n产品：{product}\n品牌一句话介绍：{desc}\n"
        f"原标题：\n{title[:200]}\n原文：\n\"\"\"\n{body[:40000]}\n\"\"\"\n"
        "只返回一个 JSON 对象，不要任何解释，格式："
        '{"title": "优化后的标题", "body_md": "优化后的完整正文（Markdown）", '
        '"changes": [{"title": "改了什么（一句话）", "detail": "具体说明（1-2 句）"}]}'
    )
    # 整篇重写输出量大（2000 字+），与生成稿件同口径用 180 秒；
    # 默认 60 秒会把慢响应误判为超时并触发重试，越等越久
    text = llm_client.chat(prompt, temperature=0.4, timeout=180, purpose="create")
    data = _extract_json_object(text)
    new_title = str(data.get("title") or "").strip()
    new_body = str(data.get("body_md") or "").strip()
    if not new_title or not new_body:
        raise llm_client.AnalysisError("优化失败（模型没有返回有效改写），请稍后再试")
    # 平台标题上限 30 字，超长静默截断
    if len(new_title) > 30:
        new_title = new_title[:30]
    if len(new_body) > 100000:
        new_body = new_body[:100000]
    changes = []
    for it in (data.get("changes") or [])[:10]:
        if isinstance(it, dict) and str(it.get("title") or "").strip():
            changes.append({"title": str(it["title"]).strip(),
                            "detail": str(it.get("detail") or "").strip()})
    return {"title": new_title, "body_md": new_body, "changes": changes}


def generate_suggestions(content: str, brand: dict, keywords: list) -> list:
    """用分析模型生成优化建议 [{title, detail, priority}]。"""
    brand_name = brand.get("brand_name") or "我的品牌"
    product = brand.get("product_name") or ""
    desc = brand.get("brand_description") or ""
    kw_text = "、".join([k for k in keywords if k]) or "（未填写）"

    excerpt = content[:40000]
    prompt = (
        "你是一名中文 GEO（生成式引擎优化）顾问，帮助品牌的内容更容易被豆包、DeepSeek、"
        "Kimi、通义千问等 AI 引擎在回答用户问题时引用和推荐。\n"
        f"品牌名：{brand_name}\n产品：{product}\n品牌一句话介绍：{desc}\n"
        f"希望覆盖的关键词：{kw_text}\n"
        "下面是用户提交的网页/文章内容：\n"
        f"\"\"\"\n{excerpt}\n\"\"\"\n"
        "请从 GEO 角度给出 5-8 条具体、可执行的优化建议，例如：结构化信息（小标题、FAQ）、"
        "权威来源引用、品牌信息呈现、关键词自然布局、与其他内容的互链等。\n"
        "只返回 JSON 数组，不要任何解释，格式："
        '[{"title": "建议标题（一句话）", "detail": "具体怎么做（1-3 句大白话）", "priority": "高|中|低"}]'
    )
    text = llm_client.chat(prompt, temperature=0.5, purpose="create")
    items = _extract_json_list(text)
    result = []
    for it in items[:10]:
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or "").strip()
        detail = str(it.get("detail") or "").strip()
        priority = str(it.get("priority") or "中").strip()
        if priority not in ("高", "中", "低"):
            priority = "中"
        if title:
            result.append({"title": title, "detail": detail, "priority": priority})
    if not result:
        raise llm_client.AnalysisError("优化建议生成失败（模型没有返回有效内容），请稍后再试")
    return result
