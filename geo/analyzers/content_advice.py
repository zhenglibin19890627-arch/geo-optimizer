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


def geo_score(content: str, brand_name: str, keywords: list) -> dict:
    """规则法 GEO 友好度评分（0-100，参考值）+ 分项说明。"""
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

    if len(re.findall(r"https?://", content)) >= 1:
        score += 10
        parts.append({"title": "有外部引用链接", "score": 10})

    if re.search(r"(公司|企业|电话|邮箱|邮箱|地址|关于我们|联系方式)", content):
        score += 10
        parts.append({"title": "有公司/联系方式等信息", "score": 10})

    if re.search(r"(目录|摘要|引言|结论|总结|FAQ|常见问题)", content):
        score += 5
        parts.append({"title": "有结构化信息（目录/摘要/结论等）", "score": 5})

    total = min(score, 100)
    return {"score": total, "breakdown": parts}


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
    text = llm_client.chat(prompt, temperature=0.5)
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
