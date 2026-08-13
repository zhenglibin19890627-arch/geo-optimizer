"""豆包（火山方舟）联网提问适配器：Responses API（独立封装，不动现有 Chat 适配器）。

联网提问（02d 5.1）：控制台开通「联网内容插件（Web Search）」后，经
POST /api/v3/responses 携带 tools:[{"type":"web_search"}] 调用；
当前 Chat API（/chat/completions）官方文档未记录该工具，故独立新适配器。

信源口径（02d 修订）：调用成功后返回对象含 annotations（url_citation：
url/title/site_name/publish_time/summary），本适配器提取 url_citation 的
url+title 作为结构化信源（经 sources.normalize_sources 归一后随
ChatResult.sources 返回，供 monitor_task 优先落库；回答文本正则解析作为回落）。
"""

import json
import random
import time

import requests

from geo import config
from geo.analyzers import sources as sources_mod
from geo.engines.base import (ChatResult, EngineAdapter, EngineError,
                              friendly_error, log_api_call)


class DoubaoResponsesAdapter(EngineAdapter):
    """豆包 Responses API 适配器（仅联网档使用；chat 恒带 web_search）。

    code/display_name 与 Chat 适配器一致，便于监测任务按引擎代码记账与展示；
    常规档仍走 geo/engines/doubao.py 的 Chat 适配器，两者互不影响。
    """

    code = "doubao"
    display_name = "豆包"
    supports_web_search = True

    def chat(self, messages, temperature=None, jitter=False, timeout=60, web_search=True):
        mon = config.get_section("monitor", {})
        max_retries = int(mon.get("max_retries", 2) or 2)
        backoff = float(mon.get("retry_backoff_seconds", 2) or 2)
        if jitter and float(mon.get("max_interval", 3) or 3) > 0:
            low = float(mon.get("min_interval", 1.5) or 1.5)
            high = float(mon.get("max_interval", 3) or 3)
            time.sleep(random.uniform(low, high))

        model = self.get_web_model()
        if not (self.cfg.get("api_key") or "").strip():
            raise EngineError(f"{self.display_name}的钥匙（API Key）还没填，请先到设置页填写")
        if not model:
            raise EngineError(f"{self.display_name}的模型还没设置好，请先到设置页选择")
        base_url = self.get_base_url()
        if not base_url:
            raise EngineError(f"{self.display_name}的接口地址还没配置好，请联系开发者检查配置文件")

        payload = {
            "model": model,
            "input": messages,
            "tools": [{"type": "web_search"}],
        }
        headers = {
            "Authorization": f"Bearer {self.cfg['api_key'].strip()}",
            "Content-Type": "application/json",
        }
        url = f"{base_url}/responses"

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
                text = self._extract_text(data)
                usage = data.get("usage") or {}
                tokens_in = (usage.get("input_tokens") or 0)
                tokens_out = (usage.get("output_tokens") or 0)
                log_api_call(self.code, model, tokens_in, tokens_out)
                src = self._extract_sources(data)
                return ChatResult(text=text, model=model,
                                  tokens_in=tokens_in, tokens_out=tokens_out,
                                  sources=src or None)
            elif resp.status_code in (401, 403):
                raise EngineError(f"{self.display_name}的钥匙（API Key）不对或已失效，请到设置页重新填写")
            elif resp.status_code == 429:
                last_err = EngineError(f"{self.display_name} 的请求太频繁了，稍等一下我会自动重试")
                continue
            else:
                body_text = resp.text or ""
                if resp.status_code == 404 and (
                        "ToolNotOpen" in body_text
                        or "not activated web search" in body_text.lower()):
                    raise EngineError(
                        f"{self.display_name} 的联网搜索插件还没开通：请到火山方舟控制台开通"
                        f"「联网内容插件（Web Search）」（网址 console.volcengine.com/common-buy/"
                        f"CC_content_plugin）。开通前可先不用「联网提问」模式监测豆包")
                if resp.status_code == 404 and ("not found" in body_text.lower()
                                                or "does not exist" in body_text.lower()):
                    raise EngineError(
                        f"{self.display_name} 提示模型不存在：请到火山方舟控制台确认模型 ID"
                        f"（形如 doubao-1-5-lite-32k），再到设置页换模型档位")
                last_err = EngineError(f"{self.display_name} 暂时出了点问题，请稍后再试")
                if resp.status_code < 500:
                    raise last_err

        if isinstance(last_err, EngineError):
            raise last_err
        raise EngineError(friendly_error(last_err, self.display_name))

    @staticmethod
    def _extract_text(data: dict) -> str:
        """Responses API 输出解析：output 数组里 type=message 的内容文本（含增量段）。"""
        parts = []
        for item in data.get("output") or []:
            if item.get("type") != "message":
                continue
            for c in item.get("content") or []:
                if c.get("type") == "output_text":
                    parts.append(c.get("text") or "")
        return "".join(parts)

    @staticmethod
    def _extract_sources(data: dict) -> list:
        """Responses API 的 url_citation → 标准信源 [{title,url,domain,category}]。

        引用位置：output[].message.content[].annotations（output_text 项内部），
        顶层 data.annotations 为 null；元素为平铺结构
        {"type":"url_citation","url":...,"title":...,"site_name":...}。
        """
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
