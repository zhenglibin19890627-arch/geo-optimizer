"""服务运行日志治理：托盘后台模式（--no-browser）的 service.log 轮转。

背景：托盘（geo/tray.py）以 `pythonw app.py --no-browser` 拉起后台服务时，
把子进程的 stdout/stderr 重定向到 data/service.log。该文件此前无上限，
长期运行会一直膨胀。本模块在 --no-browser 模式下把运行日志改由
logging.handlers.RotatingFileHandler 承接（默认 5MB×3，约 20MB 封顶），
print 调用点保持不动——stdout/stderr 被换成 LineRedirector 行缓冲桥：

- 每凑满一行立即落一条日志（handler 逐条 flush），写一条见一条；
- 半行（无换行）先缓冲，flush() 时补落，与行缓冲语义一致；
- 前台 `python app.py` 不走本模块，控制台输出行为不变。

Windows 下做轮转有两个坑，都在这里处理：

1. 进程从托盘继承的 service.log 句柄不释放，改名式轮转会一直失败
   → 安装前把 fd 1/2 重新指到 NUL（仅当 stdout/stderr 是重定向文件
   而非控制台时），释放继承句柄；
2. 轮转改名瞬间文件被别的句柄占用（托盘启动窗口期、用户开着日志文件）
   时，RotatingFileHandler 默认会抛 PermissionError 且丢日志
   → RotatingServiceLogHandler 捕获后跳过本次轮转、重开当前文件继续写，
   下一次触发再试：不崩溃、不丢日志。
"""

import io
import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler

from geo import config

#: 单个日志文件上限（字节）；配合 backupCount，service.log + .1~.3 共约 20MB 封顶
SERVICE_LOG_MAX_BYTES = 5 * 1024 * 1024
SERVICE_LOG_BACKUP_COUNT = 3

SERVICE_LOG_FILE = config.DATA_DIR / "service.log"
_LOGGER_NAME = "geo.service"


class RotatingServiceLogHandler(RotatingFileHandler):
    """轮转失败（Windows 句柄占用）时跳过本次并继续写，绝不因轮转崩溃。"""

    def doRollover(self):
        try:
            super().doRollover()
        except OSError:
            # Windows：日志文件被短时占用（托盘启动窗口期/用户开着日志）。
            # super() 已关闭流但改名失败 → 重开当前文件继续写（append 模式
            # 总写到文件尾，与任何并发写句柄都安全）；文件仍超限时下一次
            # emit 会再次尝试轮转，句柄释放后自动恢复。
            try:
                if self.stream is None:
                    self.stream = self._open()
            except Exception:
                pass

    def handleError(self, record):
        # 日志实在写不进去（磁盘满等）就静默丢弃：默认实现会把堆栈打到
        # sys.stderr，而后者的行缓冲桥又走本 handler，极端情况下互相
        # 递归打爆进程，必须掐断。
        pass


class LineRedirector:
    """把 stdout/stderr 的 print 输出按行桥接到日志的行缓冲写入器。

    print 调用点不动：write() 攒到换行就立刻落一条日志（handler 逐条
    flush），无换行的半行留在缓冲里等 flush() 补落——写一条见一条。
    tee 非空时（手动在控制台跑 --no-browser）输出同时抄送控制台。
    """

    def __init__(self, logger, tee=None, name="<stdout>"):
        self._log = logger
        self._tee = tee
        self._pending = ""
        self._lock = threading.Lock()
        self.name = name

    # ---- io 最小接口：print 与常见库用到的集合 ----
    def write(self, text):
        if not isinstance(text, str):
            text = str(text)
        if text:
            with self._lock:
                if self._tee is not None:
                    try:
                        self._tee.write(text)
                        self._tee.flush()
                    except Exception:
                        pass  # 控制台抄送失败不影响日志
                self._pending += text
                while "\n" in self._pending:
                    line, self._pending = self._pending.split("\n", 1)
                    self._log.info(line)
        return len(text)

    def flush(self):
        with self._lock:
            if self._pending:
                self._log.info(self._pending)  # 半行也落盘
                self._pending = ""
        if self._tee is not None:
            try:
                self._tee.flush()
            except Exception:
                pass

    def close(self):
        # 只冲刷缓冲，不关底层 handler：统一交给 logging.shutdown 收尾
        self.flush()

    def isatty(self):
        try:
            return bool(self._tee is not None and self._tee.isatty())
        except Exception:
            return False

    def writable(self):
        return True

    def fileno(self):
        if self._tee is not None:
            try:
                return self._tee.fileno()
            except Exception:
                pass
        raise io.UnsupportedOperation("fileno")

    @property
    def encoding(self):
        return "utf-8"

    @property
    def errors(self):
        return "replace"


def _console_stream(stream):
    """stream 是真实控制台就返回原流（供 tee 兜底），否则 None。"""
    try:
        return stream if (stream is not None and stream.isatty()) else None
    except Exception:
        return None


def _redirect_fd_to_devnull(fd: int, replace: bool):
    """把底层文件描述符改指 NUL，释放从托盘继承的 service.log 句柄。

    os.dup2 会隐式关闭 fd 原本指向的句柄——这正是目的：该句柄不释放，
    Windows 下 RotatingFileHandler 的改名轮转会一直报句柄占用。
    """
    if not replace:
        return
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(devnull, fd)
        finally:
            os.close(devnull)
    except Exception:
        pass


def install_background_logging():
    """托盘后台模式日志接管：print 照旧，落点改为 data/service.log 轮转。

    - stdout/stderr 换成 LineRedirector：行缓冲语义保留，见换行即落一条；
    - RotatingServiceLogHandler 承接（5MB×3），轮转被占用时自动延后重试；
    - pythonw 裸跑（无控制台、sys.stdout 为 None）也照常接入日志；
    - 手动在控制台跑 --no-browser 时输出同时保留在控制台（tee）；
    - 前台 `python app.py`（无 --no-browser）不进本函数，行为不变。
    """
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        handler = RotatingServiceLogHandler(
            str(SERVICE_LOG_FILE),
            maxBytes=SERVICE_LOG_MAX_BYTES,
            backupCount=SERVICE_LOG_BACKUP_COUNT,
            encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    except Exception:
        handler = None  # 日志打不开（权限/磁盘问题）也不能挡服务启动
        # 此时 sys.stderr 还是托盘重定向的原始文件，把原因留档便于排查
        try:
            import traceback
            traceback.print_exc(file=sys.stderr)
        except Exception:
            pass

    log = logging.getLogger(_LOGGER_NAME)
    log.setLevel(logging.INFO)
    log.propagate = False
    if handler is not None:
        log.addHandler(handler)

    out_console = _console_stream(sys.stdout)
    err_console = _console_stream(sys.stderr)
    # 先释放继承的 service.log 句柄（fd 指到 NUL），轮转改名才不 stuck；
    # 控制台句柄不动，手动 --no-browser 时终端仍能看到输出。
    _redirect_fd_to_devnull(1, replace=out_console is None and sys.stdout is not None)
    _redirect_fd_to_devnull(2, replace=err_console is None and sys.stderr is not None)

    sys.stdout = LineRedirector(log, tee=out_console, name="<stdout>")
    sys.stderr = LineRedirector(log, tee=err_console, name="<stderr>")

    # Flask 的默认 handler 在 import 时抓走了原始 stderr（其句柄已释放），
    # 改挂到行缓冲桥上，让 app.logger（如 500 错误堆栈）也进轮转日志。
    try:
        from flask.logging import default_handler as flask_default_handler
        flask_default_handler.setStream(sys.stderr)
    except Exception:
        pass
