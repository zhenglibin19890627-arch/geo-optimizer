"""关键词扩展问法：用分析模型生成“用户自然提问”（GEO 品牌曝光测试问题）。

按用户提供的《GEO 品牌曝光测试问题生成专家》系统提示词实现：
- 问题不得包含目标品牌名（否则测试无效），通过品类/场景/关键词自然引导；
- 覆盖 T1~T10 意图类型（至少 5 种）+ 易/中/难难度梯度；
- 输出结构化 JSON（含 expected_trigger 等元数据），本模块提取 question 供问题库使用。
"""

import json
import re
from datetime import datetime

from geo.analyzers import llm_client

# 意图类型表（用户提示词原文）
_INTENT_TABLE = """| 编号 | 意图类型 | 说明 | 示例句式 |
|------|----------|------|----------|
| T1 | 推荐求助型 | 直接让 AI 推荐产品/方案 | "有没有适合敏感肌的防晒霜推荐？" |
| T2 | 场景痛点型 | 描述具体场景或痛点，寻求解决方案 | "宝宝6个月要去海边，怎么防晒比较好？" |
| T3 | 对比选型型 | 让 AI 对比多个选项或品类 | "物理防晒和化学防晒哪个更适合孕妇？" |
| T4 | 知识科普型 | 围绕品类/领域提问，看 AI 是否引用品牌作为案例 | "SPF 和 PA 到底是什么意思？选防晒霜看什么指标？" |
| T5 | 清单/榜单型 | 要求列出 Top N、排行、合集 | "2026年口碑最好的5款儿童防晒霜有哪些？" |
| T6 | 问题解决型 | 先描述问题，再问怎么办 | "涂了防晒还是晒黑了，是不是产品没选对？" |
| T7 | 人群/预算限定型 | 限定特定人群、预算或使用条件 | "学生党预算100以内，有什么好用的防晒？" |
| T8 | 替代/升级型 | 已有某方案，想换或升级 | "现在用的XX不太行，有没有更温和的替代？" |
| T9 | 决策确认型 | 已有意向，请 AI 确认/评价 | "敏感肌用含烟酰胺的防晒到底行不行？" |
| T10 | 行业/趋势型 | 围绕行业趋势或新技术提问 | "今年防晒赛道有什么新成分或新技术值得关注？" |"""


def _extract_questions(text: str):
    """解析模型输出，返回 (questions: list[str], meta: dict)。

    支持：
    - 新格式：{brand, direction, questions: [{id, type, difficulty, question,
      expected_trigger, test_platform_suggestion}], scoring_guide}
    - 旧格式：字符串数组 / 行尾带问号的行
    """
    meta = {}
    if text:
        t = text.strip()
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t).strip()
        # 新格式：整体 JSON 对象
        try:
            data = json.loads(t)
            if isinstance(data, dict):
                meta = {k: v for k, v in data.items() if k != "questions"}
                qs = data.get("questions") or []
                if isinstance(qs, list):
                    out = []
                    for item in qs:
                        if isinstance(item, dict) and str(item.get("question") or "").strip():
                            out.append(str(item["question"]).strip())
                        elif isinstance(item, str) and item.strip():
                            out.append(item.strip())
                    if out:
                        return out, meta
        except Exception:
            pass
        # 旧格式：字符串数组
        m = re.search(r"\[.*\]", t, re.S)
        if m:
            try:
                data = json.loads(m.group(0))
                if isinstance(data, list):
                    out = [str(x).strip() for x in data if str(x).strip()]
                    if out:
                        return out, meta
            except Exception:
                pass
    # 最后的兜底：逐行取“？”结尾的行
    lines = [ln.strip(" -0123456789.\t") for ln in (text or "").splitlines()
             if "?" in ln or "？" in ln]
    return [ln for ln in lines if len(ln) >= 4], meta


