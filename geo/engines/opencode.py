"""OpenCode Go 订阅适配器（官方文档：https://opencode.ai/docs/zh-cn/go）。

Go 网关按模型路由到三种 API 形态（官方 API 端点表）：
- chat/completions（OpenAI 兼容）：GLM / Kimi / DeepSeek / MiMo / Hy3
- responses（OpenAI Responses）：Grok 4.5 / GPT 5.6 Luna
- messages（Anthropic Messages）：MiniMax M3/M2.7 / Qwen3.8/3.7/3.6

本适配器按模型名自动选择形态与鉴权头（OpenAI 形态 Bearer，
Anthropic 形态 x-api-key + anthropic-version）。

联网说明：官方 API 文档未提供联网搜索工具——opencode 配置里的
permission.websearch 是客户端（TUI）的本地搜索能力，订阅 API 网关
不提供，故联网档自动排除（supports_web_search=false）。

订阅制计费：token 费用估算表不含它（套餐内已付费），api_call_log 仍记录 token。
"""

from geo.engines.base import (ChatResult, EngineAdapter, EngineError,
                              extract_responses_text,
                              extract_url_citation_sources, log_api_call)

# 官方端点表：各模型对应的 API 形态
_RESPONSES_MODELS = {"grok-4.5", "gpt-5.6-luna"}
_ANTHROPIC_MODELS = {
    "minimax-m3", "minimax-m2.7", "minimax-m2.5",
    "qwen3.8-max", "qwen3.7-max", "qwen3.7-plus", "qwen3.6-plus",
}


class OpenCodeAdapter(EngineAdapter):
    code = "opencode"
    display_name = "OpenCode"
    note = "OpenCode Go 订阅托管模型：回答口径与其订阅端一致，费用按套餐计。"
    supports_web_search = False  # 官方 API 无联网工具（websearch 是客户端本地能力）
    # 订阅网关（一把钥匙托管 GLM/Kimi/GPT/MiniMax 等多家模型），不是模型厂家：
    # 不进报告页「引擎厂商对比」（模型归属随所选档位，归到 OpenCode 名下反而失真）
    model_vendor = False

    @classmethod
    def _shape_for(cls, model: str) -> str:
        """模型 → API 形态：chat（OpenAI 兼容）/ responses / anthropic。"""
        m = (model or "").strip().lower()
        if m in _RESPONSES_MODELS:
            return "responses"
        if m in _ANTHROPIC_MODELS:
            return "anthropic"
        return "chat"

    def chat(self, messages, temperature=None, jitter=False, timeout=60,
             web_search=False, model=None):
        if web_search:
            raise EngineError("OpenCode 订阅 API 未提供联网搜索工具，请用常规提问模式")
        model = model or self.get_model()
        shape = self._shape_for(model)
        if shape == "anthropic":
            return self._chat_anthropic(messages, temperature, jitter, timeout, model)
        if shape == "responses":
            return self._chat_responses(messages, temperature, jitter, timeout, model)
        return self.call_openai_compatible(messages, temperature, jitter=jitter,
                                           timeout=timeout, model=model)

    # ---------------- 公共：jitter / 校验 ----------------

    def _jitter_sleep(self, jitter):
        # R3 收敛：与各适配器共用 base 的限速等待
        super()._jitter_sleep(jitter)

    def _require(self):
        """钥匙/地址大白话校验（模型按 API 形态另行校验，保持原顺序）。"""
        return self._require_call_config(model=None)

    # R3 收敛：原 _retry_loop（限流重试 + 状态码翻译）与 base 各处逐行相同，
    # 统一改用 base.http_post_with_retry（经 _post_with_retry），口径不变

    # ---------------- Responses 形态（Grok 4.5 / GPT 5.6 Luna） ----------------

    def _chat_responses(self, messages, temperature, jitter, timeout, model):
        self._jitter_sleep(jitter)
        base_url = self._require()
        if not model:
            raise EngineError(f"{self.display_name}的模型还没设置好，请先到设置页选择")
        headers = {
            "Authorization": f"Bearer {self.cfg['api_key'].strip()}",
            "Content-Type": "application/json",
        }
        data = self._post_with_retry(
            f"{base_url}/responses",
            {"model": model, "input": messages},
            headers, timeout,
            model_not_found_msg=(f"{self.display_name} 提示模型不存在：请到订阅页确认模型 ID，"
                                 f"再到设置页/监测中心换模型档位"))
        text = extract_responses_text(data)
        if not text:
            raise EngineError(f"{self.display_name} 没有返回内容，请稍后再试")
        usage = data.get("usage") or {}
        tokens_in = usage.get("input_tokens") or 0
        tokens_out = usage.get("output_tokens") or 0
        log_api_call(self.code, model, tokens_in, tokens_out)
        src = extract_url_citation_sources(data)
        return ChatResult(text=text, model=model,
                          tokens_in=tokens_in, tokens_out=tokens_out,
                          sources=src or None)

    # ---------------- Anthropic Messages 形态（MiniMax / Qwen3.6-3.8） ----------------

    def _chat_anthropic(self, messages, temperature, jitter, timeout, model):
        self._jitter_sleep(jitter)
        base_url = self._require()
        if not model:
            raise EngineError(f"{self.display_name}的模型还没设置好，请先到设置页选择")
        system = None
        msgs = []
        for m in messages or []:
            if m.get("role") == "system":
                system = m.get("content") or ""
                continue
            msgs.append({"role": m.get("role", "user"), "content": m.get("content") or ""})
        payload = {"model": model, "max_tokens": 4096, "messages": msgs}
        if system:
            payload["system"] = system
        headers = {
            "x-api-key": self.cfg["api_key"].strip(),
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

        data = self._post_with_retry(
            f"{base_url}/messages", payload, headers, timeout,
            model_not_found_msg=(f"{self.display_name} 提示模型不存在：请到订阅页确认模型 ID，"
                                 f"再到设置页/监测中心换模型档位"))
        text = "".join(
            str(b.get("text") or "") for b in (data.get("content") or [])
            if isinstance(b, dict) and b.get("type") == "text")
        if not text:
            raise EngineError(f"{self.display_name} 没有返回内容，请稍后再试")
        usage = data.get("usage") or {}
        tokens_in = usage.get("input_tokens") or 0
        tokens_out = usage.get("output_tokens") or 0
        log_api_call(self.code, model, tokens_in, tokens_out)
        return ChatResult(text=text, model=model,
                          tokens_in=tokens_in, tokens_out=tokens_out)
