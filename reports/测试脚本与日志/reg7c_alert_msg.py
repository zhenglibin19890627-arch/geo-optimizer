# -*- coding: utf-8 -*-
"""回归#7c：预警恢复文案精度（纯函数验证）。"""
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"C:\Users\zlb19\Desktop\GEO")

from geo.analyzers import alerting

results = []
def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + ((" | " + detail) if detail else ""))

# mention_rate 0.6 -> 1.0：应显示 "从 60% 回到了 100%"
m = alerting._msg("mention_drop", 1.0, 0.6, recovered=True)
check("mention 恢复 0.6->1.0 显示百分比", "从 60% 回到了 100%" in m, m)

# net_sentiment -0.1 -> 0.2：应显示 "从 -10% 回到了 20%"
m = alerting._msg("sentiment_drop", 0.2, -0.1, recovered=True)
check("sentiment 恢复 -0.1->0.2 显示百分比", "从 -10% 回到了 20%" in m, m)

# score 80 -> 85：应显示 "从 80 分回到了 85 分"
m = alerting._msg("score_drop", 85, 80, recovered=True)
check("score 恢复 80->85 显示分", "从 80 分回到了 85 分" in m, m)

# 旧缺陷场景回归：0.6->1.0 不再出现 "从 1 回到了 1"
m = alerting._msg("mention_drop", 1.0, 0.6, recovered=True)
check("无取整失真（不再出现 从 1 回到 1）", "从 1 回到了 1" not in m, m)

# 降级文案（未恢复）口径不受影响
m = alerting._msg("sentiment_drop", 0.1, 0.5)
check("降级文案仍为百分比", "净情感率 50%" in m, m)
m = alerting._msg("score_drop", 55, 70)
check("评分下滑文案仍为分", "平均 70 分" in m and "这轮只有 55 分" in m, m)

print()
print("SUMMARY:", sum(1 for x in results if x[1]), "/", len(results), "PASS")
for n, ok in results:
    if not ok:
        print("FAILED:", n)
