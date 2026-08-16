"""GEO 优化系统：SQLAlchemy 数据模型（技术方案第三章，11 张表）。"""

import json
from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import (Boolean, Column, DateTime, Float, Integer, String, Text,
                        create_engine, event)
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from geo import config


def jdumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False) if obj is not None else None


def jloads(s, default=None):
    if not s:
        return default
    try:
        return json.loads(s)
    except Exception:
        return default


def now() -> datetime:
    return datetime.now()


class Base(DeclarativeBase):
    pass


def _init_db_engine():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    return create_engine(
        f"sqlite:///{config.DB_FILE.as_posix()}",
        connect_args={"check_same_thread": False},
    )


engine = _init_db_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    """WAL 模式 + 写锁等待超时：避免多连接并发写时报 database is locked。"""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=15000")
    cursor.close()


@contextmanager
def session_scope():
    """事务上下文：提交或回滚。"""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db():
    Base.metadata.create_all(engine)


# ============================================================
# 1. 品牌档案（多行：每个品牌一行；id=1 为存量威启品牌）
# ============================================================
class BrandProfile(Base):
    __tablename__ = "brand_profile"

    id = Column(Integer, primary_key=True, autoincrement=True)
    brand_name = Column(String(200))
    product_name = Column(String(200))
    brand_aliases = Column(Text)  # JSON 数组
    brand_description = Column(Text)
    competitors = Column(Text)  # JSON 数组
    auto_monitor = Column(Boolean, default=True)  # 参加定时自动监测开关
    updated_at = Column(DateTime)
    created_at = Column(DateTime, default=now)

    def to_dict(self):
        return {
            "id": self.id,
            "brand_name": self.brand_name or "",
            "product_name": self.product_name or "",
            "brand_aliases": jloads(self.brand_aliases, []) or [],
            "brand_description": self.brand_description or "",
            "competitors": jloads(self.competitors, []) or [],
            "auto_monitor": True if self.auto_monitor is None else bool(self.auto_monitor),
            "updated_at": self.updated_at.strftime("%Y-%m-%d %H:%M:%S") if self.updated_at else None,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else None,
        }


# ============================================================
# 2. 关键词
# ============================================================
class Keyword(Base):
    __tablename__ = "keyword"

    id = Column(Integer, primary_key=True, autoincrement=True)
    brand_id = Column(Integer, default=1)
    text = Column(Text)
    category = Column(String(100))
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=now)

    def to_dict(self):
        return {
            "id": self.id,
            "text": self.text,
            "category": self.category or "",
            "enabled": bool(self.enabled),
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else None,
        }


# ============================================================
# 3. 问题库
# ============================================================
class QuestionBank(Base):
    __tablename__ = "question_bank"

    id = Column(Integer, primary_key=True, autoincrement=True)
    brand_id = Column(Integer, default=1)
    text = Column(Text)
    category = Column(String(100))
    source = Column(String(20))  # preset=预置模板 / expanded=关键词扩展 / manual=手动添加
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=now)

    def to_dict(self):
        return {
            "id": self.id,
            "text": self.text,
            "category": self.category or "",
            "source": self.source or "manual",
            "enabled": bool(self.enabled),
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else None,
        }


