"""ハリーのnote実験記録：時間別ダッシュボード ＆ モバイル管理UIサーバー (src/ui_server.py)"""

import argparse
import csv
import json
import os
import re
import secrets
import socket
import sqlite3
import subprocess
import sys
import threading
import webbrowser
from collections import deque
from datetime import datetime, timedelta
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

try:
    from .config import (
        BASE_DIR,
        DB_FILE,
        DEFAULT_SCHEDULE_TIMES,
        EXPORTS_DIR,
        LOCAL_SETTINGS_FILE,
        LOGS_DIR,
        MIN_HARD_COOLDOWN_HOURS,
        RAW_DIR,
        SESSION_FILE,
        ensure_data_dirs,
        ensure_utf8_stdio,
        load_settings,
        normalize_schedule_times,
    )
    from .export_secret import encode_session_base64
    from .main import check_cooldown
    from .parser import format_diff
except ImportError:
    from config import (
        BASE_DIR,
        DB_FILE,
        DEFAULT_SCHEDULE_TIMES,
        EXPORTS_DIR,
        LOCAL_SETTINGS_FILE,
        LOGS_DIR,
        MIN_HARD_COOLDOWN_HOURS,
        RAW_DIR,
        SESSION_FILE,
        ensure_data_dirs,
        ensure_utf8_stdio,
        load_settings,
        normalize_schedule_times,
    )
    from export_secret import encode_session_base64
    from main import check_cooldown
    from parser import format_diff

ensure_utf8_stdio()

SRC_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
SCRIPTS_DIR = BASE_DIR / "scripts"
DEFAULT_TASK_NAME = "HarryNoteLabBackup"


