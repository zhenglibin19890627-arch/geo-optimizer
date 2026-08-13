"""GEO 优化系统：配置文件加载（零硬编码，一切从 YAML 读取）。"""

import copy
import os
import shutil
import tempfile
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
CONFIG_FILE = CONFIG_DIR / "config.yaml"
EXAMPLE_FILE = CONFIG_DIR / "config.example.yaml"
DATA_DIR = PROJECT_ROOT / "data"
DB_FILE = DATA_DIR / "geo.db"

_CACHE = None


def ensure_config_file() -> Path:
    """首次启动自动从模板复制生成 config.yaml（模板全留空，不覆盖已有配置）。"""
    if not CONFIG_FILE.exists():
        if EXAMPLE_FILE.exists():
            shutil.copy(EXAMPLE_FILE, CONFIG_FILE)
            print("【提示】已自动生成 config/config.yaml（配置模板）。")
            print("【提示】请把各家 AI 平台的钥匙（API Key）填到该文件后重启程序。")
        else:
            CONFIG_FILE.write_text("", encoding="utf-8")
    return CONFIG_FILE


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _deep_merge(base: dict, override: dict) -> dict:
    """override 覆盖 base，递归合并。"""
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config() -> dict:
    """读取 config.yaml；字段缺失时回落到模板（模板里也留空则回落为默认值）。"""
    global _CACHE
    if _CACHE is None:
        example = _load_yaml(EXAMPLE_FILE)
        real = _load_yaml(CONFIG_FILE)
        _CACHE = _deep_merge(example, real)
    return _CACHE


def reload_config() -> dict:
    global _CACHE
    _CACHE = None
    return load_config()


def save_engine_api_key(code: str, api_key: str) -> None:
    """把某家引擎的钥匙写回 config.yaml 并立即生效（设置页直接填钥匙用）。

    只改 api_key 字段，其余配置与注释之外的结构原样保留（yaml.safe_dump
    会丢掉注释，属已知取舍）；写完后清缓存，新的适配器实例立即读到新钥匙。
    """
    data = _load_yaml(CONFIG_FILE) or {}
    engines = data.setdefault("engines", {})
    if not isinstance(engines, dict):
        engines = {}
        data["engines"] = engines
    engine = engines.setdefault(code, {})
    if not isinstance(engine, dict):
        engine = {}
        engines[code] = engine
    engine["api_key"] = api_key
    _write_yaml(data)


def save_analysis_api_key(api_key: str) -> None:
    """把分析用模型的钥匙写回 config.yaml 并立即生效。"""
    data = _load_yaml(CONFIG_FILE) or {}
    analysis = data.setdefault("analysis", {})
    if not isinstance(analysis, dict):
        analysis = {}
        data["analysis"] = analysis
    analysis["api_key"] = api_key
    _write_yaml(data)


def _write_yaml(data: dict) -> None:
    """写回 config.yaml（保留键序），写完后重载配置缓存。

    用临时文件 + os.replace 原子替换：中途崩溃/断电不会截断 config.yaml
    丢钥匙；并发读方只会看到旧文件或新文件，不会看到半写内容。
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=CONFIG_DIR, prefix=".config.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
        os.replace(tmp_path, CONFIG_FILE)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    reload_config()


def get_engine_config(code: str) -> dict:
    """读取某家引擎的配置（enabled/api_key/base_url/model/model_options/note 等）。"""
    cfg = load_config()
    engine = (cfg.get("engines") or {}).get(code) or {}
    return engine if isinstance(engine, dict) else {}


def get_analysis_config() -> dict:
    cfg = load_config()
    return cfg.get("analysis") or {}


def get_section(name: str, default: dict = None) -> dict:
    cfg = load_config()
    section = cfg.get(name)
    if not isinstance(section, dict):
        return default or {}
    return section


def engine_exists(code: str) -> bool:
    return code in ((load_config().get("engines") or {}) or {})
