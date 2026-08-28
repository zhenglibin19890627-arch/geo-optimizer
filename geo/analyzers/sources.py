"""引用信源解析与归类（规则法，无需钥匙）。"""

import re

_URL_RE = re.compile(r"https?://[^\s<>'\"，。；、（）()\[\]【】「」]+")
_TRAIL_RE = re.compile(r"[.,;:!?，。；：！？、\)\]]+$")

# 域名关键词 → 信源类别（大白话）
_DOMAIN_RULES = [
    (["gov.cn", "gov.", "mil.", "政府"], "政府网站"),
    (["edu.cn", "edu.", "ac.cn", "大学", "学校"], "教育机构"),
    (["wikipedia", "zh.wikipedia", "百度百科", "baike.baidu"], "百科网站"),
    (["zhihu", "知乎", "douban", "豆瓣", "bilibili", "哔哩哔哩", "weibo", "微博",
      "xiaohongshu", "小红书", "reddit", "quora", "贴吧", "tieba"], "社区/问答平台"),
    (["cnblogs", "csdn", "juejin", "掘金", "medium", "blog."], "个人/技术博客"),
    (["xinlang", "sina", "163.com", "netease", "sohu", "qq.com", "people.com", "新华网",
      "xinhuanet", "澎湃", "thepaper", "36kr", "36氪", "huxiu", "虎嗅", "ifeng",
      "凤凰网", "techweb", "cnbeta", "ithome", "新浪", "网易", "腾讯新闻", "搜狐"], "新闻媒体"),
    (["org", "ngo"], "公益/组织网站"),
    ([".com.cn", ".cn"], "企业/机构网站"),
]


def classify_domain(domain: str) -> str:
    domain = domain.lower()
    for keywords, label in _DOMAIN_RULES:
        if any(k.lower() in domain for k in keywords):
            return label
    return "其他网站"


# 域名前缀 → 网站中文名（显示用，如 m.zhipin.com → BOSS直聘）。顺序即优先级，具体在前。
# 2026-08-27 扩充：按实际监测数据里出现的高频信源补录（招聘/招标/企业信用/黄页类为主）。
_SITE_NAMES = [
    ("baike.baidu.com", "百度百科"),
    ("aiqicha.baidu.com", "爱企查"),
    ("zhipin.com", "BOSS直聘"),
    ("kanzhun.com", "看准网"),
    ("liepin.com", "猎聘"),
    ("lagou.com", "拉勾招聘"),
    ("51job.com", "前程无忧"),
    ("zhaopin.com", "智联招聘"),
    ("58.com", "58同城"),
    ("qcc.com", "企查查"),
    ("tianyancha.com", "天眼查"),
    ("qixin.com", "启信宝"),
    ("shuidi.cn", "水滴信用"),
    ("qiyeku.cn", "企业库"),
    ("11467.com", "顺企网"),
    ("yellowurl.cn", "黄页88"),
    ("7788.com", "7788商城（收藏/二手）"),
    ("maigoo.com", "买购网（十大品牌）"),
    ("ihchina.cn", "中国非物质文化遗产网"),
    ("cbi360.net", "建筑网（cbi360）"),
    ("qianlima.com", "千里马招标网"),
    ("bidizhaobiao.com", "必得招标"),
    ("bidcenter.com.cn", "采购与招标网"),
    ("biaozhaozhao.com", "标招招"),
    ("chinabidding.cn", "中国招标网"),
    ("chinabidding.com.cn", "中国采购与招标网"),
    ("lqzfqc.com", "龙泉政府网（汽车产业）"),
    ("dav01.com", "DAV音响（迪士普）"),
    ("10jqka.com.cn", "同花顺财经"),
    ("360.cn", "360"),
    ("cctv.com", "央视网"),
    ("cnr.cn", "央广网"),
    ("smzdm.com", "什么值得买"),
    ("meipian.cn", "美篇"),
    ("php.cn", "PHP中文网"),
    ("aitop100.cn", "人工智能排行榜"),
    ("liebiao.com", "猎豹 WiFi"),
    ("navtool.cn", "导航工具网"),
    ("zhihu.com", "知乎"),
    ("xiaohongshu.com", "小红书"),
    ("douban.com", "豆瓣"),
    ("bilibili.com", "哔哩哔哩"),
    ("weibo.com", "微博"),
    ("csdn.net", "CSDN"),
    ("cnblogs.com", "博客园"),
    ("juejin.cn", "掘金"),
    ("segmentfault.com", "思否"),
    ("github.com", "GitHub"),
    ("gitee.com", "Gitee"),
    ("stackoverflow.com", "Stack Overflow"),
    ("163.com", "网易"),
    ("qq.com", "腾讯网"),
    ("sina.com.cn", "新浪"),
    ("sohu.com", "搜狐"),
    ("people.com.cn", "人民网"),
    ("xinhuanet.com", "新华网"),
    ("thepaper.cn", "澎湃新闻"),
    ("36kr.com", "36氪"),
    ("huxiu.com", "虎嗅"),
    ("ithome.com", "IT之家"),
    ("wikipedia.org", "维基百科"),
    ("reddit.com", "Reddit"),
    ("quora.com", "Quora"),
    ("baidu.com", "百度"),
    ("tencent.com", "腾讯"),
    ("aliyun.com", "阿里云"),
    ("jd.com", "京东"),
    ("tmall.com", "天猫"),
    ("taobao.com", "淘宝"),
    ("weixin.qq.com", "微信"),
    ("mp.weixin.qq.com", "微信公众号"),
    ("gov.cn", "政府网站"),
    ("edu.cn", "教育机构网站"),
]


