"""GEO 优化系统：预置数据初始化（问题库模板）。

当前状态（2026-08-13 整理）：
- 预置问题自动写入已停用（C1 裁决，2026-08-10）：新装首启不再自动写入预置问题，
  问题库由关键词扩展/手动添加产生；存量"预置"来源问题原样保留、不受影响。
- seed_questions() 保留空实现（create_app 仍调用，保持零改动）。
- question_templates.yaml 保留为备用素材：如将来恢复预置写入，在此函数内用
  pathlib.Path(__file__).with_name("question_templates.yaml") 读取并逐条写入即可
  （旧版 __file__.rsplit("\\", 1) 的 Windows 路径拼接已移除）。
"""

from geo.models import db as database


def seed_questions():
    """空实现（预置写入已按 C1 裁决停用），create_app 调用点保持不变。"""
    return
