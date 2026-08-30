"""正文净化：生成的稿件不得带网址链接（搜狐等平台审核会拒）。"""
import re

_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_BARE_DOMAIN_RE = re.compile(
    r"(?<![\w.@])(?:[a-z0-9-]+\.)+(?:com|cn|net|org|top|xyz|vip|club|shop|site|online|tech|io|cc|me|tv|info|biz|edu|gov)(?:/[^\s，。；、）】》\"']*)?",
    re.IGNORECASE)


def strip_links(text: str) -> str:
    """去掉正文里的各种链接：markdown 链接保留文字，http(s)/www/裸域名整段删除。"""
    if not text:
        return text or ""
    t = _MD_LINK_RE.sub(r"\1", text)
    t = _URL_RE.sub("", t)
    t = _BARE_DOMAIN_RE.sub("", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()
