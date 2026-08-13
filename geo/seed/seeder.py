"""GEO 优化系统：预置数据初始化（问题库模板）。"""

import yaml

from geo.models import db as database

TEMPLATES_FILE = __file__.rsplit("\\", 1)[0] + "\\question_templates.yaml"


def _load_templates() -> list:
    try:
        with open(TEMPLATES_FILE, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        items = []
        for cat, texts in (data.get("categories") or {}).items():
            for t in texts or []:
                items.append({"text": t, "category": cat})
        return items
    except Exception:
        return []


def seed_questions():
    """预置问题自动写入已停用（C1 裁决，2026-08-10）：新装首启不再自动写入预置问题，
    问题库由关键词扩展/手动添加产生；存量"预置"来源问题原样保留、不受影响。
    函数保留空实现（create_app 仍调用，保持零改动），模板文件与读取函数一并保留备用。
    """
    return