# ============================================================
# 4. 监测任务
# ============================================================
class MonitorTask(Base):
    __tablename__ = "monitor_task"

    id = Column(Integer, primary_key=True, autoincrement=True)
    brand_id = Column(Integer, default=1)
    type = Column(String(20))  # manual=手动发起 / scheduled=定时自动
    status = Column(String(20))  # pending / running / done / failed / cancelled
    mode = Column(String(10), default="normal")  # normal=常规提问 / web=联网提问
    progress = Column(Integer, default=0)
    total_calls = Column(Integer, default=0)
    done_calls = Column(Integer, default=0)
    estimated_seconds = Column(Integer, default=0)
    question_ids = Column(Text)  # JSON 数组
    engine_codes = Column(Text)  # JSON 数组
    models = Column(Text)  # JSON 对象 {engine_code: [model, ...]}；空=每家引擎用当前档
    error_msg = Column(Text)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)
    created_at = Column(DateTime, default=now)

    def to_dict(self):
        return {
            "id": self.id,
            "type": self.type,
            "status": self.status,
            "progress": self.progress or 0,
            "total_calls": self.total_calls or 0,
            "done_calls": self.done_calls or 0,
            "estimated_seconds": self.estimated_seconds or 0,
            "question_ids": jloads(self.question_ids, []) or [],
            "engine_codes": jloads(self.engine_codes, []) or [],
            "models": jloads(self.models, {}) or {},
            "error_msg": self.error_msg or "",
            "mode": self.mode or "normal",
            "started_at": self.started_at.strftime("%Y-%m-%d %H:%M:%S") if self.started_at else None,
            "finished_at": self.finished_at.strftime("%Y-%m-%d %H:%M:%S") if self.finished_at else None,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else None,
        }


# ============================================================
# 5. 监测轮次
# ============================================================
class MonitorRound(Base):
    __tablename__ = "monitor_round"

    id = Column(Integer, primary_key=True, autoincrement=True)
    brand_id = Column(Integer, default=1)
    task_id = Column(Integer)
    mode = Column(String(10), default="normal")  # normal=常规提问 / web=联网提问
    mention_rate = Column(Float)
    net_sentiment = Column(Float)
    overall_score = Column(Integer)
    summary = Column(Text)  # JSON：各引擎提及情况、竞品对比摘要
    auto_competitors = Column(Text)  # JSON 数组：本轮自动提取的竞品品牌名（LLM）
    created_at = Column(DateTime, default=now)
    finished_at = Column(DateTime)

    def to_dict(self):
        return {
            "id": self.id,
            "task_id": self.task_id,
            "mode": self.mode or "normal",
            "mention_rate": self.mention_rate,
            "net_sentiment": self.net_sentiment,
            "overall_score": self.overall_score,
            "summary": jloads(self.summary, {}) or {},
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else None,
            "finished_at": self.finished_at.strftime("%Y-%m-%d %H:%M:%S") if self.finished_at else None,
        }


# ============================================================
# 6. 监测结果（单条：一家引擎 × 一个问题的回答分析）
# ============================================================
class MonitorResult(Base):
    __tablename__ = "monitor_result"

    id = Column(Integer, primary_key=True, autoincrement=True)
    brand_id = Column(Integer, default=1)
    round_id = Column(Integer)
    engine_code = Column(String(30))
    model = Column(String(100))  # 实际调用的模型名（同 key 多模型监测时区分档位）
    question_id = Column(Integer)
    question_text = Column(Text)
    answer_text = Column(Text)
    is_mentioned = Column(Boolean, default=False)
    mention_count = Column(Integer, default=0)
    mention_position = Column(Integer)
    sentiment = Column(String(20))  # positive / neutral / negative
    sources = Column(Text)  # JSON 数组 [{title, url, domain}]
    competitor_mentions = Column(Text)  # JSON 数组 [{name, count, position}]
    input_mode = Column(String(10))  # auto / paste
    error_msg = Column(Text)  # 调用失败原因（大白话），成功为空
    created_at = Column(DateTime, default=now)

    def to_dict(self):
        return {
            "id": self.id,
            "round_id": self.round_id,
            "engine_code": self.engine_code,
            "model": self.model or "",
            "question_id": self.question_id,
            "question_text": self.question_text,
            "answer_text": self.answer_text,
            "is_mentioned": bool(self.is_mentioned),
            "mention_count": self.mention_count or 0,
            "mention_position": self.mention_position,
            "sentiment": self.sentiment or "neutral",
            "sources": jloads(self.sources, []) or [],
            "competitor_mentions": jloads(self.competitor_mentions, []) or [],
            "input_mode": self.input_mode or "auto",
            "error_msg": self.error_msg or "",
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else None,
        }


