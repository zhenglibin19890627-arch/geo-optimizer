"""Kimi（月之暗面）官方 API 适配器。

联网提问（02d 5.1）：OpenAI 兼容接口 tools 声明内置工具 $web_search
（type=builtin_function），走 tool_calls 多轮循环，无需自建搜索。
联网档换官方在售模型（配置 web_model，默认 kimi-k2.6 经济档）。

工具循环要点（Moonshot 官方文档）：
- $web_search 是内置工具：模型只生成搜索参数，搜索由平台执行；
- tool 回传消息必须带 tool_call_id 和 name 两个字段（缺 name 模型无法
  匹配到对应 tool_call）；
- content 只需原样返回模型生成的 arguments（文档明确"只需原封不动返回
  入参 arguments"，不用自己组装搜索结果）；
- 最多循环 MAX_TOOL_ROUNDS 轮，始终拿不到最终回答则抛大白话错误，
  避免空回答入库 / 无限调用。
"""

from geo.engines.base import ChatResult, EngineAdapter, EngineError, log_api_call

WEB_SEARCH_TOOL = [{"type": "builtin_function", "function": {"name": "$web_search"}}]
MAX_TOOL_ROUNDS = 2  # 搜索工具循环最多 2 轮（第 1 轮触发搜索、第 2 轮必须给出最终回答）


class KimiAdapter(EngineAdapter):
    code = "kimi"
    display_name = "Kimi"
    supports_web_search = True

    def chat(self, messages, temperature=None, jitter=False, timeout=60,
             web_search=False, model=None):
        if not web_search:
            return self.call_openai_compatible(messages, temperature, jitter=jitter,
                                               timeout=timeout, model=model)
        return self._web_search_chat(messages, temperature, jitter, timeout)

    def _web_search_chat(self, messages, temperature, jitter, timeout):
        if jitter:
            import random
            import time
            from geo import config
            mon = config.get_section("monitor", {})
            low = float(mon.get("min_interval", 1.5) or 1.5)
            high = float(mon.get("max_interval", 3) or 3)
            time.sleep(random.uniform(low, high))
        model = self.get_web_model()

        conv = list(messages)
        for round_no in range(1, MAX_TOOL_ROUNDS + 1):
            data = self._chat_completions_raw(conv, temperature, model, timeout,
                                              tools=WEB_SEARCH_TOOL)
            usage = data.get("usage") or {}
            log_api_call(self.code, model,
                         usage.get("prompt_tokens") or 0, usage.get("completion_tokens") or 0)
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
                # 既没内容也没工具调用：这轮没有搜到任何东西
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
                # 官方要求：tool 消息带 tool_call_id + name，content 原样返回 arguments
                conv.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "name": name,
                    "content": fn.get("arguments") or "{}",
                })

        raise EngineError(f"{self.display_name} 联网搜索一直没给出最终回答，请稍后再试")
