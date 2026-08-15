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
        intro_parts = []
        if product:
            intro_parts.append(f"核心产品/服务：{product}")
        if desc:
            intro_parts.append(f"品牌定位与卖点：{desc}")
        intro = "；".join(intro_parts) or "（暂无简介，结合品牌名与关键词推断）"

        # 品牌线索：可选素材（地域/业务/卖点/目标客户），提示词中不强制嵌入，
        # 主要靠关键词生成自然提问，线索仅在有助自然引导时使用。
        clue_parts = []
        if product:
            clue_parts.append(f"业务：{product}")
        if desc:
            clue_parts.append(f"卖点/场景：{desc}")
        clues = "；".join(clue_parts) or "（暂无简介，结合品牌名与关键词推断）"

        prompt = f"""# Role: GEO 品牌曝光测试问题生成专家

## Background
你是一位精通 GEO（Generative Engine Optimization，生成式引擎优化）的测试策略专家。你的任务是：根据用户提供的【品牌信息】、【优化方向】和【核心关键词】，生成一组高质量的“用户自然提问”，用于在 AI 搜索引擎（如 ChatGPT、Perplexity、Google AI Overview、Bing Copilot、Kimi、豆包等）中测试目标品牌是否能被自然推荐和提及。

## Input
- 品牌名称：{brand_name}（严禁出现在生成的问题中）
- 品牌简介：{intro}
- 品牌线索（可选素材，自然融入即可，不必每条都用）：{clues}
- 优化方向：{direction or "（未指定，结合品牌定位自行把握）"}
- 核心关键词：{'、'.join(keywords)}
- 目标用户画像（可选）：（未提供）

## Task
一次性生成 **{count} 个测试问题**，用于模拟真实用户在 AI 搜索引擎中的提问，验证 AI 回答中是否会自然推荐/提及目标品牌（{brand_name}）。

## 问题生成规则

### 一、提问落点（所有问题都必须满足）
每条问题都必须能引导 AI 在回答中**推荐/点名具体的企业**（最终要问出"哪家企业"）。
围绕以下角度生成（共 {count} 条，各角度都要有）：
- 求推荐：直接要企业名单（"做理化生实验室改造的企业有哪些？"）
- 求靠谱：问怎么选、怎么避坑（"实验室改造找什么样的公司靠谱？"）
- 求对比：两类方案/公司对比，要结论（"找系统集成的还是设备商？"）
- 售后找人：出了问题找谁处理（"改造完售后找谁？"）
- 口碑打听：问哪家口碑好（"本地做这个口碑好的有哪几家？"）
**禁止生成**纯知识科普、纯行业趋势类问题（这类问题 AI 回答时不会推荐企业）。

### 二、提问风格（硬性）
1. **口语化短问**：像普通人在微信里随口问的，**10-25 字为主**，最多不超过 35 字。
2. **禁止直接出现品牌名**：不得包含目标品牌名称及其别称（否则测试无效）。
3. 以【核心关键词】为骨架生成；【品牌线索】（地域/业务/卖点）可选，**{count} 条里最多 2-3 条自然融入**，其余保持纯关键词，不要每条都带。
4. 适当用"有没有""求推荐""靠谱吗""咋整""哪家好"等口语词。
5. **适度真实感**：偶尔带一个具体细节（学校/预算/老实验室等），不刻意堆砌。

### 三、与品牌信息的关联（自然融入，不强制）
- 参考品牌简介中的关键词、使用场景、人群标签来构造问题上下文，使 AI 回答时更可能推荐目标品牌。
- 每条问题的 expected_trigger 写明：预期可能触发品牌被提及的信号（线索标签或场景信号），没有就写"自然推荐"。

## Output Format
严格按以下 JSON 格式输出，不要附加多余解释：
{{
  "brand": "{brand_name}",
  "direction": "{direction or '未指定'}",
  "questions": [
    {{
      "id": 1,
      "type": "求推荐（或 求靠谱/求对比/售后找人/口碑打听）",
      "difficulty": "易",
      "question": "具体的测试问题",
      "expected_trigger": "预期可能触发品牌被提及的信号（线索或场景），无则写：自然推荐",
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