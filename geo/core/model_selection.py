"""监测模型档位选择与提问消息构造（纯函数，无库写、无线程）。

监测中心（严格校验抛错）、定时任务（宽松过滤回落）与执行循环
（build_messages）共用同一套口径，避免三处各自实现产生漂移。
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


def allowed_models(code: str, web: bool = False) -> set:
    """某引擎在当前配置下允许的模型档位集合（含当前档；联网档含联网白名单）。

    从 normalize_models 的校验逻辑抽出，供 API 校验（严格抛错）与
    定时任务读取（宽松过滤）两处复用，口径永远一致。
    """
    try:
        adapter = get_adapter(code)
    except Exception:
        return set()
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
    if web:
        # 联网档白名单：配置了 web_model_options 则只允许该子集
        # （如通义千问实时翻译模型不支持联网协议），否则回落全量档位
        web_opts = {o.get("name") for o in (meta.get("web_model_options") or [])
                    if isinstance(o, dict) and o.get("name")}
        if web_opts:
            allowed = web_opts
        try:
            wm = get_web_adapter(code).get_web_model()
        except Exception:
            wm = None
        if wm:
            allowed.add(wm)
    return allowed


def filter_models(engine_codes: list, models: dict, web: bool = False) -> dict:
    """宽松过滤：把 {engine: [model,...]} 里当前配置不允许的项剔除（不抛错）。

    用于定时监测：设置页保存的模型选择可能在之后被改档/删档，定时执行时
    按当前配置过滤；某家引擎过滤后为空则不出现在结果里（执行时回落当前档）。
    """
    result = {}
    for code in engine_codes or []:
        raw = (models or {}).get(code) or []
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            continue
        allowed = allowed_models(code, web)
        picked = [str(m).strip() for m in raw if str(m).strip() in allowed]
        picked = list(dict.fromkeys(picked))
        if picked:
            result[code] = picked
    return result


def normalize_models(engine_codes: list, models: dict, web: bool = False) -> dict:
    """把前端传来的 {engine: [model,...]} 校验归一为任务模型清单。

    - models 缺省/为空 → 每家引擎用其当前档：常规档 [adapter.get_model()]，
      联网档 [get_web_adapter(code).get_web_model()]；
    - 只认 engine_codes 内的引擎；每家的模型名必须在设置页档位列表
      （含当前档；联网档额外含联网档模型）里，否则抛大白话错误；
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
            if web:
                try:
                    picked = [get_web_adapter(code).get_web_model()]
                except Exception:
                    picked = [adapter.get_model()]
            else:
                picked = [adapter.get_model()]
        # 校验：只允许设置页档位列表里的模型（含当前档；联网档含联网档模型）
        allowed = allowed_models(code, web)
        for m in picked:
            if m not in allowed:
                raise engine_base.EngineError(
                    f"{adapter.display_name}没有「{m}」这个档位，请从档位列表里选")
        result[code] = list(dict.fromkeys(picked))
    return result