class JobManager:
    """認証やバックアップなどの長時間処理を単一ジョブとして管理"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running = False
        self._job_name = ""
        self._status = "idle"
        self._started_at: Optional[str] = None
        self._finished_at: Optional[str] = None
        self._exit_code: Optional[int] = None
        self._logs: deque[str] = deque(maxlen=400)
        self._proc: Optional[subprocess.Popen] = None
        self._cancelled = False

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "running": self._running,
                "job_name": self._job_name,
                "status": self._status,
                "started_at": self._started_at,
                "finished_at": self._finished_at,
                "exit_code": self._exit_code,
                "logs": list(self._logs),
            }

    def cancel(self) -> bool:
        with self._lock:
            proc = self._proc
            if not self._running or proc is None:
                return False
            self._cancelled = True
            pid = proc.pid

        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=5)
            else:
                proc.kill()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

        finished = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            self._running = False
            self._status = "error"
            self._exit_code = -1
            self._finished_at = finished
            self._proc = None
            self._logs.append(f"[{finished}] [キャンセル] ユーザーにより処理が中断されました。")
        return True

    def start_subprocess(
        self,
        job_name: str,
        cmd: List[str],
        stdin_text: Optional[str] = None,
        show_window: bool = False,
        log_to_scheduled_file: bool = False,
    ) -> bool:
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._cancelled = False
            self._job_name = job_name
            self._status = "running"
            self._started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._finished_at = None
            self._exit_code = None
            self._logs.clear()
            self._logs.append(f"[{self._started_at}] ジョブ開始: {job_name}")

        thread = threading.Thread(
            target=self._run_worker,
            args=(cmd, stdin_text, show_window, log_to_scheduled_file),
            daemon=True,
        )
        thread.start()
        return True

    def _run_worker(
        self,
        cmd: List[str],
        stdin_text: Optional[str],
        show_window: bool,
        log_to_scheduled_file: bool,
    ) -> None:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"

        creationflags = 0
        if not show_window and sys.platform == "win32" and hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags = subprocess.CREATE_NO_WINDOW

        proc: Optional[subprocess.Popen] = None
        captured_lines: List[str] = []
        start_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(BASE_DIR),
                stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                creationflags=creationflags,
            )
            with self._lock:
                self._proc = proc

            if stdin_text is not None and proc.stdin is not None:
                try:
                    proc.stdin.write(stdin_text)
                    proc.stdin.close()
                except Exception:
                    pass

            if proc.stdout is not None:
                for line in proc.stdout:
                    cleaned = line.rstrip("\r\n")
                    if cleaned:
                        captured_lines.append(cleaned)
                        with self._lock:
                            self._logs.append(cleaned)

            return_code = proc.wait()
            finished = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with self._lock:
                self._proc = None
                if not self._cancelled:
                    self._exit_code = return_code
                    self._finished_at = finished
                    self._status = "success" if return_code == 0 else "error"
                    self._running = False
                    self._logs.append(f"[{finished}] ジョブ終了 (code={return_code})")
        except Exception as e:
            finished = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with self._lock:
                self._proc = None
                if not self._cancelled:
                    self._exit_code = -1
                    self._finished_at = finished
                    self._status = "error"
                    self._running = False
                    self._logs.append(f"[ERROR] 実行例外: {e}")
        finally:
            if proc is not None:
                for pipe in (proc.stdout, proc.stdin):
                    if pipe is not None:
                        try:
                            pipe.close()
                        except Exception:
                            pass
            if log_to_scheduled_file:
                try:
                    LOGS_DIR.mkdir(parents=True, exist_ok=True)
                    log_path = LOGS_DIR / "scheduled_backup.log"
                    end_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    with open(log_path, "a", encoding="utf-8") as lf:
                        lf.write("========================================================\n")
                        lf.write(f"[定時観測開始] {start_ts}\n")
                        for cl in captured_lines:
                            lf.write(cl + "\n")
                        lf.write(f"[定時観測終了] {end_ts}\n\n")
                except Exception:
                    pass


JOB_MANAGER = JobManager()


def _parse_dt(ts_str: Optional[str]) -> Optional[datetime]:
    if not ts_str:
        return None
    try:
        return datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt
        except Exception:
            return None


def evaluate_times_schedule(
    settings: Dict[str, Any],
    last_captured_at: Optional[str],
    last_time_slot: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Tuple[bool, Optional[datetime], Optional[str]]:
    """9時・14時・20時・23時などの複数定時スロット（または間隔モード）の実行判定"""
    if not settings.get("auto_backup_enabled", False):
        return False, None, None

    cur_now = now or datetime.now()
    min_safe = max(float(settings.get("min_safe_interval_hours", MIN_HARD_COOLDOWN_HOURS)), MIN_HARD_COOLDOWN_HOURS)
    last_dt = _parse_dt(last_captured_at)

    cooldown_blocked = False
    if last_dt is not None:
        elapsed_hours = (cur_now - last_dt).total_seconds() / 3600.0
        if elapsed_hours < min_safe:
            cooldown_blocked = True

    mode = str(settings.get("schedule_mode", "times"))
    if mode == "interval":
        interval_hours = max(float(settings.get("sync_interval_hours", 5)), min_safe)
        if last_dt is None:
            return (not cooldown_blocked), cur_now, cur_now.strftime("%H:%M")
        next_dt = last_dt + timedelta(hours=interval_hours)
        due = (not cooldown_blocked) and (cur_now >= next_dt)
        return due, next_dt, (cur_now.strftime("%H:%M") if due else None)

    # 複数時刻スロットモード（デフォルト: 09:00, 14:00, 20:00, 23:00）
    times = normalize_schedule_times(settings.get("schedule_times", DEFAULT_SCHEDULE_TIMES))
    slot_dts = []
    for t_str in times:
        hh, mm = map(int, t_str.split(":"))
        slot_dts.append((t_str, cur_now.replace(hour=hh, minute=mm, second=0, microsecond=0)))

    # 直近に通過したスロット（最大110分以内）が未取得なら実行
    for t_str, s_dt in reversed(slot_dts):
        diff_min = (cur_now - s_dt).total_seconds() / 60.0
        if 0.0 <= diff_min <= 110.0:
            already_done_today = False
            if last_dt is not None and last_dt.date() == cur_now.date():
                if last_time_slot == t_str or last_dt >= (s_dt - timedelta(minutes=10)):
                    already_done_today = True
            if not already_done_today:
                if cooldown_blocked and last_dt is not None:
                    return False, last_dt + timedelta(hours=min_safe), t_str
                return True, s_dt, t_str
            break

    # 次回予定スロットを算出
    for _, s_dt in slot_dts:
        if s_dt > cur_now:
            return False, s_dt, None

    first_hh, first_mm = map(int, times[0].split(":"))
    tomorrow_first = (cur_now + timedelta(days=1)).replace(hour=first_hh, minute=first_mm, second=0, microsecond=0)
    return False, tomorrow_first, None


def _get_latest_snapshot_meta() -> Tuple[Optional[str], Optional[str]]:
    if not DB_FILE.exists():
        return None, None
    try:
        conn = sqlite3.connect(DB_FILE)
        try:
            cur = conn.cursor()
            cur.execute("SELECT captured_at, time_slot FROM account_snapshots ORDER BY captured_at DESC, id DESC LIMIT 1")
            row = cur.fetchone()
            if row:
                return row[0], row[1]
            return None, None
        finally:
            conn.close()
    except Exception:
        return None, None


def _builtin_scheduler_loop(stop_event: threading.Event) -> None:
    """スマホ/iPadから時刻編集・ON/OFFできる内蔵バックグラウンドスケジューラ"""
    while not stop_event.is_set():
        try:
            if SESSION_FILE.exists() and not JOB_MANAGER.snapshot()["running"]:
                settings = load_settings()
                last_cap, last_slot = _get_latest_snapshot_meta()
                due, _, target_slot = evaluate_times_schedule(settings, last_cap, last_slot)
                if due:
                    cmd = [sys.executable, str(SRC_DIR / "main.py"), "--type", "scheduled"]
                    if target_slot:
                        cmd.extend(["--slot", target_slot])
                    JOB_MANAGER.start_subprocess(
                        f"定時観測 ({target_slot or 'scheduled'})",
                        cmd,
                        log_to_scheduled_file=True,
                    )
        except Exception:
            pass
        stop_event.wait(30.0)


def get_scheduler_status() -> Dict[str, Any]:
    """Windowsタスクスケジューラの登録状況を取得"""
    venv_py = BASE_DIR / ".venv" / "bin" / "python"
    py_cmd = str(venv_py) if venv_py.exists() else "python3"
    cron_example = f'0 9,14,20,23 * * * cd "{BASE_DIR}" && "{py_cmd}" src/main.py --type scheduled >> data/logs/scheduled_backup.log 2>&1'

    if sys.platform != "win32":
        return {
            "platform": sys.platform,
            "supported": False,
            "registered": False,
            "task_name": DEFAULT_TASK_NAME,
            "state": "cron等で設定",
            "next_run_time": "-",
            "last_run_time": "-",
            "last_result": "-",
            "cron_example": cron_example,
        }

    ps_script = (
        f"$t = Get-ScheduledTask -TaskName '{DEFAULT_TASK_NAME}' -ErrorAction SilentlyContinue; "
        "if (-not $t) { Write-Output '{\"registered\":false}'; exit 0 }; "
        "$i = Get-ScheduledTaskInfo -TaskName $t.TaskName; "
        "$obj = [PSCustomObject]@{ "
        "registered = $true; "
        "state = [string]$t.State; "
        "next_run_time = if ($i.NextRunTime -and $i.NextRunTime.Year -gt 1900) { $i.NextRunTime.ToString('yyyy-MM-dd HH:mm:ss') } else { '-' }; "
        "last_run_time = if ($i.LastRunTime -and $i.LastRunTime.Year -gt 1900) { $i.LastRunTime.ToString('yyyy-MM-dd HH:mm:ss') } else { '-' }; "
        "last_result = [string]$i.LastTaskResult "
        "}; "
        "$obj | ConvertTo-Json -Compress"
    )
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        res = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            creationflags=creationflags,
        )
        raw_out = res.stdout.strip()
        if res.returncode == 0 and raw_out:
            parsed = json.loads(raw_out)
            if parsed.get("registered"):
                return {
                    "platform": "win32",
                    "supported": True,
                    "registered": True,
                    "task_name": DEFAULT_TASK_NAME,
                    "state": parsed.get("state", "Ready"),
                    "next_run_time": parsed.get("next_run_time", "-"),
                    "last_run_time": parsed.get("last_run_time", "-"),
                    "last_result": parsed.get("last_result", "-"),
                    "cron_example": cron_example,
                }
    except Exception:
        pass

    return {
        "platform": "win32",
        "supported": True,
        "registered": False,
        "task_name": DEFAULT_TASK_NAME,
        "state": "未登録",
        "next_run_time": "-",
        "last_run_time": "-",
        "last_result": "-",
        "cron_example": cron_example,
    }


def _build_hourly_timeline(conn: sqlite3.Connection) -> Dict[str, Any]:
    """DBから時間ごとの観測記録と直前スロットからの増減を算出"""
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT id, snapshot_date, captured_at, time_slot, capture_type, period,
               impressions, page_views, likes, comments,
               follower_count, follower_growth, note_count,
               creator_urlname, creator_nickname, aggregation_at
        FROM account_snapshots
        ORDER BY captured_at ASC, id ASC
    """)
    rows = [dict(r) for r in cur.fetchall()]

    timeline: List[Dict[str, Any]] = []
    prev: Optional[Dict[str, Any]] = None
    dates_set = set()

    for r in rows:
        imp = int(r.get("impressions") or 0)
        pv = int(r.get("page_views") or 0)
        likes = int(r.get("likes") or 0)
        comm = int(r.get("comments") or 0)
        fol = int(r.get("follower_count") or 0)

        d_imp = (imp - int(prev.get("impressions") or 0)) if prev else None
        d_pv = (pv - int(prev.get("page_views") or 0)) if prev else None
        d_likes = (likes - int(prev.get("likes") or 0)) if prev else None
        d_comm = (comm - int(prev.get("comments") or 0)) if prev else None
        d_fol = (
            (fol - int(prev.get("follower_count") or 0))
            if (prev and fol > 0 and int(prev.get("follower_count") or 0) > 0)
            else None
        )

        slot = r.get("time_slot") or (r.get("captured_at", "")[11:16])
        dates_set.add(r["snapshot_date"])

        timeline.append(
            {
                "id": r["id"],
                "snapshot_date": r["snapshot_date"],
                "captured_at": r["captured_at"],
                "time_slot": slot,
                "capture_type": r["capture_type"],
                "period": r["period"],
                "impressions": imp,
                "diff_impressions": d_imp,
                "diff_impressions_str": format_diff(d_imp),
                "page_views": pv,
                "diff_page_views": d_pv,
                "diff_page_views_str": format_diff(d_pv),
                "likes": likes,
                "diff_likes": d_likes,
                "diff_likes_str": format_diff(d_likes),
                "comments": comm,
                "diff_comments": d_comm,
                "diff_comments_str": format_diff(d_comm),
                "follower_count": fol,
                "diff_follower_count": d_fol,
                "diff_follower_count_str": format_diff(d_fol),
                "follower_growth": int(r.get("follower_growth") or 0),
                "note_count": int(r.get("note_count") or 0),
                "creator_urlname": r.get("creator_urlname") or "",
                "creator_nickname": r.get("creator_nickname") or "",
                "aggregation_at": r.get("aggregation_at") or "",
            }
        )
        prev = r

    return {
        "records": list(reversed(timeline)),  # 新しい順に表示
        "available_dates": sorted(dates_set, reverse=True),
        "latest": timeline[-1] if timeline else None,
    }


