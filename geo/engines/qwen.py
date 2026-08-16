"""通义千问（阿里云百炼）官方 API 适配器。

常规提问：OpenAI 兼容模式（compatible-mode/v1，改动最小）。
联网提问（02d 5.1 修订）：改用 DashScope 原生协议
（/api/v1/services/aigc/<type>-generation/generation，parameters.enable_search=true）
——只有原生协议返回结构化引用 output.search_info.search_results[]，
OpenAI 兼容模式的 enable_search 拿不到引用，故联网档不走兼容模式。

端点选择（按模型类型，DashScope 官方口径）：
- 文本模型（qwen-plus / qwen3-max / qwen3.7-max / deepseek-v4-pro 等）→ text-generation；
- 多模态模型（qwen-vl-* / -vl 系列等）→ multimodal-generation，
  消息 content 需为 [{text: ...}] 数组、响应 content 也可能为数组；
- 若请求 400 且提示 url error（模型类型与端点不匹配），自动换另一端点重试一次。

费用记录：usage.input_tokens / output_tokens，与价格表 qwen 前缀匹配。
"""

import random
import time

import requests

from geo import config
from geo.analyzers import sources as sources_mod
from geo.engines.base import (ChatResult, EngineAdapter, EngineError,
                              friendly_error, log_api_call)


class _EndpointMismatch(Exception):
    """模型类型与端点不匹配（DashScope 400 url error），供切换端点重试。"""


