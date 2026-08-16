"""腾讯云联网搜索 API（SearchPro）：为元宝联网档提供结构化信源（2026-08-16）。

背景：元宝 TokenHub 的 chat/completions 接口即使带上 search_info/citation
等参数，响应里也没有任何来源字段。按腾讯云官方文档
（https://cloud.tencent.com/document/api/1806/121811）改用独立的「联网搜索API」：
POST https://wsa.tencentcloudapi.com （Action=SearchPro，Version=2025-05-08，
TC3-HMAC-SHA256 签名），返回 Pages（JSON 字符串数组：title/url/date/passage/site…），
归一化为系统标准信源 [{title,url,domain,category}]。

凭据：需要腾讯云账号开通「联网搜索API」，并在 config.yaml engines.yuanbao 下填
wsa_secret_id / wsa_secret_key（腾讯云 SecretId/SecretKey，与 TokenHub 的 sk- 钥匙不同）。
未配置时静默跳过（元宝联网档无信源，不影响回答）。
"""

import hashlib
import hmac
import json
import time

import requests

from geo.analyzers import sources as sources_mod

_ENDPOINT = "https://wsa.tencentcloudapi.com"
_HOST = "wsa.tencentcloudapi.com"
_SERVICE = "wsa"
_ACTION = "SearchPro"
_VERSION = "2025-05-08"


class WsaError(Exception):
    """SearchPro 接口错误（未开通/限流/参数错等），调用方按「无信源」降级。"""


def _tc3_authorization(secret_id: str, secret_key: str, payload_str: str,
                       timestamp: int) -> str:
    """腾讯云 API 3.0 TC3-HMAC-SHA256 签名 → Authorization 头。"""
    date = time.strftime("%Y-%m-%d", time.gmtime(timestamp))

    def _hmac(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    canonical_headers = f"content-type:application/json; charset=utf-8\nhost:{_HOST}\n"
    signed_headers = "content-type;host"
    hashed_payload = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()
    canonical_request = "\n".join([
        "POST", "/", "",
        canonical_headers,
        signed_headers,
        hashed_payload,
    ])
    credential_scope = f"{date}/{_SERVICE}/tc3_request"
    string_to_sign = "\n".join([
        "TC3-HMAC-SHA256",
        str(timestamp),
        credential_scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])
    secret_date = _hmac(("TC3" + secret_key).encode("utf-8"), date)
    secret_service = _hmac(secret_date, _SERVICE)
    secret_signing = _hmac(secret_service, "tc3_request")
    signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"),
                         hashlib.sha256).hexdigest()
    return (f"TC3-HMAC-SHA256 Credential={secret_id}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}")


def search(query: str, secret_id: str, secret_key: str, timeout: int = 20) -> list:
    """SearchPro 联网搜索 → 标准信源列表 [{title,url,domain,category}]；失败抛 WsaError。"""
    query = (query or "").strip()
    if not query:
        return []
    timestamp = int(time.time())
    payload_str = json.dumps({"Query": query, "Mode": 0}, ensure_ascii=False)
    headers = {
        "Authorization": _tc3_authorization(secret_id, secret_key, payload_str,
                                            timestamp),
        "Content-Type": "application/json; charset=utf-8",
        "Host": _HOST,
        "X-TC-Action": _ACTION,
        "X-TC-Version": _VERSION,
        "X-TC-Timestamp": str(timestamp),
    }
    try:
        resp = requests.post(_ENDPOINT, data=payload_str.encode("utf-8"),
                             headers=headers, timeout=timeout)
    except requests.exceptions.RequestException as e:
        raise WsaError(f"联网搜索接口请求失败：{e}")
    try:
        data = resp.json()
    except Exception:
        raise WsaError("联网搜索接口返回格式不对")
    body = data.get("Response") or {}
    if body.get("Error"):
        err = body["Error"]
        raise WsaError(f"{err.get('Code') or '错误'}：{err.get('Message') or '未知原因'}")
    raw = []
    for page in body.get("Pages") or []:
        if isinstance(page, str):
            try:
                page = json.loads(page)
            except Exception:
                continue
        if not isinstance(page, dict) or not page.get("url"):
            continue
        raw.append({
            "url": str(page["url"]).strip(),
            "title": str(page.get("title") or page.get("passage") or "").strip(),
        })
    return sources_mod.normalize_sources(raw)