def _read_csv_preview(limit: int = 500) -> Dict[str, Any]:
    latest_csv = EXPORTS_DIR / "note_articles_latest.csv"
    hourly_csv = EXPORTS_DIR / "note_hourly_history.csv"
    csv_files = (
        sorted(EXPORTS_DIR.glob("note_articles_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
        if EXPORTS_DIR.exists()
        else []
    )
    archives = [p for p in csv_files if p.name != "note_articles_latest.csv"]
    archive_files = [
        {
            "name": p.name,
            "size_kb": round(p.stat().st_size / 1024, 1),
            "mtime": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
        }
        for p in archives[:20]
    ]

    rows: List[Dict[str, str]] = []
    if latest_csv.exists():
        try:
            with open(latest_csv, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for idx, row in enumerate(reader):
                    if idx >= limit:
                        break
                    rows.append(dict(row))
        except Exception:
            pass

    return {
        "latest_csv_exists": latest_csv.exists(),
        "hourly_csv_exists": hourly_csv.exists(),
        "archive_count": len(archives),
        "archive_files": archive_files,
        "preview_rows": rows,
    }


def _read_scheduled_log_tail(max_lines: int = 50) -> Dict[str, Any]:
    log_file = LOGS_DIR / "scheduled_backup.log"
    if not log_file.exists():
        return {"exists": False, "path": str(log_file), "lines": []}
    try:
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()[-max_lines:]
    except Exception:
        lines = []
    return {"exists": True, "path": str(log_file), "lines": lines}


def get_status_payload() -> Dict[str, Any]:
    """UI表示用の現在ステータス・時間別タイムラインを集約"""
    ensure_data_dirs()
    settings = load_settings()
    min_safe_hours = float(settings.get("min_safe_interval_hours", MIN_HARD_COOLDOWN_HOURS))

    auth_exists = SESSION_FILE.exists()
    auth_updated = (
        datetime.fromtimestamp(SESSION_FILE.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        if auth_exists
        else None
    )

    db_info: Dict[str, Any] = {
        "exists": DB_FILE.exists(),
        "path": str(DB_FILE),
        "articles_count": 0,
        "snapshots_count": 0,
        "last_captured_at": None,
        "last_time_slot": None,
        "latest_summary": None,
    }
    timeline_data: Dict[str, Any] = {"records": [], "available_dates": [], "latest": None}

    if DB_FILE.exists():
        try:
            conn = sqlite3.connect(DB_FILE)
            try:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM articles")
                db_info["articles_count"] = cur.fetchone()[0]
                timeline_data = _build_hourly_timeline(conn)
                db_info["snapshots_count"] = len(timeline_data["records"])
                if timeline_data["latest"]:
                    db_info["last_captured_at"] = timeline_data["latest"]["captured_at"]
                    db_info["last_time_slot"] = timeline_data["latest"]["time_slot"]
                    db_info["latest_summary"] = timeline_data["latest"]
            finally:
                conn.close()
        except Exception:
            pass

    is_safe, elapsed = check_cooldown(min_safe_hours)
    remaining_minutes = 0 if is_safe else int((min_safe_hours - elapsed) * 60) + 1
    raw_count = len(list(RAW_DIR.glob("stats_raw_*.json"))) if RAW_DIR.exists() else 0

    _, next_dt, _ = evaluate_times_schedule(settings, db_info["last_captured_at"], db_info["last_time_slot"])

    return {
        "auth": {
            "configured": auth_exists,
            "updated_at": auth_updated,
            "path": str(SESSION_FILE),
        },
        "database": db_info,
        "timeline": timeline_data,
        "cooldown": {
            "can_sync": is_safe,
            "elapsed_hours": round(elapsed, 2) if elapsed < 900 else None,
            "remaining_minutes": remaining_minutes,
            "min_safe_hours": min_safe_hours,
        },
        "settings": {
            "auto_backup_enabled": bool(settings.get("auto_backup_enabled", True)),
            "schedule_mode": str(settings.get("schedule_mode", "times")),
            "schedule_times": settings.get("schedule_times", DEFAULT_SCHEDULE_TIMES),
            "headless": bool(settings.get("headless", True)),
            "max_load_more_clicks": int(settings.get("max_load_more_clicks", 50)),
            "sync_interval_hours": int(settings.get("sync_interval_hours", 5)),
            "min_safe_interval_hours": min_safe_hours,
        },
        "builtin_scheduler": {
            "enabled": bool(settings.get("auto_backup_enabled", True)),
            "mode": str(settings.get("schedule_mode", "times")),
            "schedule_times": settings.get("schedule_times", DEFAULT_SCHEDULE_TIMES),
            "interval_hours": int(settings.get("sync_interval_hours", 5)),
            "next_run_at": next_dt.strftime("%Y-%m-%d %H:%M") if next_dt else "-",
        },
        "network": {"lan_ip": _get_lan_ip()},
        "scheduler": get_scheduler_status(),
        "exports": _read_csv_preview(),
        "raw_count": raw_count,
        "logs": _read_scheduled_log_tail(),
        "job": JOB_MANAGER.snapshot(),
    }


def save_local_settings(payload: Dict[str, Any]) -> Dict[str, Any]:
    """ローカル設定オーバーライド (config/settings.local.json) を検証・保存"""
    current = load_settings()

    if "auto_backup_enabled" in payload:
        current["auto_backup_enabled"] = bool(payload["auto_backup_enabled"])
    if "schedule_mode" in payload:
        mode = str(payload["schedule_mode"])
        if mode not in ("times", "interval"):
            raise ValueError("スケジュール種別は times または interval を指定してください。")
        current["schedule_mode"] = mode
    if "schedule_times" in payload:
        raw_list = payload["schedule_times"]
        if isinstance(raw_list, str):
            raw_list = [x.strip() for x in raw_list.split(",") if x.strip()]
        if not isinstance(raw_list, list) or not raw_list:
            raise ValueError("観測時刻を1つ以上指定してください (例: 09:00, 14:00, 20:00, 23:00)。")
        normalized = normalize_schedule_times(raw_list)
        if not normalized:
            raise ValueError("有効な HH:MM 形式の時刻を指定してください。")
        current["schedule_times"] = normalized
    if "headless" in payload:
        current["headless"] = bool(payload["headless"])
    if "max_load_more_clicks" in payload:
        clicks = int(payload["max_load_more_clicks"])
        if clicks < 1 or clicks > 200:
            raise ValueError("最大クリック回数は 1〜200 の範囲で指定してください。")
        current["max_load_more_clicks"] = clicks
    if "sync_interval_hours" in payload:
        interval = int(payload["sync_interval_hours"])
        if interval < int(MIN_HARD_COOLDOWN_HOURS):
            raise ValueError(f"安全弁により、同期間隔は最低 {int(MIN_HARD_COOLDOWN_HOURS)} 時間以上を指定してください。")
        current["sync_interval_hours"] = interval

    LOCAL_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    to_save = {
        "auto_backup_enabled": bool(current.get("auto_backup_enabled", True)),
        "schedule_mode": str(current.get("schedule_mode", "times")),
        "schedule_times": current.get("schedule_times", list(DEFAULT_SCHEDULE_TIMES)),
        "headless": bool(current.get("headless", True)),
        "max_load_more_clicks": int(current.get("max_load_more_clicks", 50)),
        "sync_interval_hours": int(current.get("sync_interval_hours", 5)),
    }
    with open(LOCAL_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(to_save, f, ensure_ascii=False, indent=2)

    return to_save


def open_local_folder(target: str) -> str:
    folder_map = {
        "exports": EXPORTS_DIR,
        "data": BASE_DIR / "data",
        "logs": LOGS_DIR,
        "raw": RAW_DIR,
    }
    if target not in folder_map:
        raise ValueError("無効なフォルダ指定です。")
    ensure_data_dirs()
    folder_path = folder_map[target]
    if sys.platform == "win32":
        os.startfile(str(folder_path))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(folder_path)])
    else:
        subprocess.Popen(["xdg-open", str(folder_path)])
    return str(folder_path)


class BackupUIHandler(BaseHTTPRequestHandler):
    """ローカル管理UI用のHTTPリクエストハンドラ"""

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, status_code: int, data: Any) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("JSONオブジェクトを送信してください。")
        return data

    def _authorize_request(self) -> Optional[str]:
        expected = getattr(self.server, "access_key", None)
        if not expected:
            return None

        parsed = urlparse(self.path)
        query_keys = parse_qs(parsed.query).get("key", [])
        if query_keys and secrets.compare_digest(query_keys[0], expected):
            return f"ui_key={expected}; Path=/; HttpOnly; SameSite=Lax"

        raw_cookie = self.headers.get("Cookie", "")
        if raw_cookie:
            cookie = SimpleCookie()
            try:
                cookie.load(raw_cookie)
                morsel = cookie.get("ui_key")
                if morsel and secrets.compare_digest(morsel.value, expected):
                    return ""
            except Exception:
                pass

        self._send_json(HTTPStatus.FORBIDDEN, {"error": "アクセス権がありません。正しい認証付きURLから開いてください。"})
        return "__DENIED__"

    def do_GET(self) -> None:
        set_cookie_hdr = self._authorize_request()
        if set_cookie_hdr == "__DENIED__":
            return

        path = self.path.split("?", 1)[0]

        if path in ("/", "/index.html"):
            index_file = WEB_DIR / "index.html"
            if not index_file.exists():
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "web/index.html が見つかりません。"})
                return
            content = index_file.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            if set_cookie_hdr:
                self.send_header("Set-Cookie", set_cookie_hdr)
            self.end_headers()
            self.wfile.write(content)
            return

        if path == "/api/status":
            self._send_json(HTTPStatus.OK, get_status_payload())
            return

        if path == "/api/job":
            self._send_json(HTTPStatus.OK, JOB_MANAGER.snapshot())
            return

        if path == "/api/export/latest.csv":
            target = EXPORTS_DIR / "note_articles_latest.csv"
            if not target.exists():
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "CSVファイルがまだありません。"})
                return
            data = target.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="note_articles_latest.csv"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if path == "/api/export/hourly.csv":
            target = EXPORTS_DIR / "note_hourly_history.csv"
            if not target.exists():
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "時間別CSVがまだありません。"})
                return
            data = target.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="note_hourly_history.csv"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if path == "/api/export/file":
            qs = parse_qs(urlparse(self.path).query)
            fname = (qs.get("name", [""])[0] or "").strip()
            if not re.match(r"^note_(?:articles|hourly)_[A-Za-z0-9_]+\.csv$", fname):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "無効なファイル名です。"})
                return
            target_file = EXPORTS_DIR / fname
            if not target_file.exists():
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "指定のCSVファイルが見つかりません。"})
                return
            data = target_file.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if path == "/api/export/db":
            if not DB_FILE.exists():
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "SQLiteデータベースがまだ作成されていません。"})
                return
            data = DB_FILE.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/x-sqlite3")
            self.send_header("Content-Disposition", 'attachment; filename="note_stats.db"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if path == "/api/auth/export-b64":
            encoded = encode_session_base64()
            if not encoded:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "認証セッションがまだ保存されていません。"})
                return
            self._send_json(HTTPStatus.OK, {"secret_name": "NOTE_SESSION_STATE_B64", "value": encoded})
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not Found"})

    def do_POST(self) -> None:
        set_cookie_hdr = self._authorize_request()
        if set_cookie_hdr == "__DENIED__":
            return

        path = self.path.split("?", 1)[0]

        try:
            body = self._read_json_body()
        except Exception as e:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": f"リクエスト解析エラー: {e}"})
            return

        if path == "/api/job/cancel":
            cancelled = JOB_MANAGER.cancel()
            self._send_json(HTTPStatus.OK, {"status": "cancelled" if cancelled else "idle", "job": JOB_MANAGER.snapshot()})
            return

        if path == "/api/auth/credentials":
            identifier = str(body.get("identifier", "")).strip()
            password = str(body.get("password", ""))
            if not identifier or not password:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "メールアドレス（またはnote ID）とパスワードを入力してください。"})
                return
            started = JOB_MANAGER.start_subprocess(
                "ID・パスワード直接認証",
                [sys.executable, str(SRC_DIR / "login.py"), "--credentials-stdin"],
                stdin_text=json.dumps({"identifier": identifier, "password": password}),
            )
            if not started:
                self._send_json(HTTPStatus.CONFLICT, {"error": "別のジョブが実行中です。"})
                return
            self._send_json(HTTPStatus.ACCEPTED, {"status": "started", "job": JOB_MANAGER.snapshot()})
            return

        if path == "/api/auth/login":
            started = JOB_MANAGER.start_subprocess(
                "PCブラウザ手動ログイン",
                [sys.executable, str(SRC_DIR / "login.py")],
                show_window=True,
            )
            if not started:
                self._send_json(HTTPStatus.CONFLICT, {"error": "別のジョブが実行中です。"})
                return
            self._send_json(HTTPStatus.ACCEPTED, {"status": "started", "job": JOB_MANAGER.snapshot()})
            return

        if path == "/api/auth/cookie":
            cookie_val = str(body.get("cookie", "")).strip()
            skip_verify = bool(body.get("skip_verify", False))
            if not cookie_val:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Cookie値を入力してください。"})
                return
            cmd = [sys.executable, str(SRC_DIR / "import_cookie.py"), "--cookie-stdin"]
            if skip_verify:
                cmd.append("--skip-verify")
            started = JOB_MANAGER.start_subprocess("Cookie取り込み＆接続検証", cmd, stdin_text=cookie_val + "\n")
            if not started:
                self._send_json(HTTPStatus.CONFLICT, {"error": "別のジョブが実行中です。"})
                return
            self._send_json(HTTPStatus.ACCEPTED, {"status": "started", "job": JOB_MANAGER.snapshot()})
            return

        if path == "/api/auth/logout":
            if SESSION_FILE.exists():
                SESSION_FILE.unlink()
            self._send_json(HTTPStatus.OK, {"status": "ok", "message": "認証セッションを削除しました。"})
            return

        if path == "/api/backup":
            force = bool(body.get("force", False))
            parse_only = bool(body.get("parse_only", False))
            cmd = [sys.executable, str(SRC_DIR / "main.py"), "--type", "manual"]
            if force:
                cmd.append("--force")
            if parse_only:
                cmd.append("--parse-only")
            label = "最新raw JSONから再生成" if parse_only else ("手動観測（強制実行）" if force else "手動観測（今すぐ取得）")
            started = JOB_MANAGER.start_subprocess(label, cmd)
            if not started:
                self._send_json(HTTPStatus.CONFLICT, {"error": "別のジョブが実行中です。"})
                return
            self._send_json(HTTPStatus.ACCEPTED, {"status": "started", "job": JOB_MANAGER.snapshot()})
            return

        if path == "/api/scheduler":
            if sys.platform != "win32":
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Windowsタスク登録は Windows 環境専用です。"})
                return
            action = str(body.get("action", "register"))
            ps_file = SCRIPTS_DIR / "register_task_windows.ps1"
            if action == "unregister":
                cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps_file), "-Unregister"]
                job_label = "Windows定時タスク解除"
            else:
                settings = load_settings()
                times_csv = ",".join(settings.get("schedule_times", DEFAULT_SCHEDULE_TIMES))
                cmd = [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(ps_file),
                    "-Times",
                    times_csv,
                ]
                job_label = f"Windows定時タスク登録 ({times_csv})"
            started = JOB_MANAGER.start_subprocess(job_label, cmd)
            if not started:
                self._send_json(HTTPStatus.CONFLICT, {"error": "別のジョブが実行中です。"})
                return
            self._send_json(HTTPStatus.ACCEPTED, {"status": "started", "job": JOB_MANAGER.snapshot()})
            return

        if path == "/api/autostart":
            if sys.platform != "win32":
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Windows自動起動登録は Windows 環境専用です。"})
                return
            action = str(body.get("action", "register"))
            ps_file = SCRIPTS_DIR / "register_ui_autostart_windows.ps1"
            if action == "unregister":
                cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps_file), "-Unregister"]
                job_label = "PC起動時の自動起動を解除"
            else:
                cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps_file)]
                job_label = "PC起動時のスマホ用自動起動を登録"
            started = JOB_MANAGER.start_subprocess(job_label, cmd)
            if not started:
                self._send_json(HTTPStatus.CONFLICT, {"error": "別のジョブが実行中です。"})
                return
            self._send_json(HTTPStatus.ACCEPTED, {"status": "started", "job": JOB_MANAGER.snapshot()})
            return

        if path in ("/api/settings", "/api/schedule"):
            try:
                saved = save_local_settings(body)
                self._send_json(HTTPStatus.OK, {"status": "ok", "settings": saved})
            except ValueError as e:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        if path == "/api/open-folder":
            target = str(body.get("target", "exports"))
            try:
                opened = open_local_folder(target)
                self._send_json(HTTPStatus.OK, {"status": "ok", "path": opened})
            except Exception as e:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not Found"})


