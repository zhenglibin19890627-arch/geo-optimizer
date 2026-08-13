"""引用信源解析纯函数单测（geo/analyzers/sources.py）。"""

from geo.analyzers.sources import (classify_domain, extract_urls,
                                   normalize_domain, normalize_sources,
                                   parse_sources, site_name)


def test_classify_domain():
    assert classify_domain("www.gov.cn") == "政府网站"
    assert classify_domain("edu.cn") == "教育机构"
    assert classify_domain("zhihu.com") == "社区/问答平台"
    assert classify_domain("sohu.com") == "新闻媒体"
    assert classify_domain("wikipedia.org") == "百科网站"
    assert classify_domain("example.com") == "其他网站"


def test_normalize_domain():
    assert normalize_domain("WWW.Example.COM") == "example.com"
    assert normalize_domain("m.zhipin.com") == "zhipin.com"
    assert normalize_domain("wap.163.com") == "163.com"
    assert normalize_domain("") == ""


def test_site_name():
    assert site_name("m.zhipin.com") == "BOSS直聘"
    assert site_name("www.zhihu.com") == "知乎"
    assert site_name("unknown-site.cn") == "unknown-site.cn"


def test_extract_urls():
    text = "来源：https://a.com/x 和 https://a.com/x 以及 http://b.cn?q=1。"
    urls = extract_urls(text)
    assert urls == ["https://a.com/x", "http://b.cn?q=1"]
    assert extract_urls("无链接") == []
    assert extract_urls("") == []


def test_parse_sources():
    r = parse_sources("参考 https://www.zhihu.com/question/123 的内容")
    assert len(r) == 1
    assert r[0]["domain"] == "www.zhihu.com"
    assert r[0]["site_name"] == "知乎"
    assert r[0]["category"] == "社区/问答平台"
    assert r[0]["url"] == "https://www.zhihu.com/question/123"


def test_normalize_sources_多种输入形态():
    raw = [
        "https://a.com/x",
        {"url": "https://b.cn/y", "title": "标题B"},
        {"link": "https://c.com/z"},
        {"url": "https://a.com/x"},          # 重复，去重
        {"url": "not-a-url"},                # 非 http，过滤
        "https://d.com",
    ]
    r = normalize_sources(raw)
    assert [x["url"] for x in r] == [
        "https://a.com/x", "https://b.cn/y", "https://c.com/z", "https://d.com"
    ]
    assert r[1]["title"] == "标题B"
    assert normalize_sources([]) == []
    assert normalize_sources(None) == []
