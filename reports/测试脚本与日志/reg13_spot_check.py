# -*- coding: utf-8 -*-
"""回归#13：抽查真实监测结果与 AI 实际回答的一致性（人工交叉验证辅助）。"""
import io, json, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

brand_names = ["星辰母婴", "星辰", "星辰宝宝"]
competitors = ["好孩子", "贝亲"]

rows = []
with open(r"C:\Users\zlb19\Desktop\GEO\reports\测试脚本与日志\reg13_round_detail.json", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:
            rows.append(json.loads(line))

results = []
def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + ((" | " + detail) if detail else ""))

for item in rows:
    det = item["detail"]
    for res in det["results"]:
        ans = res["answer_text"] or ""
        # 实际文本中品牌出现次数 vs 解析值
        actual_brand = sum(ans.count(b) for b in brand_names)
        parsed_mentioned = res["is_mentioned"]
        parsed_count = res["mention_count"]
        consistent = (actual_brand > 0) == parsed_mentioned and actual_brand == parsed_count
        check(f"round{item['round_id']} 品牌提及一致性(实际{actual_brand}次 vs 解析{parsed_count}次/{parsed_mentioned})",
              consistent, f"text={ans[:120]}")
        # 竞品实际出现次数 vs 解析竞品明细
        cm = res.get("competitor_mentions") or []
        for c in competitors:
            actual_c = ans.count(c)
            parsed_c = sum(int(m.get("count") or 0) for m in cm if m.get("name") == c)
            check(f"round{item['round_id']} 竞品[{c}] 实际{actual_c}次 vs 解析{parsed_c}次",
                  actual_c == parsed_c, f"cm={json.dumps(cm, ensure_ascii=False)}")
        # 情感判定与文本基调人工核对（打印摘要）
        print(f"--- round{item['round_id']} 情感={res['sentiment']} 顺位={res.get('mention_position')} 信源={len(res.get('sources') or [])}个")
        print(f"    Q: {item['question']}")
        print(f"    A: {ans[:200]}...")

print()
print("SUMMARY:", sum(1 for x in results if x[1]), "/", len(results), "PASS")
for n, ok in results:
    if not ok:
        print("FAILED:", n)
