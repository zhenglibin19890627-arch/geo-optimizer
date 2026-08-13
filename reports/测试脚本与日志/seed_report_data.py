import sqlite3, json, datetime

DB = r"C:\Users\zlb19\Desktop\GEO\data\geo.db"
con = sqlite3.connect(DB)
con.text_factory = str
cur = con.cursor()

# 记录种子数据 ID 范围，便于清理
start_ids = {t: (cur.execute(f"SELECT MAX(id) FROM {t}").fetchone()[0] or 0) for t in
             ["monitor_round", "monitor_result", "score_snapshot", "alert"]}
print("seed 前各表最大 id:", start_ids)

now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# 3 轮 × 2 引擎（deepseek + yuanbao，验证元宝口径说明）
brand = "星辰母婴"
comps = ["好孩子", "贝亲"]
results_spec = [
    # round 1：好评、提及、信源
    [
        ("deepseek", "婴儿推车什么牌子好？", f"婴儿推车推荐{brand}的X系列，性价比高、口碑好。信息来源：https://www.zhihu.com/question/1 和 https://post.smzdm.com/p/123456 的内容。", True, 2, 1, "positive",
         json.dumps([{"title": "a", "url": "https://www.zhihu.com/question/1", "domain": "www.zhihu.com", "category": "社区/问答平台"},
                     {"title": "b", "url": "https://post.smzdm.com/p/123456", "domain": "post.smzdm.com", "category": "社区/问答平台"}], ensure_ascii=False),
         json.dumps([{"name": "好孩子", "count": 1, "position": 2}], ensure_ascii=False)),
        ("yuanbao", "婴儿推车什么牌子好？", f"市面上{brand}和好孩子都不错，{brand}轻便。来源：https://baike.baidu.com/item/婴儿推车", True, 2, 1, "positive",
         json.dumps([{"title": "c", "url": "https://baike.baidu.com/item/婴儿推车", "domain": "baike.baidu.com", "category": "百科网站"}], ensure_ascii=False),
         json.dumps([{"name": "好孩子", "count": 1, "position": 1}], ensure_ascii=False)),
    ],
    # round 2：中性、仍提及
    [
        ("deepseek", "如何选择婴儿推车？", f"选购时注意避震和材质，{brand}是常见选择之一。来源：https://www.zhihu.com/question/1", True, 1, 1, "neutral",
         json.dumps([{"title": "a", "url": "https://www.zhihu.com/question/1", "domain": "www.zhihu.com", "category": "社区/问答平台"}], ensure_ascii=False),
         json.dumps([{"name": "贝亲", "count": 1, "position": 1}], ensure_ascii=False)),
        ("yuanbao", "如何选择婴儿推车？", "选择推车主要看安全认证。", False, 0, None, "neutral",
         json.dumps([], ensure_ascii=False), json.dumps([], ensure_ascii=False)),
    ],
    # round 3：负面、提及
    [
        ("deepseek", "婴儿推车什么牌子好？", f"不推荐{brand}，有用户反馈质量差、售后差。来源：https://www.zhihu.com/question/2", True, 1, 1, "negative",
         json.dumps([{"title": "d", "url": "https://www.zhihu.com/question/2", "domain": "www.zhihu.com", "category": "社区/问答平台"}], ensure_ascii=False),
         json.dumps([], ensure_ascii=False)),
        ("yuanbao", "婴儿推车什么牌子好？", f"{brand}有一定知名度，但建议多方对比。", True, 1, 1, "neutral",
         json.dumps([], ensure_ascii=False), json.dumps([], ensure_ascii=False)),
    ],
]

round_ids = []
for ri, specs in enumerate(results_spec, 1):
    # 模拟指标：按答案算
    answered = len(specs)
    mentioned = sum(1 for s in specs if s[3])
    pos = sum(1 for s in specs if s[6] == "positive")
    neg = sum(1 for s in specs if s[6] == "negative")
    mr = mentioned / answered
    ns = (pos - neg) / answered
    score = 30 + int(mr * 40) + int((ns + 1) / 2 * 20)  # 简化示意分
    cur.execute(
        "INSERT INTO monitor_round (task_id, mention_rate, net_sentiment, overall_score, summary, created_at, finished_at) VALUES (NULL, ?, ?, ?, ?, ?, ?)",
        (mr, ns, score, json.dumps({"per_engine": {}, "total_answers": answered, "mentioned_answers": mentioned}, ensure_ascii=False), now, now))
    round_ids.append(cur.lastrowid)
    for (eng, q, ans, ism, mc, posn, sent, srcs, comps_json) in specs:
        cur.execute(
            "INSERT INTO monitor_result (round_id, engine_code, question_id, question_text, answer_text, is_mentioned, mention_count, mention_position, sentiment, sources, competitor_mentions, input_mode, created_at) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, 'auto', ?)",
            (round_ids[-1], eng, q, ans, 1 if ism else 0, mc, posn, sent, srcs, comps_json, now))
    # 评分快照
    cur.execute("INSERT INTO score_snapshot (round_id, score, breakdown, created_at) VALUES (?, ?, ?, ?)",
                (round_ids[-1], score, json.dumps({"mention_cover": {"score": mr*40}, "total": score}, ensure_ascii=False), now))

con.commit()
end_ids = {t: (cur.execute(f"SELECT MAX(id) FROM {t}").fetchone()[0] or 0) for t in
           ["monitor_round", "monitor_result", "score_snapshot", "alert"]}
print("seed 后最大 id:", end_ids)
print("种子轮次 ids:", round_ids)
print("清理范围（id 大于 seed 前最大值）:", start_ids)
con.close()