class QwenAdapter(EngineAdapter):
    code = "qwen"
    display_name = "通义千问"
    supports_web_search = True

    # DashScope 原生协议地址（与兼容模式的 base_url 不同，故单独声明）
    _NATIVE_BASE = "https://dashscope.aliyuncs.com/api/v1/services/aigc"
    _TEXT_EP = f"{_NATIVE_BASE}/text-generation/generation"
    _MULTIMODAL_EP = f"{_NATIVE_BASE}/multimodal-generation/generation"
    # 多模态模型名特征（-vl 系列）。2026-08-16 修正：不再把 "qwen3.7" 前缀整体当多模态——
    # qwen3.7-max 是纯文本模型，误走 multimodal 端点虽然能返回 200，但会静默丢掉
    # search_info.search_results（信源为空）；text 端点 + enable_source 才返回来源。
    # 真正的多模态模型走 text 端点会报 400 url error，由端点重试自动兜底。
    _MULTIMODAL_MARKERS = ("-vl",)

    @classmethod
    def _is_multimodal(cls, model: str) -> bool:
        m = (model or "").lower()
        return any(k in m for k in cls._MULTIMODAL_MARKERS)

    def chat(self, messages, temperature=None, jitter=False, timeout=60,
             web_search=False, model=None):
        if not web_search:
            return self.call_openai_compatible(messages, temperature, jitter=jitter,
                                               timeout=timeout, model=model)
        return self._dashscope_web_chat(messages, temperature, jitter, timeout, model)

    def _dashscope_web_chat(self, messages, temperature, jitter, timeout, model=None):
        mon = config.get_section("monitor", {})
        if jitter and float(mon.get("max_interval", 3) or 3) > 0:
            low = float(mon.get("min_interval", 1.5) or 1.5)
            high = float(mon.get("max_interval", 3) or 3)
            time.sleep(random.uniform(low, high))

        model = model or self.get_web_model()
        if not (self.cfg.get("api_key") or "").strip():
            raise EngineError(f"{self.display_name}的钥匙（API Key）还没填，请先到设置页填写")
        if not model:
            raise EngineError(f"{self.display_name}的模型还没设置好，请先到设置页选择")

        # 首选端点按模型类型判断，备选另一端点做 400 url error 时的兜底
        if self._is_multimodal(model):
            endpoints = [self._MULTIMODAL_EP, self._TEXT_EP]
        else:
            endpoints = [self._TEXT_EP, self._MULTIMODAL_EP]

        last_err = None
        for ep in endpoints:
            try:
                return self._dashscope_chat_once(ep, messages, model, temperature, timeout)
            except _EndpointMismatch as e:
                last_err = e
                continue
        raise EngineError(f"{self.display_name} 联网提问的模型档位与接口不匹配，"
                          f"请到设置页换一个模型档位试试")

    def _dashscope_chat_once(self, endpoint, messages, model, temperature, timeout):
        mon = config.get_section("monitor", {})
        max_retries = int(mon.get("max_retries", 2) or 2)
        backoff = float(mon.get("retry_backoff_seconds", 2) or 2)
        temp = float(temperature if temperature is not None else mon.get("temperature", 0.3))
        multimodal = endpoint.endswith("multimodal-generation")

        # 多模态端点的消息 content 需为 [{text: ...}] 数组
        input_msgs = []
        for m in messages or []:
            item = dict(m)
            if multimodal and isinstance(m.get("content"), str):
                item["content"] = [{"text": m["content"]}]
            input_msgs.append(item)

        payload = {
            "model": model,
            "input": {"messages": input_msgs},
            "parameters": {
                "result_format": "message",
                "temperature": temp,
                "enable_search": True,
                # 2026-08-16：官方文档（platform.qianwenai.com web-search）要求
                # search_options.enable_source=true 才在响应的 search_info.search_results
                # 里返回来源列表（index/title/url）；不加则联网回答不带来源，信源排行为空
                "search_options": {
                    "enable_source": True,
                },
            },
        }
        headers = {
            "Authorization": f"Bearer {self.cfg['api_key'].strip()}",
            "Content-Type": "application/json",
        }

        last_err = None
        for attempt in range(max_retries + 1):
            if attempt > 0:
                wait = backoff * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                time.sleep(wait)
            try:
                resp = requests.post(endpoint, json=payload, headers=headers,
                                     timeout=timeout)
            except requests.exceptions.RequestException as e:
                last_err = e
                continue
            if resp.status_code == 200:
                try:
                    data = resp.json()
                except Exception:
                    raise EngineError(f"{self.display_name} 返回的内容格式不对，请稍后再试")
                return self._parse_dashscope(data, model)
            elif resp.status_code in (401, 403):
                raise EngineError(f"{self.display_name}的钥匙（API Key）不对或已失效，请到设置页重新填写")
            elif resp.status_code == 429:
                last_err = EngineError(f"{self.display_name} 的请求太频繁了，稍等一下我会自动重试")
                continue
            else:
                # 模型类型与端点不匹配：换另一端点重试（由调用方处理）
                if resp.status_code == 400 and "url error" in (resp.text or "").lower():
                    raise _EndpointMismatch("url error")
                last_err = EngineError(f"{self.display_name} 暂时出了点问题，请稍后再试")
                if resp.status_code < 500:
                    raise last_err

        if isinstance(last_err, EngineError):
            raise last_err
        raise EngineError(friendly_error(last_err, self.display_name))

    def _parse_dashscope(self, data: dict, model: str) -> ChatResult:
        """原生协议响应解析：output.choices[0].message.content（字符串或数组）+ search_info。"""
        try:
            output = data["output"]
            content = output["choices"][0]["message"].get("content")
            if isinstance(content, list):
                text = "".join(c.get("text", "") for c in content
                               if isinstance(c, dict) and c.get("text"))
            else:
                text = content or ""
        except Exception:
            raise EngineError(f"{self.display_name} 返回的内容格式不对，请稍后再试")
        usage = data.get("usage") or {}
        tokens_in = usage.get("input_tokens") or 0
        tokens_out = usage.get("output_tokens") or 0
        log_api_call(self.code, model, tokens_in, tokens_out)
        raw = []
        try:
            results = (output.get("search_info") or {}).get("search_results") or []
            if isinstance(results, list):
                for item in results:
                    if isinstance(item, dict) and item.get("url"):
                        raw.append({
                            "url": str(item["url"]),
                            "title": str(item.get("title") or item.get("site_name") or ""),
                        })
        except Exception:
            raw = []
        src = sources_mod.normalize_sources(raw)
        return ChatResult(text=text, model=model,
                          tokens_in=tokens_in, tokens_out=tokens_out,
                          sources=src or None)
