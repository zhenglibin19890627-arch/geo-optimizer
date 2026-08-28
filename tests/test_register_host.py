"""注册脚本纯逻辑回归（不碰真实注册表）。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bridge.register_host import (HOST_NAME, REPO_ROOT, HOST_SCRIPT,
                                  build_bat, build_manifest, validate_ext_id)
import pytest


def test_扩展ID校验():
    assert validate_ext_id("ABCDEFGHIJKLMNOPABCDEFGHIJKLMNOP") == "abcdefghijklmnop" * 2
    with pytest.raises(SystemExit):
        validate_ext_id("too-short")
    with pytest.raises(SystemExit):
        validate_ext_id("z" * 32)  # 扩展 ID 字母表不含 z
    with pytest.raises(SystemExit):
        validate_ext_id("")


def test_manifest结构():
    m = build_manifest("a" * 32)
    assert m["name"] == HOST_NAME == "org.synccaster.bridge"
    assert m["type"] == "stdio"
    assert m["allowed_origins"] == ["chrome-extension://" + "a" * 32 + "/"]
    assert m["path"].endswith("geo_bridge_host.bat")


def test_bat用绝对路径并带引号():
    bat = build_bat()
    lines = bat.splitlines()
    assert lines[0] == "@echo off"
    assert f'"{sys.executable}"' in bat
    assert f'"{HOST_SCRIPT}"' in bat
    assert HOST_SCRIPT == os.path.join(REPO_ROOT, "bridge", "geo_bridge_host.py")
