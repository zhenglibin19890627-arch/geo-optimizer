# -*- coding: utf-8 -*-
"""回归抽查：界面可见文案禁词扫描。"""
import io, os, re, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

root = r"C:\Users\zlb19\Desktop\GEO\static"
banned = ["token", "api key", "endpoint", "接口", "数据库", "SQL", "模型名", "HTTP", "JSON", "console"]
hits = []
for dirpath, dirs, files in os.walk(root):
    for fn in files:
        if not fn.endswith((".html", ".js", ".css")):
            continue
        path = os.path.join(dirpath, fn)
        with open(path, encoding="utf-8", errors="ignore") as f:
            content = f.read()
        for i, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
                continue
            low = stripped.lower()
            for b in banned:
                if b.lower() in low:
                    if re.search(r"['\"\u2018\u2019\u201c\u201d\u4e00-\u9fff]", stripped):
                        hits.append((fn, i, b, stripped[:120]))
for h in hits[:50]:
    print(h)
print("TOTAL:", len(hits))
