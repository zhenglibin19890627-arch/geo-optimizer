"""DeepSeek 官方 API 适配器（常规提问档）。

联网提问（2026 起官方支持）：DeepSeek 提供 Responses API 联网搜索
（https://api-docs.deepseek.com/zh-cn/guides/responses_api），联网档由
geo/engines/deepseek_responses.py 独立适配（get_web_adapter 按 code 分发），
本适配器只服务常规档；supports_web_search 置 True 使联网档纳入 DeepSeek。
"""

from geo.engines.base import EngineAdapter, EngineError


class DeepSeekAdapter(EngineAdapter):
    code = "deepseek"
    display_name = "DeepSeek"
    supports_web_search = True  # 联网能力由 Responses 适配器提供

    def chat(self, messages, temperature=None, jitter=False, timeout=60,
             web_search=False, model=None):
        if web_search:
            # 防御性拦截：联网档统一走 DeepSeekResponsesAdapter，正常流程不会走到
            raise EngineError("DeepSeek 联网提问请走 Responses API 适配器")
        return self.call_openai_compatible(messages, temperature, jitter=jitter,
                                           timeout=timeout, model=model)
