import sqlite3

DB = r"C:\Users\zlb19\Desktop\GEO\data\geo.db"
con = sqlite3.connect(DB)
cur = con.cursor()
# 清理种子数据：round id>1、result id>17、snapshot 全部
cur.execute("DELETE FROM monitor_round WHERE id > 1")
cur.execute("DELETE FROM monitor_result WHERE round_id > 1")
cur.execute("DELETE FROM score_snapshot")
con.commit()
print("清理后 round:", cur.execute("SELECT id,task_id,mention_rate,overall_score FROM monitor_round").fetchall())
print("清理后 result:", cur.execute("SELECT count(*) FROM monitor_result").fetchone())
print("清理后 snapshot:", cur.execute("SELECT count(*) FROM score_snapshot").fetchone())
con.close()