def expand_questions(keywords: list, count: int = 10, direction: str = None,
                     brand: dict = None) -> list:
    """按《GEO 品牌曝光测试问题生成专家》生成 count 条自然提问（需分析模型钥匙）。

    brand（可选）：品牌档案 dict。传入时问题围绕品牌卖点/人群/差异化设计，
    但问题本身不得出现品牌名（通过品类、场景、关键词自然引导）。
    返回问题字符串列表（question 字段）；无品牌时回落到通用生成模板。
    """
    keywords = [str(k).strip() for k in keywords if str(k).strip()]
    if not keywords:
        raise llm_client.AnalysisError("请先填写至少一个关键词，我才能帮你扩展问法")
    count = max(1, min(int(count or 10), 30))
    direction = (direction or "").strip()
    if len(direction) > 200:
        raise llm_client.AnalysisError("优化方向太长了，请控制在 200 个字以内")

    brand = brand if isinstance(brand, dict) else {}
    brand_name = str(brand.get("brand_name") or "").strip()

    if brand_name:
        aliases = [str(a or "").strip() for a in (brand.get("brand_aliases") or [])
                   if str(a or "").strip()]
        product = str(brand.get("product_name") or "").strip()
        desc = str(brand.get("brand_description") or "").strip()
        if len(desc) > 300:
            desc = desc[:300] + "……"
        competitors = [str(c).strip() for c in (brand.get("competitors") or [])
                       if str(c).strip()]
        intro_parts = []
        if product:
            intro_parts.append(f"核心产品/服务：{product}")
        if desc:
            intro_parts.append(f"品牌定位与卖点：{desc}")
        intro = "；".join(intro_parts) or "（暂无简介，结合品牌名与关键词推断）"

        # 品牌线索：问题中必须嵌入、但不得出现品牌名的标签（地域/业务/卖点/目标客户），
        # 让 AI 在回答时高概率命中目标品牌，而不是泛泛罗列整个行业。
        clue_parts = []
        if product:
            clue_parts.append(f"业务：{product}")
        if desc:
            clue_parts.append(f"卖点/场景：{desc}")
        if competitors:
            clue_parts.append(f"同类竞争：{'、'.join(competitors)}")
        clues = "；".join(clue_parts) or "（暂无简介，结合品牌名与关键词推断）"

        prompt = f"""# Role: GEO 品牌曝光测试问题生成专家

## Background
你是一位精通 GEO（Generative Engine Optimization，生成式引擎优化）的测试策略专家。你的任务是：根据用户提供的【品牌信息】、【优化方向】和【核心关键词】，生成一组高质量的“用户自然提问”，用于在 AI 搜索引擎（如 ChatGPT、Perplexity、Google AI Overview、Bing Copilot、Kimi、豆包等）中测试目标品牌是否能被自然推荐和提及。

## Input
- 品牌名称：{brand_name}（严禁出现在生成的问题中）
- 品牌简介：{intro}
- 品牌线索（问题中必须嵌入的标签，用于引导 AI 推荐目标品牌）：{clues}
- 优化方向：{direction or "（未指定，结合品牌定位自行把握）"}
- 核心关键词：{'、'.join(keywords)}
- 目标用户画像（可选）：（未提供）
- 竞品品牌（可选）：{'、'.join(competitors) if competitors else "（未提供）"}

## Task
一次性生成 **{count} 个测试问题**，用于模拟真实用户在 AI 搜索引擎中的提问，验证 AI 回答中是否会自然推荐/提及目标品牌（{brand_name}）。

## 问题生成规则

### 一、意图类型覆盖（{count} 题必须覆盖以下至少 5 种）
{_INTENT_TABLE}

### 二、提问风格要求
1. **自然口语化**：模拟真实用户语气，避免书面化、学术化措辞。
2. **禁止直接出现品牌名**：问题中不得包含目标品牌名称及其别称（否则测试无效），但必须嵌入【品牌线索】中的标签（地域、业务、卖点、目标客户等），引导 AI 在回答时自然推荐该品牌。
3. **长度多样**：短问句（5-10字）和长描述（30-50字）混合。
4. **口语词/语气词**：适当加入“求推荐”“有没有”“到底”“真的好难选”等自然表达。
5. **搜索式 & 对话式混合**：部分像搜索引擎关键词组合，部分像跟朋友聊天。

### 三、与品牌信息的关联（硬性要求）
- 每条问题必须嵌入至少 1 条【品牌线索】中的标签（最好是地域 + 业务/卖点组合），使 AI 在回答时有高概率推荐目标品牌；禁止生成泛泛罗列整个行业、与品牌线索无关的问题。
- 参考品牌简介中的关键词、使用场景、人群标签来构造问题上下文，问题要能“精准命中”目标品牌。
- 反面示例（太泛、不合格）：“有哪些学校改造公司？”；正面示例（嵌入地域+业务+卖点，合格）：“龙泉市中小学的智慧教室改造，找本地做弱电系统集成的公司，施工和售后能一起负责吗？”
- 若提供了竞品，可设计 1-2 个“竞品对比”问题，观察 AI 是否同时提及目标品牌。
- 每条问题的 expected_trigger 必须写明：预期命中目标品牌的哪些线索标签（如“龙泉本地 + 系统集成 + 售后”），AI 据哪些信号可能推荐该品牌。

### 四、难度梯度
- **易（约 {max(1, count // 3)} 题）**：品类词 + 推荐，AI 大概率直接列出品牌。
- **中（约 {max(2, count // 3 + 1)} 题）**：场景/人群/痛点限定，AI 需推理后推荐。
- **难（约 {max(1, count // 3)} 题）**：间接提问、知识科普、趋势讨论，品牌作为案例/论据出现即为成功。

## Output Format
严格按以下 JSON 格式输出，不要附加多余解释：
{{
  "brand": "{brand_name}",
  "direction": "{direction or '未指定'}",
  "questions": [
    {{
      "id": 1,
      "type": "T1-推荐求助型",
      "difficulty": "易",
      "question": "具体的测试问题",
      "expected_trigger": "预期命中的品牌线索标签（如：龙泉本地+系统集成+售后），AI 据哪些信号可能推荐该品牌",
      "test_platform_suggestion": "建议优先测试的平台（如 Perplexity / ChatGPT / Kimi）"
    }}
  ],
  "scoring_guide": {{
    "pass": "AI 回答中自然提及品牌名，且描述与品牌定位一致",
    "partial": "AI 提及品牌但描述有偏差，或仅在列表末尾出现",
    "fail": "AI 回答中完全未提及该品牌"
  }}
}}
只输出上述 JSON 对象本身，不要输出 JSON 之外的内容。"""
    else:
        prompt = (
            "你是中文问答策划专家。下面是一些关键词（用户希望在 AI 回答里被提及）：\n"
            f"{'、'.join(keywords)}\n"
            "请站在普通用户的角度，生成自然、口语化的中文问题（用户真的会去问豆包、DeepSeek、"
            "Kimi 这类 AI 的问题），问题里要包含关键词或明显相关的内容，"
            "覆盖：选购咨询、对比评测、口碑评价、使用教程、价格优惠、避坑建议等角度，避免重复。\n"
        )
        if direction:
            prompt += (
                f"本次优化的目的和方向：{direction}。生成的问题必须围绕这个目的/方向展开，"
                "避免与优化目标无关的泛泛问题。\n"
            )
        prompt += f"只返回 JSON 字符串数组（共 {count} 条），不要任何解释。"

    text = llm_client.chat(prompt, temperature=0.7)
    questions, _meta = _extract_questions(text)
    if not questions:
        raise llm_client.AnalysisError("扩展问法生成失败（模型没有返回有效内容），请稍后再试")
    return questions[:count]