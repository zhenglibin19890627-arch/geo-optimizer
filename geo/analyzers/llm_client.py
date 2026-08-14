"""分析用大模型统一客户端（OpenAI 兼容）。

厂商可切换（2026-08-14 修订）：设置页可选 5 家厂商（deepseek/doubao/
qwen/yuanbao/opencode），分析任务复用该厂商的钥匙/接口地址/档位列表；
默认 DeepSeek（最便宜档），deepseek 厂商在引擎钥匙未填时回落旧版
analysis 节的钥匙/地址（老配置兼容）。
"""

import random
import time

import requests

from geo import config
from geo.engines import base as engine_base
from geo.models import db as database


class AnalysisError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def get_analysis_vendor() -> str:
    """当前分析模型厂商（引擎 code）；非法值回落 deepseek。"""
    from geo.engines import AUTO_CODES
    vendor = str(database.get_setting("analysis_vendor", "deepseek") or "deepseek").strip()
    return vendor if vendor in AUTO_CODES else "deepseek"


def _vendor_cfg(vendor: str) -> tuple:
    """厂商的 (api_key, base_url, default_model)。

    deepseek：优先 engines.deepseek 节，未填回落 analysis 节（旧口径兼容）；
    其余厂商：直接读该引擎节的钥匙/地址/模型。
    """
    engine = config.get_engine_config(vendor) or {}
    api_key = str(engine.get("api_key") or "").strip()
    base_url = str(engine.get("base_url") or "").strip().rstrip("/")
    model = str(engine.get("model") or "").strip()
    if vendor == "deepseek":
        analysis = config.get_analysis_config() or {}
        api_key = api_key or str(analysis.get("api_key") or "").strip()
        base_url = base_url or str(analysis.get("base_url") or "").strip().rstrip("/")
        model = model or str(analysis.get("model") or "").strip()
    return api_key, base_url, model


def get_analysis_model() -> str:
    """当前分析模型档位（默认当前厂商的默认档）。"""
    _key, _base, default_model = _vendor_cfg(get_analysis_vendor())
    return str(database.get_setting("analysis_model", default_model) or "")


def is_configured() -> bool:
    key, _base, _model = _vendor_cfg(get_analysis_vendor())
    return bool(key)


def chat(prompt: str, temperature: float = 0.3, timeout: int = 60, system: str = None) -> str:
    """用分析模型执行一次思考，返回文本。失败抛 AnalysisError（大白话）。"""
    vendor = get_analysis_vendor()
    api_key, base_url, _m = _vendor_cfg(vendor)
    if not api_key:
        raise AnalysisError("分析用的模型还没填钥匙（API Key），请先到设置页填写")
    model = get_analysis_model()
    if not model:
        raise AnalysisError("分析用的模型还没设置好，请先到设置页选择")
    if not base_url:
        raise AnalysisError("分析用的接口地址还没配置好，请联系开发者检查配置文件")

    # OpenCode 厂商：网关按模型路由三种形态，直接复用其适配器
    if vendor == "opencode":
        from geo.engines.opencode import OpenCodeAdapter
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": prompt})
        try:
            return OpenCodeAdapter().chat(msgs, temperature=temperature,
                                          timeout=timeout, model=model).text
        except engine_base.EngineError as e:
            raise AnalysisError(e.message)

    mon = config.get_section("monitor", {})
    max_retries = int(mon.get("max_retries", 2) or 2)
    backoff = float(mon.get("retry_backoff_seconds", 2) or 2)

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    url = f"{base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "temperature": temperature}

    last_err = None
    for attempt in range(max_retries + 1):
        if attempt > 0:
            time.sleep(backoff * (2 ** (attempt - 1)) + random.uniform(0, 0.5))
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
        except requests.exceptions.RequestException as e:
            last_err = e
            continue
        if resp.status_code == 200:
            data = resp.json()
            try:
                text = data["choices"][0]["message"]["content"] or ""
            except Exception:
                raise AnalysisError("分析用的模型返回的内容格式不对，请稍后再试")
            usage = data.get("usage") or {}
            tokens_in = usage.get("prompt_tokens") or 0
            tokens_out = usage.get("completion_tokens") or 0
            engine_base.log_api_call("analysis", model, tokens_in, tokens_out)
            return text
        elif resp.status_code in (401, 403):
            raise AnalysisError("分析用的钥匙（API Key）不对或已失效，请到设置页重新填写")
        elif resp.status_code == 429:
            last_err = Exception("太频繁")
            continue
        else:
            if resp.status_code < 500:
                raise AnalysisError("分析用的模型暂时出了点问题，请稍后再试")
            last_err = Exception(str(resp.status_code))

    if isinstance(last_err, requests.exceptions.RequestException):
        raise AnalysisError(engine_base.friendly_error(last_err, "分析用模型"))
    raise AnalysisError("分析用的模型那边出了点状况，请稍后再试")
