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


# ---------------- Responses API 公共解析（2026-09 评审 R3 收敛） ----------------
# 此前 deepseek_responses / doubao_responses / opencode 三处各复制一份几乎
# 逐行相同的 output 文本解析与 url_citation 信源提取，现在收拢为模块级纯函数。

def extract_responses_text(data: dict) -> str:
    """Responses API 输出解析：output 数组里 type=message 的内容文本（含增量段）。"""
    parts = []
    for item in (data or {}).get("output") or []:
        if item.get("type") != "message":
            continue
        for c in item.get("content") or []:
            if c.get("type") == "output_text":
                parts.append(c.get("text") or "")
    return "".join(parts)


def extract_url_citation_sources(data: dict) -> list:
    """Responses API 的 url_citation → 标准信源 [{title,url,domain,category}]。

    引用位置：output[].message.content[].annotations（output_text 项内部），
    元素为平铺结构 {"type":"url_citation","url":...,"title":...,"site_name":...}。
    """
    from geo.analyzers import sources as sources_mod
    raw = []
    for item in (data or {}).get("output") or []:
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


# ---------------- 共享限流重试循环（2026-09 评审 R3 收敛） ----------------
# 此前同一套「429/网络错误指数退避重试、401/403 钥匙话术、404 not found→
# 模型不存在、5xx 重试/4xx 直抛」独立实现于 6 处并已出现漂移；现在收拢为
# 一个函数，重试次数/退避间隔/各状态码文案与原实现完全一致（口径不变）。

def http_post_with_retry(display_name: str, url: str, payload: dict, headers: dict,
                         timeout: int, model_not_found_msg: str = None,
                         classify_error=None) -> dict:
    """统一的限流重试 HTTP POST：200 返回解析后的 JSON dict，失败抛 EngineError。

    - model_not_found_msg：404 且响应含 not found / does not exist 时的大白话
      文案（各平台话术不同，由调用方传入；不传则走通用「暂时出了点问题」）；
    - classify_error(resp) -> Exception | None：平台特殊错误兜底识别（如千问
      400 url error 换端点、豆包 ToolNotOpen 插件未开通），命中即抛出；
    - 其余口径：401/403 钥匙话术直抛；429 记录后重试；4xx 直抛、5xx 重试；
      网络异常记录后重试，重试耗尽按 friendly_error 翻译。
    """
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
                return resp.json()
            except Exception:
                raise EngineError(f"{display_name} 返回的内容格式不对，请稍后再试")
        elif resp.status_code in (401, 403):
            raise EngineError(f"{display_name}的钥匙（API Key）不对或已失效，请到设置页重新填写")
        elif resp.status_code == 429:
            last_err = EngineError(f"{display_name} 的请求太频繁了，稍等一下我会自动重试")
            continue
        else:
            if classify_error:
                special = classify_error(resp)
                if special is not None:
                    raise special
            body_text = resp.text or ""
            if (model_not_found_msg and resp.status_code == 404
                    and ("not found" in body_text.lower()
                         or "does not exist" in body_text.lower())):
                raise EngineError(model_not_found_msg)
            last_err = EngineError(f"{display_name} 暂时出了点问题，请稍后再试")
            # 5xx 才重试，其余直接报错
            if resp.status_code < 500:
                raise last_err

    if isinstance(last_err, EngineError):
        raise last_err
    raise EngineError(friendly_error(last_err, display_name))


class ChatResult:
    def __init__(self, text: str, model: str, tokens_in: int = 0, tokens_out: int = 0,
                 sources: list = None):
        self.text = text
        self.model = model
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.sources = sources  # 结构化信源 [{title,url,domain,category}]，None=未提供


