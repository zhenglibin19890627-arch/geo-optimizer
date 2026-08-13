"""DeepSeek 官方 API 适配器。

DeepSeek 无联网能力（02d 5.1）：supports_web_search=false，联网档被自动排除；
防御性拦截 web_search=True 的调用（正常流程不会走到）。
"""

from geo.engines.base import EngineAdapter, EngineError


class DeepSeekAdapter(EngineAdapter):
    code = "deepseek"
    display_name = "DeepSeek"

    def chat(self, messages, temperature=None, jitter=False, timeout=60, web_search=False):
        if web_search:
            raise EngineError("DeepSeek 暂不支持联网提问，联网监测不包含它")
        return self.call_openai_compatible(messages, temperature, jitter=jitter, timeout=timeout)