# ============================================================
# 7. 内容优化记录
# ============================================================
class OptimizationRecord(Base):
    __tablename__ = "optimization_record"

    id = Column(Integer, primary_key=True, autoincrement=True)
    brand_id = Column(Integer, default=1)
    input_type = Column(String(10))  # url / text
    url = Column(Text)
    content = Column(Text)
    suggestions = Column(Text)  # JSON 数组 [{title, detail, priority}]
    geo_score = Column(Integer)
    status = Column(String(20))  # pending / running / done / failed
    error_msg = Column(Text)
    created_at = Column(DateTime, default=now)
    finished_at = Column(DateTime)

    def to_dict(self):
        return {
            "id": self.id,
            "input_type": self.input_type,
            "url": self.url or "",
            "content": self.content,
            "suggestions": jloads(self.suggestions, []) or [],
            "geo_score": self.geo_score,
            "status": self.status or "pending",
            "error_msg": self.error_msg or "",
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else None,
            "finished_at": self.finished_at.strftime("%Y-%m-%d %H:%M:%S") if self.finished_at else None,
        }


# ============================================================
# 8. 评分快照
# ============================================================
class ScoreSnapshot(Base):
    __tablename__ = "score_snapshot"

    id = Column(Integer, primary_key=True, autoincrement=True)
    brand_id = Column(Integer, default=1)
    round_id = Column(Integer)
    score = Column(Integer)
    breakdown = Column(Text)  # JSON
    created_at = Column(DateTime, default=now)

    def to_dict(self):
        return {
            "id": self.id,
            "round_id": self.round_id,
            "score": self.score,
            "breakdown": jloads(self.breakdown, {}) or {},
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else None,
        }


# ============================================================
# 9. 预警
# ============================================================
class Alert(Base):
    __tablename__ = "alert"

    id = Column(Integer, primary_key=True, autoincrement=True)
    brand_id = Column(Integer, default=1)
    round_id = Column(Integer)
    alert_type = Column(String(30))  # mention_drop / sentiment_drop / score_drop
    level = Column(String(10))  # watch=观察中 / warning=正式预警
    message = Column(Text)
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=now)

    def to_dict(self):
        return {
            "id": self.id,
            "round_id": self.round_id,
            "alert_type": self.alert_type,
            "level": self.level,
            "message": self.message,
            "is_read": bool(self.is_read),
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else None,
        }


# ============================================================
# 10. 竞品深度分析（每轮至多一条，监测收尾异步生成，02d 3.3.2）
# ============================================================
class CompetitorAnalysis(Base):
    __tablename__ = "competitor_analysis"

    id = Column(Integer, primary_key=True, autoincrement=True)
    round_id = Column(Integer, unique=True)  # 每轮至多 1 条
    brand_id = Column(Integer, default=1)
    status = Column(String(20), default="pending")  # pending/running/done/failed/unavailable
    data = Column(Text)  # JSON：{is_speculative, competitors[], advice[]}
    error_msg = Column(Text)
    created_at = Column(DateTime, default=now)
    finished_at = Column(DateTime)

    def to_dict(self):
        return {
            "id": self.id,
            "round_id": self.round_id,
            "brand_id": self.brand_id,
            "status": self.status or "pending",
            "data": jloads(self.data, None),
            "error_msg": self.error_msg or "",
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else None,
            "finished_at": self.finished_at.strftime("%Y-%m-%d %H:%M:%S") if self.finished_at else None,
        }


# ============================================================
# 11. 设置（key 唯一，value 为 JSON 字符串）
# ============================================================
class Setting(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(100), unique=True)
    value = Column(Text)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now)


def _set_setting_row(s, key: str, value):
    """在指定 session 内 upsert 设置行（不负责 commit，交由调用方事务统一提交）。"""
    row = s.query(Setting).filter(Setting.key == key).first()
    if row:
        row.value = jdumps(value)
        row.updated_at = now()
    else:
        s.add(Setting(key=key, value=jdumps(value), updated_at=now()))


