"""OpenCode 订阅套餐（opencode.ai）适配器。

用户裁决接入方式：OpenCode（终端 AI 编程代理）的付费订阅套餐提供 API Key，
走 OpenAI 兼容接口（默认 https://opencode.ai/zen/v1，如 Go 套餐接口地址/模型名
不同，在设置页或 config.yaml 里改即可）。
- 无联网提问能力（supports_web_search=false），联网档自动排除；
- 订阅制计费：token 费用估算表不含它（套餐内已付费），api_call_log 仍记录 token。
"""

from geo.engines.base import EngineAdapter, EngineError


class OpenCodeAdapter(EngineAdapter):
    code = "opencode"
    display_name = "OpenCode"
    note = "OpenCode 订阅套餐托管模型：回答口径与其订阅端一致，费用按套餐计。"

    def chat(self, messages, temperature=None, jitter=False, timeout=60,
             web_search=False, model=None):
        if web_search:
            raise EngineError("OpenCode 暂不支持联网提问，联网监测不包含它")
        return self.call_openai_compatible(messages, temperature, jitter=jitter,
                                           timeout=timeout, model=model)
