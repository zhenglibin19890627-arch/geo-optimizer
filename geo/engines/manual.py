"""手动粘贴适配器：不调用任何接口，始终可用（备用输入通道）。"""

from geo.engines.base import EngineAdapter, EngineError


class ManualAdapter(EngineAdapter):
    code = "manual"
    display_name = "手动粘贴"

    def is_configured(self):
        return True

    def is_enabled(self):
        return True

    def chat(self, messages, temperature=None, jitter=False, timeout=60):
        raise EngineError("手动粘贴不需要调用接口：请直接在监测中心粘贴 AI 的回答即可")
