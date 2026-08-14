"""OpenCode 订阅套餐（opencode.ai）适配器。

接入方式：OpenCode 订阅套餐提供 API Key，走 OpenAI 兼容接口
（默认 https://opencode.ai/zen/v1，接口地址/模型名/联网工具名可在
config.yaml 或设置页改）。

联网提问（2026-08-14）：订阅端支持联网搜索（opencode 配置里的
websearch 工具，permission.websearch=allow）。本适配器用工具循环模式：
请求携带 websearch 工具声明，模型触发 tool_calls 后回填参数、
最多 2 轮拿最终回答（与 Kimi $web_search 循环同模式）。

订阅制计费：token 费用估算表不含它（套餐内已付费），api_call_log 仍记录 token。
"""

import random
import time

from geo import config
from geo.engines.base import ChatResult, EngineAdapter, EngineError, log_api_call

MAX_TOOL_ROUNDS = 2  # 搜索工具循环最多 2 轮（第 1 轮触发搜索、第 2 轮必须给出最终回答）


class OpenCodeAdapter(EngineAdapter):
    code = "opencode"
    display_name = "OpenCode"
    note = "OpenCode 订阅套餐托管模型：回答口径与其订阅端一致，费用按套餐计。"
    supports_web_search = True

    def chat(self, messages, temperature=None, jitter=False, timeout=60,
             web_search=False, model=None):
        if not web_search:
            return self.call_openai_compatible(messages, temperature, jitter=jitter,
                                               timeout=timeout, model=model)
        return self._web_search_chat(messages, temperature, jitter, timeout, model)

    def _web_search_chat(self, messages, temperature, jitter, timeout, model):
        if jitter:
            mon = config.get_section("monitor", {})
            low = float(mon.get("min_interval", 1.5) or 1.5)
            high = float(mon.get("max_interval", 3) or 3)
            time.sleep(random.uniform(low, high))
        model = model or self.get_web_model()
        # 联网工具名以订阅端为准（websearch）；官方若改名改 config.yaml 的
        # engines.opencode.web_tool_type，无需动代码
        tool_name = str(self.cfg.get("web_tool_type") or "websearch").strip()
        tools = [{
            "type": "function",
            "function": {
                "name": tool_name,
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        }]

        conv = list(messages)
        for round_no in range(1, MAX_TOOL_ROUNDS + 1):
            data = self._chat_completions_raw(conv, temperature, model, timeout,
                                              tools=tools)
            usage = data.get("usage") or {}
            log_api_call(self.code, model,
                         usage.get("prompt_tokens") or 0,
                         usage.get("completion_tokens") or 0)
            try:
                msg = data["choices"][0]["message"]
            except Exception:
                raise EngineError(f"{self.display_name} 返回的内容格式不对，请稍后再试")
            text = msg.get("content") or ""
            if text.strip():
                # 已拿到最终回答（无论是否继续触发搜索，以文本为准）
                return ChatResult(text=text, model=model,
                                  tokens_in=usage.get("prompt_tokens") or 0,
                                  tokens_out=usage.get("completion_tokens") or 0)
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                raise EngineError(f"{self.display_name} 联网搜索没有返回内容，请稍后再试")
            if round_no >= MAX_TOOL_ROUNDS:
                raise EngineError(f"{self.display_name} 联网搜索一直没给出最终回答，请稍后再试")

            # 把工具调用结果回填给模型，让模型基于搜索结果作答
            conv.append({"role": "assistant", "content": text,
                         "tool_calls": tool_calls})
            for tc in tool_calls:
                fn = tc.get("function") or {}
                name = fn.get("name") or ""
                tool_call_id = tc.get("id") or ""
                if not name or not tool_call_id:
                    raise EngineError(f"{self.display_name} 联网搜索返回的工具信息不完整，请稍后再试")
                conv.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "name": name,
                    "content": fn.get("arguments") or "{}",
                })

        raise EngineError(f"{self.display_name} 联网搜索一直没给出最终回答，请稍后再试")