def get_setting(key: str, default=None, session=None):
    """读设置：数据库优先，其次回落到传入的默认值。

    session 传入时复用调用方事务（不另开连接），供外层事务未提交的场景使用。
    """
    if session is not None:
        row = session.query(Setting).filter(Setting.key == key).first()
        if row:
            return jloads(row.value, default)
        return default
    with session_scope() as s:
        row = s.query(Setting).filter(Setting.key == key).first()
        if row:
            return jloads(row.value, default)
    return default


def set_setting(key: str, value, session=None):
    """写设置（upsert）。

    session 传入时复用调用方事务（不另开连接、不提交，随外层事务原子落库）。
    """
    if session is not None:
        _set_setting_row(session, key, value)
        return
    with session_scope() as s:
        _set_setting_row(s, key, value)


# ============================================================
# 12. AI 调用日志（token 与估算费用）
# ============================================================
class ApiCallLog(Base):
    __tablename__ = "api_call_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    engine_code = Column(String(30))  # 引擎或 analysis
    model = Column(String(100))
    tokens_in = Column(Integer, default=0)
    tokens_out = Column(Integer, default=0)
    cost_yuan = Column(Float, default=0)
    created_at = Column(DateTime, default=now)

    def to_dict(self):
        return {
            "id": self.id,
            "engine_code": self.engine_code,
            "model": self.model,
            "tokens_in": self.tokens_in or 0,
            "tokens_out": self.tokens_out or 0,
            "cost_yuan": round(self.cost_yuan or 0, 4),
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else None,
        }


# ============================================================
# 品牌档案便捷读写（多行：按 id；缺省品牌 1 兼容旧调用）
# ============================================================
EMPTY_BRAND = {
    "brand_name": "",
    "product_name": "",
    "brand_aliases": [],
    "brand_description": "",
    "competitors": [],
    "auto_monitor": True,
    "updated_at": None,
    "created_at": None,
}


def get_brand(brand_id: int = 1) -> dict:
    with session_scope() as s:
        row = s.get(BrandProfile, brand_id)
        if row:
            return row.to_dict()
    return dict(EMPTY_BRAND)


def brand_exists(brand_id: int) -> bool:
    with session_scope() as s:
        return s.get(BrandProfile, brand_id) is not None


def list_brands() -> list:
    with session_scope() as s:
        return [row.to_dict() for row in
                s.query(BrandProfile).order_by(BrandProfile.id.asc()).all()]


def save_brand(data: dict, brand_id: int = 1) -> dict:
    """按 id 更新品牌档案；该品牌不存在时（旧调用兜底）创建一行。"""
    with session_scope() as s:
        row = s.get(BrandProfile, brand_id)
        if not row:
            row = BrandProfile(id=brand_id)
            s.add(row)
        row.brand_name = (data.get("brand_name") or "").strip()
        row.product_name = (data.get("product_name") or "").strip()
        row.brand_aliases = jdumps(data.get("brand_aliases") or [])
        row.brand_description = (data.get("brand_description") or "").strip()
        # 竞品设置已取消（2026-08-15）：竞品一律由 AI 回答自动提取，此处固定清空
        row.competitors = "[]"
        if data.get("auto_monitor") is not None:
            row.auto_monitor = bool(data["auto_monitor"])
        row.updated_at = now()
        return row.to_dict()


def delete_brand_cascade(brand_id: int):
    """级联删除品牌及其全部业务数据（单事务）。api_call_log 全局保留。"""
    with session_scope() as s:
        for model in (QuestionBank, Keyword, MonitorTask, MonitorRound,
                      MonitorResult, ScoreSnapshot, Alert, OptimizationRecord,
                      CompetitorAnalysis):
            s.query(model).filter(model.brand_id == brand_id).delete(synchronize_session=False)
        # B1 遗留 2（07k 任务 4）：清掉该品牌的组登记 settings 键，避免残留无引用键
        s.query(Setting).filter(Setting.key == f"question_group_names_{brand_id}").delete(
            synchronize_session=False)
        row = s.get(BrandProfile, brand_id)
        if row:
            s.delete(row)
