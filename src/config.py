"""共通パス定義・設定読込・定時スロット正規化 (src/config.py)"""

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
AUTH_DIR = DATA_DIR / "auth"
SESSION_FILE = AUTH_DIR / "session_state.json"
RAW_DIR = DATA_DIR / "raw"
EXPORTS_DIR = DATA_DIR / "exports"
LOGS_DIR = DATA_DIR / "logs"
DB_FILE = DATA_DIR / "note_stats.db"

CONFIG_DIR = BASE_DIR / "config"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
LOCAL_SETTINGS_FILE = CONFIG_DIR / "settings.local.json"

DEFAULT_SCHEDULE_TIMES = ["09:00", "14:00", "20:00", "23:00"]
MIN_HARD_COOLDOWN_HOURS = 2.0


def ensure_utf8_stdio() -> None:
    """Windows環境でのコンソール出力文字化けを防止"""
    for stream in (sys.stdout, sys.stderr):
        if stream and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass


def ensure_data_dirs() -> None:
    """必要なデータディレクトリを作成"""
    for directory in (DATA_DIR, AUTH_DIR, RAW_DIR, EXPORTS_DIR, LOGS_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def normalize_schedule_times(raw_times: Iterable[Any]) -> List[str]:
    """時刻リストを検証し、HH:MM 昇順・重複なしに正規化"""
    cleaned = set()
    for item in raw_times:
        s = str(item).strip()
        if re.match(r"^(?:[01]?\d|2[0-3]):[0-5]\d$", s):
            hh, mm = s.split(":")
            cleaned.add(f"{int(hh):02d}:{int(mm):02d}")
    return sorted(cleaned) if cleaned else list(DEFAULT_SCHEDULE_TIMES)


def load_settings() -> Dict[str, Any]:
    """設定ファイルを読み込む（settings.local.json があれば優先上書き）"""
    settings: Dict[str, Any] = {
        "auto_backup_enabled": True,
        "schedule_mode": "times",
        "schedule_times": list(DEFAULT_SCHEDULE_TIMES),
        "sync_interval_hours": 5,
        "min_safe_interval_hours": MIN_HARD_COOLDOWN_HOURS,
        "headless": True,
        "max_load_more_clicks": 50,
    }

    for path in (SETTINGS_FILE, LOCAL_SETTINGS_FILE):
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    settings.update(loaded)
            except Exception:
                pass

    settings["schedule_times"] = normalize_schedule_times(settings.get("schedule_times", DEFAULT_SCHEDULE_TIMES))
    configured_min = float(settings.get("min_safe_interval_hours", MIN_HARD_COOLDOWN_HOURS))
    settings["min_safe_interval_hours"] = max(configured_min, MIN_HARD_COOLDOWN_HOURS)
    return settings
