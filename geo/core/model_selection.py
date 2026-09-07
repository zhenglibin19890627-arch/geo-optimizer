"""监测模型选择与提问消息构造（纯函数，无库写、无线程）。

2026-09-04 起：模型从「档位勾选」改为「自由填写」（前端输入框手填型号，
逗号分隔可填多个），本模块只做清洗归一（去空白/去重/保序/超长截断），
不再按配置档位白名单校验——型号是否存在由执行时各引擎 API 判定
（报大白话错误）。监测中心、定时任务与执行循环（build_messages）
共用同一套口径，避免多处各自实现产生漂移。
"""

from geo.engines import base as engine_base, get_adapter, get_web_adapter

SYSTEM_PROMPT = "你是一个乐于助人的中文AI助手。请用中文客观、详细地回答用户的问题。"


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


def _clean_model_list(raw) -> list:
    """单个引擎的模型清单清洗：字符串容错、去空白、超长截断、保序去重。"""
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    picked = [str(m).strip()[:100] for m in raw if str(m).strip()]
    return list(dict.fromkeys(picked))


def filter_models(engine_codes: list, models: dict, web: bool = False) -> dict:
    """宽松清洗：把 {engine: [model,...]} 里空白/坏形状项剔除（不抛错）。

    用于定时监测：型号是手填的，没有"下架档位"概念，故原样放行——
    型号写错由执行时引擎 API 报大白话错误；某家引擎清洗后为空则不出现在
    结果里（调用方据此跳过该引擎，不发起空任务）。
    """
    result = {}
    for code in engine_codes or []:
        picked = _clean_model_list((models or {}).get(code))
        if picked:
            result[code] = picked
    return result


def normalize_models(engine_codes: list, models: dict, web: bool = False) -> dict:
    """把前端传来的 {engine: [model,...]} 归一为任务模型清单（自由填写口径）。

    - 型号由用户手填，不再按档位白名单校验；只做清洗（去空白/截断/去重保序）；
    - 某引擎清单为空（前端留空）→ 回落当前档（联网档回落联网档模型）；
    - 只认 engine_codes 内的引擎；
    - 结果恒为 {code: [model, ...]}，供任务落库与执行循环使用。
    """
    result = {}
    for code in engine_codes or []:
        try:
            adapter = get_adapter(code)
        except Exception:
            continue
        picked = _clean_model_list((models or {}).get(code))
        if not picked:
            if web:
                try:
                    picked = [get_web_adapter(code).get_web_model()]
                except Exception:
                    picked = [adapter.get_model()]
            else:
                picked = [adapter.get_model()]
        result[code] = picked
    return result