def normalize_domain(domain: str) -> str:
    """域名归一化：小写、去掉 www./m./mobile./wap. 等前缀，用于同站合并。"""
    d = (domain or "").strip().lower()
    changed = True
    while changed:
        changed = False
        for prefix in ("www.", "m.", "mobile.", "wap."):
            if d.startswith(prefix):
                d = d[len(prefix):]
                changed = True
                break
    return d


def site_name(domain: str) -> str:
    """域名 → 网站中文名（如 m.zhipin.com → BOSS直聘）。

    未收录时的兜底（2026-08-27）：不再显示整个裸域名（子域名常常一长串），
    而是取主域名的可读形式——首段有意义的（如 seo.shuidi.cn 的 shuidi）
    显示「Shuidi」，纯数字/单字母的（如 7788.com）原样显示主域名。
    """
    d = normalize_domain(domain)
    for key, name in _SITE_NAMES:
        if key in d:
            return name
    # 兜底：主域名 = 去掉子域后的最后两段（如 seo.shuidi.cn → shuidi.cn）
    parts = d.split(".")
    main = ".".join(parts[-2:]) if len(parts) >= 2 else d
    first = main.split(".")[0]
    if not first:
        return d
    # 纯数字/单字符没有可读性，直接显示主域名
    if first.isdigit() or len(first) <= 1:
        return main
    # 首字母大写（英文站名习惯）；中文域名原样
    readable = first[:1].upper() + first[1:]
    return readable


def extract_urls(text: str) -> list:
    """从回答中提取引用链接（保序去重）。"""
    if not text:
        return []
    seen = set()
    result = []
    for m in _URL_RE.finditer(text):
        url = _TRAIL_RE.sub("", m.group(0))
        if not url:
            continue
        key = url.rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        result.append(url)
    return result


def _domain_of(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url)
    return m.group(1) if m else url


def parse_sources(text: str) -> list:
    """[{title, url, domain, site_name, category}]。title 用“域名 + 路径最后一段”做可读说明。"""
    sources = []
    for url in extract_urls(text):
        domain = _domain_of(url)
        path = url.split("://", 1)[-1]
        path = path.split("/", 1)[1] if "/" in path else ""
        seg = [s for s in path.split("/") if s and len(s) > 2]
        title = domain
        if seg:
            title = seg[-1][:30]
        sources.append({
            "title": title,
            "url": url,
            "domain": domain,
            "site_name": site_name(domain),
            "category": classify_domain(domain),
        })
    return sources


def normalize_sources(raw: list) -> list:
    """把引擎返回的结构化引用（各家字段名不一）统一成标准信源字段。

    输入可以是 [{title,url,...}] / [{link,...}] / [{site_name,...}] / 纯字符串 URL；
    输出 [{title, url, domain, category}]，与 parse_sources 口径一致（保序去重）。
    """
    if not raw:
        return []
    seen = set()
    result = []
    for item in raw:
        if isinstance(item, str):
            url = item.strip()
            title = None
        elif isinstance(item, dict):
            url = str(item.get("url") or item.get("link") or
                      item.get("source") or "").strip()
            title = str(item.get("title") or item.get("site_name") or
                        item.get("name") or "").strip() or None
        else:
            continue
        if not url.startswith(("http://", "https://")):
            continue
        key = url.rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        domain = _domain_of(url)
        result.append({
            "title": title or domain,
            "url": url,
            "domain": domain,
            "site_name": site_name(domain),
            "category": classify_domain(domain),
        })
    return result
