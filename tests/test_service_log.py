"""服务日志治理（geo/service_log.py）单测：行缓冲桥 + 轮转兜底。

保障点：
- print 一行立即落一条日志（写一条见一条），半行由 flush() 兜底；
- 轮转按大小触发并保留历史（.1/.2），不丢已写入的行；
- 日志文件被句柄占用时轮转失败不抛错、不丢日志，解锁后自动恢复轮转。

安装入口 install_background_logging 会整体替换 sys.stdout/stderr 并动
fd 1/2，不适合在 pytest 进程内实测（会破坏捕获与终端），由
--no-browser 模式实起服务的端到端验证覆盖。

运行：python -m pytest tests/test_service_log.py
"""

import io
import logging
import time

from geo import service_log


def _make_logger(tmp_path, max_bytes, backup_count):
    """独立 logger + 小轮转上限的 handler，互不影响其他用例。"""
    name = f"geo.service.test.{time.time_ns()}"
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = service_log.RotatingServiceLogHandler(
        str(tmp_path / "service.log"), maxBytes=max_bytes,
        backupCount=backup_count, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return logger, handler, tmp_path / "service.log"


def _read(path):
    return path.read_text(encoding="utf-8")


def _all_text(tmp_path):
    return "".join(
        p.read_text(encoding="utf-8") for p in sorted(tmp_path.glob("service.log*")))


def test_write_line_lands_immediately(tmp_path):
    logger, handler, log = _make_logger(tmp_path, 1024 * 1024, 1)
    out = service_log.LineRedirector(logger)
    try:
        out.write("第一行\n")
        assert "第一行" in _read(log)  # 见换行即落盘，不积压
        out.write("半行")
        assert "半行" not in _read(log)  # 行缓冲：无换行先缓冲
        out.flush()
        assert "半行" in _read(log)  # flush 兜底落半行
    finally:
        out.close()
        handler.close()


def test_write_splits_multiline_write(tmp_path):
    """print("a\\nb\\nc") 场景：a/b 两行立即落盘，c 半行等 flush。"""
    logger, handler, log = _make_logger(tmp_path, 1024 * 1024, 1)
    out = service_log.LineRedirector(logger)
    try:
        out.write("a\nb\nc")
        content = _read(log)
        assert "a" in content and "b" in content
        assert "c" not in content
        out.flush()
        assert "c" in _read(log)
    finally:
        out.close()
        handler.close()


def test_tee_keeps_console_copy(tmp_path):
    """手动在控制台跑 --no-browser 时输出抄送控制台，日志照落。"""

    class FakeConsole(io.StringIO):
        def isatty(self):
            return True

    console = FakeConsole()
    logger, handler, log = _make_logger(tmp_path, 1024 * 1024, 1)
    out = service_log.LineRedirector(logger, tee=console)
    try:
        out.write("控制台可见\n")
        assert console.getvalue() == "控制台可见\n"  # 原文抄送
        assert "控制台可见" in _read(log)
        assert out.isatty() is True  # 控制台探测走 tee
    finally:
        out.close()
        handler.close()


def test_rotation_by_size_keeps_history(tmp_path):
    logger, handler, log = _make_logger(tmp_path, 200, 2)
    out = service_log.LineRedirector(logger)
    try:
        for i in range(30):  # 每行约 31B，30 行远超 200B 上限
            out.write(f"轮转测试行-{i:03d}-0123456789\n")
        out.flush()
        backups = list(tmp_path.glob("service.log.*"))
        assert backups, "超过 maxBytes 后应轮转出历史文件"
        assert len(backups) <= 2  # backupCount 封顶
        assert "轮转测试行-029" in _read(log)  # 最后一行必在当前文件
    finally:
        out.close()
        handler.close()


def test_rotation_defers_when_file_locked(tmp_path):
    """句柄占用窗口（托盘启动期/用户开着日志）轮转失败不炸不丢，解锁自愈。"""
    logger, handler, log = _make_logger(tmp_path, 200, 2)
    out = service_log.LineRedirector(logger)
    try:
        for i in range(10):
            out.write(f"占用测试-{i}-0123456789\n")
        # 模拟托盘启动窗口期：另开一个写句柄占用日志文件（Windows 下
        # rename 会因共享冲突失败），期间继续写不应抛任何异常
        with open(log, "ab", buffering=0) as lock:
            lock.write(b"x")
            for i in range(10, 20):
                out.write(f"占用测试-{i}-0123456789\n")
        out.write("解锁后-1-0123456789\n")  # 解锁后下一次触发应成功轮转
        out.flush()
        assert "解锁后-1" in _read(log)
        assert "占用测试-19" in _all_text(tmp_path)  # 占用期间的日志没丢
        assert list(tmp_path.glob("service.log.*")), "解锁后应恢复轮转"
    finally:
        out.close()
        handler.close()


def test_handler_level_and_stderr_bridge_share_logger(tmp_path):
    """stdout/stderr 桥共用同一 logger：输出交错落同一份轮转日志。"""
    logger, handler, log = _make_logger(tmp_path, 1024 * 1024, 1)
    out = service_log.LineRedirector(logger, name="<stdout>")
    err = service_log.LineRedirector(logger, name="<stderr>")
    try:
        out.write("来自 stdout\n")
        err.write("来自 stderr\n")
        content = _read(log)
        assert "来自 stdout" in content and "来自 stderr" in content
    finally:
        out.close()
        err.close()
        handler.close()
