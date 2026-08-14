"""AI 引擎适配器注册表：新增引擎只需加一个文件并在这里登记。"""

from geo.engines.base import EngineAdapter
from geo.engines.deepseek import DeepSeekAdapter
from geo.engines.doubao import DoubaoAdapter
from geo.engines.kimi import KimiAdapter
from geo.engines.manual import ManualAdapter
from geo.engines.opencode import OpenCodeAdapter
from geo.engines.qwen import QwenAdapter
from geo.engines.yuanbao import YuanbaoAdapter

_REGISTRY = {
    DeepSeekAdapter.code: DeepSeekAdapter,
    KimiAdapter.code: KimiAdapter,
    DoubaoAdapter.code: DoubaoAdapter,
    QwenAdapter.code: QwenAdapter,
    YuanbaoAdapter.code: YuanbaoAdapter,
    OpenCodeAdapter.code: OpenCodeAdapter,
    ManualAdapter.code: ManualAdapter,
}

# 自动监测引擎（手动粘贴不在其列）
AUTO_CODES = ["deepseek", "kimi", "doubao", "qwen", "yuanbao", "opencode"]


def get_adapter(code: str) -> EngineAdapter:
    cls = _REGISTRY.get(code)
    if cls is None:
        raise EngineError_NotFound(f"没找到这家引擎：{code}")
    return cls()


def get_web_adapter(code: str) -> EngineAdapter:
    """联网提问档适配器：豆包、DeepSeek 走 Responses API 新适配器（独立封装），
    其余联网引擎复用既有适配器的 web_search 能力；不支持联网（如 opencode）抛错。"""
    if code == "doubao":
        from geo.engines.doubao_responses import DoubaoResponsesAdapter
        return DoubaoResponsesAdapter()
    if code == "deepseek":
        from geo.engines.deepseek_responses import DeepSeekResponsesAdapter
        return DeepSeekResponsesAdapter()
    adapter = get_adapter(code)
    if not adapter.supports_web_search:
        raise EngineError_NotFound(f"{code} 暂不支持联网提问")
    return adapter


class EngineError_NotFound(Exception):
    pass


def adapter_meta(code: str) -> dict:
    """引擎元信息：显示名、口径说明、档位选项、开关、钥匙状态。"""
    from geo.engines.base import EngineAdapter
    adapter = get_adapter(code)
    cfg = adapter.cfg if hasattr(adapter, "cfg") else {}
    model_options = []
    for opt in cfg.get("model_options") or []:
        if isinstance(opt, dict):
            model_options.append({"name": opt.get("name", ""), "desc": opt.get("desc", "")})
    return {
        "engine": code,
        "display_name": adapter.display_name,
        "note": adapter.note or (cfg.get("note") or ""),
        "configured": adapter.is_configured(),
        "enabled": adapter.is_enabled(),
        "model": adapter.get_model(),
        "web_model": adapter.get_web_model(),
        "model_options": model_options,
        "supports_web_search": bool(adapter.supports_web_search),
    }
