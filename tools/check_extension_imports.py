# -*- coding: utf-8 -*-
"""扩展 JS 模块导入/导出静态核验：每个相对导入的命名绑定必须在目标文件中 export。
用法：python tools/check_extension_imports.py"""
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "extension")
errors = []
# 匹配: import { a, b as c } from "./x.js" / import x, { y } from "./x.js"
IMPORT_RE = re.compile(r"""import\s+([^;'"]+?)\s*from\s*['"](\.[^'"]+)['"]""")

for dirpath, _dirs, files in os.walk(ROOT):
    for name in files:
        if not name.endswith(".js"):
            continue
        path = os.path.join(dirpath, name)
        with open(path, encoding="utf-8") as f:
            src = f.read()
        for m in IMPORT_RE.finditer(src):
            binding_part, rel = m.group(1), m.group(2)
            target = os.path.normpath(os.path.join(dirpath, rel))
            if not os.path.exists(target):
                errors.append(f"{os.path.relpath(path, ROOT)}: 目标不存在 {rel}")
                continue
            with open(target, encoding="utf-8") as tf:
                tsrc = tf.read()
            exported = set(re.findall(r"export\s+(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", tsrc))
            exported |= set(re.findall(r"export\s+(?:const|let|var)\s+([A-Za-z_$][\w$]*)", tsrc))
            for chunk in re.findall(r"export\s*\{([^}]+)\}", tsrc):
                for item in chunk.split(","):
                    exported.add(item.split(" as ")[-1].strip())
            for brace in re.findall(r"\{([^}]*)\}", binding_part):
                for item in brace.split(","):
                    item = item.strip()
                    if not item:
                        continue
                    bound = item.split(" as ")[-1].strip()
                    if bound not in exported:
                        errors.append(
                            f"{os.path.relpath(path, ROOT)}: 导入的 {bound} 未在 {rel} 中导出")

if errors:
    print("发现问题:")
    for e in errors:
        print(" -", e)
    sys.exit(1)
print("全部导入/导出核验通过")
