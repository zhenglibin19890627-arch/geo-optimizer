import os, re

ROOT = r"C:\Users\zlb19\Desktop\GEO\static"
# 只检查“用户能看到”的文案：HTML 文本节点 + JS/CSS 中含中文的字符串行
files = []
for dirpath, dirnames, filenames in os.walk(ROOT):
    for fn in filenames:
        if fn.endswith((".html", ".js", ".css")) and "echarts.min.js" not in fn:
            files.append(os.path.join(dirpath, fn))

issues = []

def scan_text(f, text):
    for pat, label in [
        (r"\btoken[s]?\b", "token"),
        (r"\bendpoint[s]?\b", "endpoint"),
        (r"\bSQL\b", "SQL"),
        (r"\bAPI\b(?!\s*Key)", "裸API"),
        (r"接口", "接口"),
        (r"数据库", "数据库"),
        (r"报错堆栈", "报错堆栈"),
        (r"异常码", "异常码"),
        (r"kimi-k3|deepseek-reasoner|doubao-1-5-lite|qwen-plus|qwen-max|qwen-turbo|hunyuan-turbos|hunyuan-turbo|hunyuan-lite|moonshot-v1|kimi-k2-turbo", "模型名"),
    ]:
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            line_no = text[:m.start()].count("\n") + 1
            ctx = text[max(0, m.start()-30):m.end()+30].replace("\n", " ")
            issues.append((f, label, line_no, m.group(0), ctx))

for f in files:
    with open(f, "r", encoding="utf-8", errors="replace") as fh:
        content = fh.read()
    if f.endswith(".html"):
        # 提取 HTML 中用户可见文本（去掉 script/style 块和属性）
        body = re.sub(r"<script.*?</script>|<style.*?</style>", "", content, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", body)
        scan_text(f, text)
        # 属性里的 placeholder/title 也是用户可见
        for m in re.finditer(r'(placeholder|title|aria-label)="([^"]+)"', content):
            scan_text(f, m.group(2))
    else:
        # JS/CSS：只扫含中文的行（界面文案字符串）
        for i, line in enumerate(content.splitlines(), 1):
            if re.search(r"[\u4e00-\u9fff]", line):
                scan_text(f, line)

if issues:
    for f, label, ln, matched, ctx in issues:
        print(f"[命中] {os.path.relpath(f, ROOT)}:{ln} ({label}) matched={matched!r} ctx=...{ctx}...")
    print(f"\n共 {len(issues)} 处（仅限界面可见文案）")
else:
    print("界面可见文案禁词扫描：0 命中")

print()
print("--- 裸 API Key 写法（用户可见处）---")
bad = 0
ok = 0
for f in files:
    with open(f, "r", encoding="utf-8", errors="replace") as fh:
        content = fh.read()
    for m in re.finditer(r"API Key", content):
        if "钥匙（API Key）" in content[max(0, m.start()-8):m.end()+8]:
            ok += 1
        else:
            line_no = content[:m.start()].count("\n") + 1
            ctx = content[max(0, m.start()-25):m.end()+25].replace("\n", " ")
            if re.search(r"[\u4e00-\u9fff]", ctx):
                bad += 1
                print(f"[裸API Key] {os.path.relpath(f, ROOT)}:{line_no} ctx=...{ctx}...")
print(f"合规写法(钥匙（API Key）) {ok} 处；用户可见裸 API Key {bad} 处")