def _pick_price(model: str) -> tuple:
    """按模型名前缀从配置的价格表里匹配（输入价/输出价，元每百万 token）。

    最长前缀优先：同一系列可同时放「精确档」与「系列前缀」两级条目
    （如 hy3 精确条目优先于 hy 系列前缀兜底，见 config.example.yaml 的
    pricing 段注释）；未收录的模型按 0 记，估算与账单解耦、不瞎猜。
    """
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
    # 是否模型厂家自有引擎：订阅网关/聚合类（如 opencode，一把钥匙托管多家模型）
    # 置 False——回答照常监测落库与统计，但不进报告页「引擎厂商对比」。
    model_vendor = True

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

    # ---------------- 调用公共件（2026-09 评审 R3 收敛） ----------------

    def _jitter_sleep(self, jitter):
        """调用前的随机限速等待（monitor.min_interval/max_interval）。"""
        if not jitter:
            return
        mon = config.get_section("monitor", {})
        if float(mon.get("max_interval", 3) or 3) > 0:
            low = float(mon.get("min_interval", 1.5) or 1.5)
            high = float(mon.get("max_interval", 3) or 3)
            time.sleep(random.uniform(low, high))

    def _require_call_config(self, model: str = "") -> str:
        """调用前的大白话校验：钥匙/模型/接口地址（原 5 处复制同一套话术）。

        返回去掉尾部斜杠的 base_url。model 传 None 可跳过模型校验
        （OpenCode 等先校验钥匙地址、再按形态校验模型的顺序保持不变）。
        """
        if not (self.cfg.get("api_key") or "").strip():
            raise EngineError(f"{self.display_name}的钥匙（API Key）还没填，请先到设置页填写")
        if model is not None and not model:
            raise EngineError(f"{self.display_name}的模型还没设置好，请先到设置页选择")
        base_url = self.get_base_url()
        if not base_url:
            raise EngineError(f"{self.display_name}的接口地址还没配置好，请联系开发者检查配置文件")
        return base_url

    def _post_with_retry(self, url: str, payload: dict, headers: dict, timeout: int,
                         model_not_found_msg: str = None,
                         classify_error=None) -> dict:
        """共享限流重试循环（见模块级 http_post_with_retry 的口径说明）。"""
        return http_post_with_retry(self.display_name, url, payload, headers,
                                    timeout, model_not_found_msg=model_not_found_msg,
                                    classify_error=classify_error)

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
        temp = float(temperature if temperature is not None else mon.get("temperature", 0.3))
        model = model or self.get_model()
        base_url = self._require_call_config(model)

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

        return self._post_with_retry(
            url, payload, headers, timeout,
            model_not_found_msg=(f"{self.display_name} 提示模型不存在：请到对应的 AI 平台控制台"
                                 f"确认模型 ID，再到设置页换模型档位"))

    def call_openai_compatible(self, messages: list, temperature: float = None,
                               model: str = None, jitter: bool = False,
                               timeout: int = 60, extra_payload: dict = None,
                               tools: list = None) -> ChatResult:
        """统一的 OpenAI 兼容协议调用：限流重试 + 指数退避 + 费用记录。"""
        self._jitter_sleep(jitter)

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


class ResponsesWebAdapter(EngineAdapter):
    """联网档 Responses API 适配器公共基类（2026-09 评审 R3 参数化合并）。

    DeepSeek 与豆包两套联网适配器此前各自复制了约 130 行几乎逐行相同的实现
    （HTTP 限流重试/状态码大白话翻译/output 文本解析/url_citation 信源提取），
    只差平台专属文案与工具名来源；公共部分收拢在这里，子类只声明差异：
    - web_tool_type_setting：从引擎配置读联网工具名的键名（DeepSeek 可经
      config 的 web_tool_type 改名；None = 固定 "web_search"）；
    - model_not_found_msg：404 not found 的大白话文案（各家平台话术不同）；
    - classify_error：平台特殊错误兜底识别（如豆包 ToolNotOpen 插件未开通
      要给控制台开通指引），命中即抛出；
    - empty_text_message：200 但没有解析出文本时的报错（DeepSeek 明确报
      「联网搜索没有返回内容」；置 None 保持豆包旧行为、不做该校验）。
    信源提取两家平台字段结构一致，共用同一份 url_citation 实现。
    """

    supports_web_search = True
    web_tool_type_setting = None
    model_not_found_msg = ""
    empty_text_message = None

    def _tool_type(self) -> str:
        if self.web_tool_type_setting:
            return str(self.cfg.get(self.web_tool_type_setting) or "web_search").strip()
        return "web_search"

    def classify_error(self, resp):
        """平台特殊错误识别；子类按需覆盖，返回异常对象或 None。"""
        return None

    def chat(self, messages, temperature=None, jitter=False, timeout=60,
             web_search=True, model=None):
        self._jitter_sleep(jitter)
        model = model or self.get_web_model()
        base_url = self._require_call_config(model)

        payload = {
            "model": model,
            "input": messages,
            "tools": [{"type": self._tool_type()}],
        }
        headers = {
            "Authorization": f"Bearer {self.cfg['api_key'].strip()}",
            "Content-Type": "application/json",
        }
        data = self._post_with_retry(
            f"{base_url}/responses", payload, headers, timeout,
            model_not_found_msg=self.model_not_found_msg,
            classify_error=self.classify_error)

        text = extract_responses_text(data)
        if not text and self.empty_text_message:
            raise EngineError(self.empty_text_message)
        usage = data.get("usage") or {}
        tokens_in = usage.get("input_tokens") or 0
        tokens_out = usage.get("output_tokens") or 0
        log_api_call(self.code, model, tokens_in, tokens_out)
        src = extract_url_citation_sources(data)
        return ChatResult(text=text, model=model,
                          tokens_in=tokens_in, tokens_out=tokens_out,
                          sources=src or None)
