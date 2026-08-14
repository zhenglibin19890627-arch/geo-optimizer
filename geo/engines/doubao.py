"""豆包（火山方舟）官方 API 适配器（常规提问档：Chat API）。

联网提问走独立的 Responses API 适配器（geo/engines/doubao_responses.py，
get_web_adapter 按 code=doubao 分发），本适配器零改动；supports_web_search
标记为 True 表示「豆包引擎整体具备联网能力」（供前端置灰/联网档参与判断）。
"""

from geo.engines.base import EngineAdapter


class DoubaoAdapter(EngineAdapter):
    code = "doubao"
    display_name = "豆包"
    supports_web_search = True  # 联网能力由 Responses 适配器提供（02d 5.1）

    def chat(self, messages, temperature=None, jitter=False, timeout=60,
             web_search=False, model=None):
        if web_search:
            from geo.engines.doubao_responses import DoubaoResponsesAdapter
            return DoubaoResponsesAdapter().chat(messages, temperature,
                                                 jitter=jitter, timeout=timeout,
                                                 web_search=True, model=model)
        return self.call_openai_compatible(messages, temperature, jitter=jitter,
                                           timeout=timeout, model=model)
