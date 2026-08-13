"""pytest 公共配置：项目路径 + 预警测试用临时数据库。"""

import os
import sys

# 让测试可以从项目根目录 import geo
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault("GEO_NO_SCHEDULER", "1")

import pytest  # noqa: E402


@pytest.fixture(scope="module")
def alert_db():
    """预警测试专用：把数据库引擎换成临时文件库，测完还原，不碰正式 data/geo.db。"""
    import tempfile

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from geo.models import db as database

    tmp = tempfile.mktemp(suffix=".db")
    old_engine, old_session = database.engine, database.SessionLocal
    database.engine = create_engine(
        f"sqlite:///{tmp}", connect_args={"check_same_thread": False}
    )
    database.SessionLocal = sessionmaker(bind=database.engine, expire_on_commit=False)
    database.init_db()
    yield database
    database.engine, database.SessionLocal = old_engine, old_session
    try:
        os.remove(tmp)
    except OSError:
        pass