def _get_lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return str(s.getsockname()[0])
    except Exception:
        return "127.0.0.1"


def _find_available_port(host: str, preferred_port: int) -> int:
    for candidate in [preferred_port] + list(range(preferred_port + 1, preferred_port + 20)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, candidate))
                return candidate
            except OSError:
                continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


def serve_ui(
    port: int = 8766,
    open_browser: bool = True,
    host: str = "127.0.0.1",
    access_key: Optional[str] = None,
) -> None:
    ensure_data_dirs()
    actual_port = _find_available_port(host, port)
    server = ThreadingHTTPServer((host, actual_port), BackupUIHandler)
    server.access_key = access_key  # type: ignore[attr-defined]
    suffix = f"/?key={access_key}" if access_key else ""
    local_url = f"http://127.0.0.1:{actual_port}{suffix}"
    lan_ip = _get_lan_ip()
    lan_url = f"http://{lan_ip}:{actual_port}{suffix}"

    print("=" * 65)
    print(" ハリーのnote実験記録：時間別観測 ＆ 管理UI")
    print("=" * 65)
    print(f" >> PCブラウザ用URL   : {local_url}")
    if host == "0.0.0.0":
        print(f" >> スマホ/同一Wi-Fi用: {lan_url}")
    if access_key:
        print(" >> アクセス保護キー  : 有効 (?key=... 付きURLのみ許可)")
    print(" >> 停止するにはこのウィンドウで Ctrl+C を押してください。")
    print("=" * 65)

    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(local_url)).start()

    stop_event = threading.Event()
    sched_thread = threading.Thread(target=_builtin_scheduler_loop, args=(stop_event,), daemon=True)
    sched_thread.start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n>> サーバーを終了しています...")
    finally:
        stop_event.set()
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ハリーのnote実験記録 管理UIサーバー")
    parser.add_argument("--port", type=int, default=8766, help="待受ポート番号 (デフォルト: 8766)")
    parser.add_argument("--host", default="127.0.0.1", help="待受ホスト (デフォルト: 127.0.0.1)")
    parser.add_argument("--lan", action="store_true", help="同一Wi-Fi内のスマホ・iPadからもアクセス可能にする")
    parser.add_argument("--access-key", default="", help="URLアクセス保護用のシークレットキー")
    parser.add_argument("--random-key", action="store_true", help="ランダムなアクセス保護キーを自動生成")
    parser.add_argument("--no-browser", action="store_true", help="起動時にブラウザを自動で開かない")
    args = parser.parse_args()

    bind_host = "0.0.0.0" if args.lan else args.host
    key = secrets.token_urlsafe(18) if args.random_key else (args.access_key or None)
    serve_ui(port=args.port, open_browser=not args.no_browser, host=bind_host, access_key=key)
