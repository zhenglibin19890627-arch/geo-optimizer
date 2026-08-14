"""GEO 优化系统：AI 引擎适配器（统一接口、统一错误、限流重试、费用记录）。"""

import random
import time

import requests

from geo import config
from geo.models import db as database


class EngineError(Exception):
    """引擎调用失败，message 必须是大白话中文。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ChatResult:
    def __init__(self, text: str, model: str, tokens_in: int = 0, tokens_out: int = 0,
                 sources: list = None):
        self.text = text
        self.model = model
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.sources = sources  # 结构化信源 [{title,url,domain,category}]，None=未提供


def _pick_price(model: str) -> tuple:
    """按模型名前缀从配置的价格表里匹配（输入价/输出价，元每百万 token）。"""
    pricing = config.get_section("pricing", {})
    best = None
    best_len = 0
    for prefix, prices in pricing.items():
        if isinstance(prices, dict) and model and str(model).startswith(str(prefix)):
            if len(str(prefix)) > best_len:
                best_len = len(str(prefix))
                best = prices
    if best:
        return float(best.get("input", 0) or 0), float(best.get("output", 0) or 0)
    return 0.0, 0.0


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    p_in, p_out = _pick_price(model)
    return (tokens_in or 0) / 1000000 * p_in + (tokens_out or 0) / 1000000 * p_out


def get_call_timeout() -> int:
    """AI 调用超时秒数：monitor.timeout_seconds 可配，默认 90。

    豆包等平台响应偏慢（实测可达 20-60s），60s 固定超时会把慢响应误判为
    失败并触发重试，拖慢整轮监测；提高默认值减少误杀。
    """
    mon = config.get_section("monitor", {})
    try:
        return max(int(mon.get("timeout_seconds", 90) or 90), 10)
    except (TypeError, ValueError):
        return 90


def log_api_call(engine_code: str, model: str, tokens_in: int, tokens_out: int):
    """每次 AI 调用记录 token 与估算费用，支撑“本月大概花了多少钱”。"""
    cost = estimate_cost(model, tokens_in, tokens_out)
    with database.session_scope() as s:
        s.add(database.ApiCallLog(
            engine_code=engine_code,
            model=model,
            tokens_in=tokens_in or 0,
            tokens_out=tokens_out or 0,
            cost_yuan=round(cost, 6),
        ))


def friendly_error(e: Exception, display_name: str) -> str:
    """把底层异常翻译成大白话。"""
    if isinstance(e, EngineError):
        return e.message
    if isinstance(e, requests.exceptions.Timeout):
        return f"{display_name} 连接超时了，请检查网络后重试"
    if isinstance(e, requests.exceptions.ConnectionError):
        return f"连不上 {display_name} 的服务器，请检查网络后重试"
    if isinstance(e, requests.exceptions.RequestException):
        return f"请求 {display_name} 时网络出了点问题，请稍后再试"
    return f"{display_name} 那边出了点状况，请稍后再试"


class EngineAdapter:
    """所有自动监测引擎的基类。子类只需提供 code / display_name / is_configured。"""

    code = ""
    display_name = ""
    note = ""  # 口径说明（如元宝）
    supports_web_search = False  # 是否支持联网提问（各适配器按平台能力覆盖）

    def __init__(self):
        self.cfg = config.get_engine_config(self.code)

    def is_enabled(self) -> bool:
        if self.code == "manual":
            return True
        enabled = database.get_setting(f"engine_enabled_{self.code}", self.cfg.get("enabled", True))
        return bool(enabled)

    def is_configured(self) -> bool:
        """是否已填钥匙。"""
        if self.code == "manual":
            return True
        return bool((self.cfg.get("api_key") or "").strip())

    def get_model(self) -> str:
        if self.code == "manual":
            return ""
        return str(database.get_setting(f"engine_model_{self.code}", self.cfg.get("model", "")) or "")

    def get_web_model(self) -> str:
        """联网提问档模型：设置页可配置（engine_web_model_<code>），否则回落配置 web_model，再回落常规 model。"""
        if self.code == "manual":
            return ""
        return str(database.get_setting(
            f"engine_web_model_{self.code}",
            self.cfg.get("web_model") or self.cfg.get("model", "")) or "")

    def get_base_url(self) -> str:
        return str(self.cfg.get("base_url", "")).rstrip("/")

    def chat(self, messages: list, temperature: float = None,
             jitter: bool = False, timeout: int = 60, web_search: bool = False) -> ChatResult:
        """调用官方 API。失败抛 EngineError（大白话）。"""
        raise EngineError("这家引擎不能自动调用")

    def _chat_completions_raw(self, messages: list, temperature: float = None,
                              model: str = None, timeout: int = 60,
                              extra_payload: dict = None, tools: list = None) -> dict:
        """底层 OpenAI 兼容调用：限流重试 + 指数退避，返回解析后的 JSON。

        只负责网络与重试，不解析内容、不记费用（费用由调用方统一记录，
        供联网工具循环等多次调用场景按每次 HTTP 调用记账）。
        """
        mon = config.get_section("monitor", {})
        max_retries = int(mon.get("max_retries", 2) or 2)
        backoff = float(mon.get("retry_backoff_seconds", 2) or 2)
        temp = float(temperature if temperature is not None else mon.get("temperature", 0.3))
        model = model or self.get_model()

        if not (self.cfg.get("api_key") or "").strip():
            raise EngineError(f"{self.display_name}的钥匙（API Key）还没填，请先到设置页填写")
        if not model:
            raise EngineError(f"{self.display_name}的模型还没设置好，请先到设置页选择")
        base_url = self.get_base_url()
        if not base_url:
            raise EngineError(f"{self.display_name}的接口地址还没配置好，请联系开发者检查配置文件")

        url = f"{base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.cfg['api_key'].strip()}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temp,
        }
        if extra_payload:
            payload.update(extra_payload)
        if tools:
            payload["tools"] = tools

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
                    return resp.json()
                except Exception:
                    raise EngineError(f"{self.display_name} 返回的内容格式不对，请稍后再试")
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
                        f"{self.display_name} 提示模型不存在：请到对应的 AI 平台控制台"
                        f"确认模型 ID，再到设置页换模型档位")
                last_err = EngineError(f"{self.display_name} 暂时出了点问题，请稍后再试")
                # 5xx 才重试，其余直接报错
                if resp.status_code < 500:
                    raise last_err

        if isinstance(last_err, EngineError):
            raise last_err
        raise EngineError(friendly_error(last_err, self.display_name))

    def call_openai_compatible(self, messages: list, temperature: float = None,
                               model: str = None, jitter: bool = False,
                               timeout: int = 60, extra_payload: dict = None,
                               tools: list = None) -> ChatResult:
        """统一的 OpenAI 兼容协议调用：限流重试 + 指数退避 + 费用记录。"""
        if jitter:
            mon = config.get_section("monitor", {})
            if float(mon.get("max_interval", 3) or 3) > 0:
                low = float(mon.get("min_interval", 1.5) or 1.5)
                high = float(mon.get("max_interval", 3) or 3)
                time.sleep(random.uniform(low, high))

        data = self._chat_completions_raw(messages, temperature, model, timeout,
                                          extra_payload, tools)
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except Exception:
            raise EngineError(f"{self.display_name} 返回的内容格式不对，请稍后再试")
        usage = data.get("usage") or {}
        tokens_in = usage.get("prompt_tokens") or 0
        tokens_out = usage.get("completion_tokens") or 0
        log_api_call(self.code, data.get("model") or model or self.get_model(),
                     tokens_in, tokens_out)
        # 结构化引用提取：优先 choices[0].message.citations（OpenAI 兼容通用），
        # 兜底顶层 search_info.search_results（腾讯混元原生字段名）
        from geo.analyzers import sources as sources_mod
        citations = []
        try:
            cit = data["choices"][0]["message"].get("citations") or []
            if isinstance(cit, list):
                citations = cit
        except Exception:
            citations = []
        if not citations:
            try:
                results = (data.get("search_info") or {}).get("search_results") or []
                if isinstance(results, list):
                    citations = results
            except Exception:
                citations = []
        return ChatResult(text=text,
                          model=data.get("model") or model or self.get_model(),
                          tokens_in=tokens_in, tokens_out=tokens_out,
                          sources=sources_mod.normalize_sources(citations)
                          if citations else None)
