"""网页抓取：robots.txt 检查、浏览器 UA、超时、5 万字符截断（只抓公开网页）。"""

import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from geo import config


class FetchError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class RobotsTxt:
    """极简 robots.txt 解析：只关心“是否允许抓取某个路径”。"""

    def __init__(self, text: str):
        self._disallows = []  # (agents, [paths])
        self._allows = []
        agents = None
        paths = []
        for line in (text or "").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            low = line.lower()
            if low.startswith("user-agent"):
                if agents is not None:
                    self._disallows.append((agents, paths))
                    paths = []
                agents = low.split(":", 1)[1].strip()
            elif low.startswith("disallow"):
                paths.append(line.split(":", 1)[1].strip() or "/")
            elif low.startswith("allow"):
                self._allows.append((agents, line.split(":", 1)[1].strip()))
        if agents is not None:
            self._disallows.append((agents, paths))

    def can_fetch(self, ua_token: str, path: str) -> bool:
        """是否允许抓取；对 UA token 不区分大小写。"""
        for allow_agents, allow_path in self._allows:
            if self._match(allow_agents, ua_token) and path.startswith(allow_path):
                return True
        for agents, paths in self._disallows:
            if not self._match(agents, ua_token):
                continue
            for p in paths:
                if path.startswith(p):
                    return False
        return True

    @staticmethod
    def _match(rule_agents: str, ua_token: str) -> bool:
        rule_agents = (rule_agents or "").strip().lower()
        ua_token = (ua_token or "").lower()
        if rule_agents in ("*", ""):
            return True
        return rule_agents in ua_token


def _fetch_robots(origin: str, ua: str) -> RobotsTxt:
    try:
        resp = requests.get(f"{origin}/robots.txt", headers={"User-Agent": ua},
                            timeout=5, allow_redirects=True)
        if resp.status_code == 200 and "text" in resp.headers.get("Content-Type", ""):
            return RobotsTxt(resp.text[:20000])
    except Exception:
        pass
    return RobotsTxt("")


def fetch_page(url: str) -> dict:
    """抓取公开网页正文。返回 {text, title}；失败抛 FetchError（大白话）。"""
    fetch_cfg = config.get_section("fetch", {})
    timeout = int(fetch_cfg.get("timeout_seconds", 10) or 10)
    max_chars = int(fetch_cfg.get("max_chars", 50000) or 50000)
    ua = str(fetch_cfg.get("user_agent", "")).strip() or "Mozilla/5.0"

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise FetchError("这个链接格式不太对，请检查是不是完整的网址（以 http:// 或 https:// 开头）")

    robots = _fetch_robots(f"{parsed.scheme}://{parsed.netloc}", ua)
    if not robots.can_fetch(ua, parsed.path or "/"):
        raise FetchError("这个网站设置了禁止程序自动读取内容（robots.txt 禁止抓取），"
                         "请改用「粘贴文字」的方式提交内容")

    try:
        resp = requests.get(url, headers={"User-Agent": ua},
                            timeout=timeout, allow_redirects=True)
    except requests.exceptions.Timeout:
        raise FetchError("网页打开超时了（可能网络慢或网站较卡），可以稍后再试，或改用「粘贴文字」")
    except requests.exceptions.RequestException:
        raise FetchError("网页打开失败（可能网站暂时打不开或网络有问题），请改用「粘贴文字」")

    if resp.status_code != 200:
        raise FetchError("网页打不开，请确认链接是否正确，或改用「粘贴文字」")
    ctype = resp.headers.get("Content-Type", "") or ""
    if "html" not in ctype.lower():
        raise FetchError("这个链接不是普通网页（可能是文件下载链接），请改用「粘贴文字」")

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "iframe", "svg", "form"]):
        tag.decompose()
    title = soup.title.get_text(strip=True) if soup.title else ""
    text = soup.get_text(separator="\n") if soup.body else soup.get_text(separator="\n")
    text = re.sub(r"[ \t\r\u3000]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars]
    if not text:
        raise FetchError("网页内容为空（可能是动态页面，程序读不到），请改用「粘贴文字」")
    return {"text": text, "title": title}
