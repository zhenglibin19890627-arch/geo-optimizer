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

import random
import time

import requests

from geo import config
from geo.analyzers import sources as sources_mod
from geo.engines.base import (ChatResult, EngineAdapter, EngineError,
                              friendly_error, log_api_call)

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
        if not jitter:
            return
        mon = config.get_section("monitor", {})
        low = float(mon.get("min_interval", 1.5) or 1.5)
        high = float(mon.get("max_interval", 3) or 3)
        time.sleep(random.uniform(low, high))

    def _require(self):
        if not (self.cfg.get("api_key") or "").strip():
            raise EngineError(f"{self.display_name}的钥匙（API Key）还没填，请先到设置页填写")
        base_url = self.get_base_url()
        if not base_url:
            raise EngineError(f"{self.display_name}的接口地址还没配置好，请联系开发者检查配置文件")
        return base_url

    def _retry_loop(self, url, payload, headers, timeout, parse):
        """通用限流重试 + 状态码翻译；parse(data, model) 返回 ChatResult。"""
        mon = config.get_section("monitor", {})
        max_retries = int(mon.get("max_retries", 2) or 2)
        backoff = float(mon.get("retry_backoff_seconds", 2) or 2)
        last_err = None
        for attempt in range(max_retries + 1):
            if attempt > 0:
                wait = backoff * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                time.sleep(wait)
            try:
                resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
            except requests.exceptions.RequestException as e:
                last_err = e
                continue
            if resp.status_code == 200:
                try:
                    data = resp.json()
                except Exception:
                    raise EngineError(f"{self.display_name} 返回的内容格式不对，请稍后再试")
                return parse(data)
            elif resp.status_code in (401, 403):
                raise EngineError(f"{self.display_name}的钥匙（API Key）不对或已失效，请到设置页重新填写")
            elif resp.status_code == 429:
                last_err = EngineError(f"{self.display_name} 的请求太频繁了，稍等一下我会自动重试")
                continue
            else:
                body_text = resp.text or ""
                if resp.status_code == 404 and ("not found" in body_text.lower()
                                                or "does not exist" in body_text.lower()):
                    raise EngineError(
                        f"{self.display_name} 提示模型不存在：请到订阅页确认模型 ID，"
                        f"再到设置页/监测中心换模型档位")
                last_err = EngineError(f"{self.display_name} 暂时出了点问题，请稍后再试")
                if resp.status_code < 500:
                    raise last_err
        if isinstance(last_err, EngineError):
            raise last_err
        raise EngineError(friendly_error(last_err, self.display_name))

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

        def parse(data):
            text = self._extract_responses_text(data)
            if not text:
                raise EngineError(f"{self.display_name} 没有返回内容，请稍后再试")
            usage = data.get("usage") or {}
            tokens_in = usage.get("input_tokens") or 0
            tokens_out = usage.get("output_tokens") or 0
            log_api_call(self.code, model, tokens_in, tokens_out)
            src = self._extract_responses_sources(data)
            return ChatResult(text=text, model=model,
                              tokens_in=tokens_in, tokens_out=tokens_out,
                              sources=src or None)

        return self._retry_loop(
            f"{base_url}/responses",
            {"model": model, "input": messages},
            headers, timeout, parse)

    @staticmethod
    def _extract_responses_text(data: dict) -> str:
        parts = []
        for item in data.get("output") or []:
            if item.get("type") != "message":
                continue
            for c in item.get("content") or []:
                if c.get("type") == "output_text":
                    parts.append(c.get("text") or "")
        return "".join(parts)

    @staticmethod
    def _extract_responses_sources(data: dict) -> list:
        raw = []
        for item in data.get("output") or []:
            if item.get("type") != "message":
                continue
            for c in item.get("content") or []:
                for anno in c.get("annotations") or []:
                    if not isinstance(anno, dict):
                        continue
                    if anno.get("type") != "url_citation":
                        continue
                    url = str(anno.get("url") or "").strip()
                    if not url:
                        continue
                    entry = {"url": url}
                    title = str(anno.get("title") or "").strip()
                    if title:
                        entry["title"] = title
                    raw.append(entry)
        return sources_mod.normalize_sources(raw)

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

        def parse(data):
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

        return self._retry_loop(
            f"{base_url}/messages", payload, headers, timeout, parse)
