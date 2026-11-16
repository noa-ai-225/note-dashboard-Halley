"""データ正規化・時間スロット別DB保存・増減算出・CSV出力 (src/parser.py)

9時・14時・20時・23時などの時間ごとのスナップショットを SQLite (data/note_stats.db) に
追記保存し、直前観測との増減（インプ / PV / スキ / コメント / フォロワー数）を
自動算出して Excel対応 UTF-8 (BOM付き) CSV を出力します。
"""

import csv
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

try:
    from .config import DEFAULT_SCHEDULE_TIMES, DB_FILE, EXPORTS_DIR, RAW_DIR, ensure_data_dirs, ensure_utf8_stdio, load_settings
except ImportError:
    from config import DEFAULT_SCHEDULE_TIMES, DB_FILE, EXPORTS_DIR, RAW_DIR, ensure_data_dirs, ensure_utf8_stdio, load_settings

ensure_utf8_stdio()


def clean_int(val) -> int:
    """カンマやハイフンを含む文字列を整数に変換"""
    if val is None:
        return 0
    s = str(val).replace(",", "").strip()
    if s in ("-", "", "None"):
        return 0
    try:
        match = re.search(r"\d+", s)
        return int(match.group()) if match else 0
    except Exception:
        return 0


def format_diff(diff: Optional[int]) -> str:
    """差分値を表示用文字列にフォーマット"""
    if diff is None:
        return "-"
    if diff > 0:
        return f"+{diff}"
    return str(diff)


def infer_time_slot(
    dt: datetime,
    schedule_times: Optional[List[str]] = None,
    capture_type: str = "scheduled",
    explicit_slot: Optional[str] = None,
) -> str:
    """観測時刻から表示用の時間帯スロット（例: '09:00', '14:00', '20:00', '23:00'）を決定"""
    if explicit_slot and re.match(r"^(?:[01]\d|2[0-3]):[0-5]\d$", explicit_slot.strip()):
        return explicit_slot.strip()

    times = schedule_times or DEFAULT_SCHEDULE_TIMES
    if capture_type == "scheduled":
        best_slot = None
        best_diff_min = 999999.0
        for slot_str in times:
            try:
                hh, mm = map(int, slot_str.split(":"))
                slot_dt = dt.replace(hour=hh, minute=mm, second=0, microsecond=0)
                diff_min = (dt - slot_dt).total_seconds() / 60.0
                # スロット時刻の -15分 〜 +120分 以内なら該当スロットとして正規化（cron遅延吸収）
                if -15.0 <= diff_min <= 120.0 and abs(diff_min) < best_diff_min:
                    best_diff_min = abs(diff_min)
                    best_slot = slot_str
            except Exception:
                continue
        if best_slot:
            return best_slot

    return dt.strftime("%H:%M")


def init_database(conn: sqlite3.Connection) -> None:
    """SQLiteテーブルおよびインデックスの初期化・マイグレーション"""
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS articles (
        article_id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        url TEXT NOT NULL,
        published_at TEXT,
        status TEXT DEFAULT '',
        updated_at TEXT
    )
    """)

    cur.execute("PRAGMA table_info(articles)")
    if "status" not in {row[1] for row in cur.fetchall()}:
        cur.execute("ALTER TABLE articles ADD COLUMN status TEXT DEFAULT ''")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS account_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_date TEXT NOT NULL,
        captured_at TEXT NOT NULL,
        time_slot TEXT DEFAULT '',
        capture_type TEXT NOT NULL DEFAULT 'scheduled',
        period TEXT NOT NULL,
        impressions INTEGER DEFAULT 0,
        page_views INTEGER DEFAULT 0,
        likes INTEGER DEFAULT 0,
        comments INTEGER DEFAULT 0,
        follower_count INTEGER DEFAULT 0,
        follower_growth INTEGER DEFAULT 0,
        note_count INTEGER DEFAULT 0,
        creator_urlname TEXT DEFAULT '',
        creator_nickname TEXT DEFAULT '',
        aggregation_at TEXT
    )
    """)

    cur.execute("PRAGMA table_info(account_snapshots)")
    acc_cols = {row[1] for row in cur.fetchall()}
    for col_name, col_def in (
        ("time_slot", "TEXT DEFAULT ''"),
        ("follower_count", "INTEGER DEFAULT 0"),
        ("note_count", "INTEGER DEFAULT 0"),
        ("creator_urlname", "TEXT DEFAULT ''"),
        ("creator_nickname", "TEXT DEFAULT ''"),
    ):
        if col_name not in acc_cols:
            cur.execute(f"ALTER TABLE account_snapshots ADD COLUMN {col_name} {col_def}")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS article_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_date TEXT NOT NULL,
        captured_at TEXT NOT NULL,
        time_slot TEXT DEFAULT '',
        capture_type TEXT NOT NULL DEFAULT 'scheduled',
        article_id TEXT NOT NULL,
        impressions INTEGER DEFAULT 0,
        page_views INTEGER DEFAULT 0,
        likes INTEGER DEFAULT 0,
        comments INTEGER DEFAULT 0,
        sales INTEGER DEFAULT 0,
        aggregation_at TEXT,
        FOREIGN KEY (article_id) REFERENCES articles(article_id)
    )
    """)

    cur.execute("PRAGMA table_info(article_snapshots)")
    if "time_slot" not in {row[1] for row in cur.fetchall()}:
        cur.execute("ALTER TABLE article_snapshots ADD COLUMN time_slot TEXT DEFAULT ''")

    cur.execute("CREATE INDEX IF NOT EXISTS idx_art_captured ON article_snapshots(article_id, captured_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_acc_captured ON account_snapshots(captured_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_acc_date_slot ON account_snapshots(snapshot_date, time_slot)")
    conn.commit()


def export_hourly_history_csv(conn: sqlite3.Connection) -> Path:
    """account_snapshots 全件から時間別推移＋前回比増減CSVを書き出す"""
    ensure_data_dirs()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT snapshot_date, captured_at, time_slot, capture_type, period,
               impressions, page_views, likes, comments,
               follower_count, follower_growth, note_count, aggregation_at
        FROM account_snapshots
        ORDER BY captured_at ASC, id ASC
    """)
    rows = [dict(r) for r in cur.fetchall()]

    out_rows: List[Dict[str, object]] = []
    prev: Optional[Dict[str, object]] = None
    for r in rows:
        imp = int(r["impressions"] or 0)
        pv = int(r["page_views"] or 0)
        likes = int(r["likes"] or 0)
        comm = int(r["comments"] or 0)
        fol = int(r["follower_count"] or 0)

        d_imp = (imp - int(prev["impressions"] or 0)) if prev else None
        d_pv = (pv - int(prev["page_views"] or 0)) if prev else None
        d_likes = (likes - int(prev["likes"] or 0)) if prev else None
        d_comm = (comm - int(prev["comments"] or 0)) if prev else None
        d_fol = (fol - int(prev["follower_count"] or 0)) if (prev and fol > 0 and int(prev["follower_count"] or 0) > 0) else None

        out_rows.append(
            {
                "captured_at": r["captured_at"],
                "snapshot_date": r["snapshot_date"],
                "time_slot": r["time_slot"] or r["captured_at"][11:16],
                "capture_type": r["capture_type"],
                "impressions": imp,
                "diff_impressions": format_diff(d_imp),
                "page_views": pv,
                "diff_page_views": format_diff(d_pv),
                "likes": likes,
                "diff_likes": format_diff(d_likes),
                "comments": comm,
                "diff_comments": format_diff(d_comm),
                "follower_count": fol,
                "diff_follower_count": format_diff(d_fol),
                "follower_growth_28d": int(r["follower_growth"] or 0),
                "note_count": int(r["note_count"] or 0),
                "aggregation_at": r["aggregation_at"] or "",
            }
        )
        prev = r

    hourly_csv = EXPORTS_DIR / "note_hourly_history.csv"
    fieldnames = [
        "captured_at",
        "snapshot_date",
        "time_slot",
        "capture_type",
        "impressions",
        "diff_impressions",
        "page_views",
        "diff_page_views",
        "likes",
        "diff_likes",
        "comments",
        "diff_comments",
        "follower_count",
        "diff_follower_count",
        "follower_growth_28d",
        "note_count",
        "aggregation_at",
    ]
    with open(hourly_csv, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(out_rows)

    return hourly_csv


def process_latest_raw(
    capture_type: str = "scheduled",
    raw_file: Optional[Path] = None,
    explicit_slot: Optional[str] = None,
) -> Optional[Path]:
    """最新の生JSONを正規化し、SQLite追記と時間別/記事別CSV出力を実行"""
    ensure_data_dirs()
    settings = load_settings()
    schedule_times = settings.get("schedule_times", DEFAULT_SCHEDULE_TIMES)

    if raw_file is None:
        raw_files = sorted(RAW_DIR.glob("stats_raw_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not raw_files:
            print("[ERROR] 解析対象の raw JSON がありません。先に collector.py を実行してください。")
            return None
        target_file = raw_files[0]
    else:
        target_file = raw_file

    print("=" * 65)
    print(f"【ハリーのnote実験記録：時系列DB追記 ＆ CSV保存】: {target_file.name}")
    print(f">> 取得種別: {capture_type}")
    print("=" * 65)

    with open(target_file, "r", encoding="utf-8") as f:
        payload = json.load(f)

    raw_captured_at = payload.get("captured_at", datetime.now().isoformat())
    try:
        dt = datetime.fromisoformat(raw_captured_at.replace("Z", "+00:00"))
        local_dt = dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt
    except Exception:
        local_dt = datetime.now()

    local_captured_at = local_dt.strftime("%Y-%m-%d %H:%M:%S")
    timestamp_compact = local_dt.strftime("%Y%m%d_%H%M%S")
    today_str = local_captured_at.split(" ")[0]
    time_slot = infer_time_slot(
        local_dt,
        schedule_times=schedule_times,
        capture_type=capture_type,
        explicit_slot=explicit_slot,
    )

    conn = sqlite3.connect(DB_FILE)
    try:
        init_database(conn)
        cur = conn.cursor()

        cur.execute("DELETE FROM account_snapshots WHERE captured_at = ?", (local_captured_at,))
        cur.execute("DELETE FROM article_snapshots WHERE captured_at = ?", (local_captured_at,))

        summary = payload.get("summary", {})
        account = payload.get("account", {})
        aggregation_at = summary.get("aggregation_at", "")

        cur.execute(
            """
            INSERT INTO account_snapshots (
                snapshot_date, captured_at, time_slot, capture_type, period,
                impressions, page_views, likes, comments,
                follower_count, follower_growth, note_count,
                creator_urlname, creator_nickname, aggregation_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                today_str,
                local_captured_at,
                time_slot,
                capture_type,
                summary.get("period", "過去28日間"),
                clean_int(summary.get("impressions", 0)),
                clean_int(summary.get("page_views", 0)),
                clean_int(summary.get("likes", 0)),
                clean_int(summary.get("comments", 0)),
                clean_int(account.get("follower_count", 0)),
                clean_int(summary.get("follower_growth", 0)),
                clean_int(account.get("note_count", 0)),
                str(account.get("urlname", "")),
                str(account.get("nickname", "")),
                aggregation_at,
            ),
        )

        raw_articles = payload.get("articles", [])
        parsed_articles = []

        for item in raw_articles:
            note_id = item.get("note_id")
            if not note_id:
                continue

            title = item.get("title", "").strip()
            url = item.get("url", "").strip()
            published_at = item.get("published_at", "").strip()
            status = item.get("status", "").strip()

            raw_nums = item.get("raw_numbers", [])
            if len(raw_nums) < 4:
                lines = item.get("full_text", "").split("\n")
                nums_line = lines[-1] if lines else ""
                raw_nums = nums_line.split("\t")

            impressions = clean_int(raw_nums[0]) if len(raw_nums) > 0 else 0
            page_views = clean_int(raw_nums[1]) if len(raw_nums) > 1 else 0
            likes = clean_int(raw_nums[2]) if len(raw_nums) > 2 else 0
            comments = clean_int(raw_nums[3]) if len(raw_nums) > 3 else 0
            sales = clean_int(raw_nums[4]) if len(raw_nums) > 4 else 0

            cur.execute(
                """
                INSERT INTO articles (article_id, title, url, published_at, status, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(article_id) DO UPDATE SET
                    title = excluded.title,
                    url = excluded.url,
                    published_at = excluded.published_at,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (note_id, title, url, published_at, status, local_captured_at),
            )

            cur.execute(
                """
                INSERT INTO article_snapshots (
                    snapshot_date, captured_at, time_slot, capture_type, article_id,
                    impressions, page_views, likes, comments, sales, aggregation_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    today_str,
                    local_captured_at,
                    time_slot,
                    capture_type,
                    note_id,
                    impressions,
                    page_views,
                    likes,
                    comments,
                    sales,
                    aggregation_at,
                ),
            )

            cur.execute(
                """
                SELECT impressions, page_views, likes, comments
                FROM article_snapshots
                WHERE article_id = ? AND captured_at < ?
                ORDER BY captured_at DESC LIMIT 1
                """,
                (note_id, local_captured_at),
            )
            prev_row = cur.fetchone()

            diff_imp = (impressions - prev_row[0]) if prev_row else None
            diff_pv = (page_views - prev_row[1]) if prev_row else None
            diff_likes = (likes - prev_row[2]) if prev_row else None
            diff_comments = (comments - prev_row[3]) if prev_row else None

            parsed_articles.append(
                {
                    "captured_at": local_captured_at,
                    "time_slot": time_slot,
                    "capture_type": capture_type,
                    "article_id": note_id,
                    "status": status,
                    "title": title,
                    "published_at": published_at,
                    "impressions": impressions,
                    "diff_impressions": format_diff(diff_imp),
                    "page_views": page_views,
                    "diff_pv": format_diff(diff_pv),
                    "likes": likes,
                    "diff_likes": format_diff(diff_likes),
                    "comments": comments,
                    "diff_comments": format_diff(diff_comments),
                    "sales": sales,
                    "url": url,
                }
            )

        conn.commit()
        hourly_csv = export_hourly_history_csv(conn)
    finally:
        conn.close()

    csv_file_timestamped = EXPORTS_DIR / f"note_articles_{timestamp_compact}.csv"
    csv_file_latest = EXPORTS_DIR / "note_articles_latest.csv"

    fieldnames = [
        "captured_at",
        "time_slot",
        "capture_type",
        "article_id",
        "status",
        "title",
        "published_at",
        "impressions",
        "diff_impressions",
        "page_views",
        "diff_pv",
        "likes",
        "diff_likes",
        "comments",
        "diff_comments",
        "sales",
        "url",
    ]

    for out_path in (csv_file_timestamped, csv_file_latest):
        with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(parsed_articles)

    print(f"[OK] DB時系列追記完了 : {DB_FILE} (時間帯スロット: {time_slot})")
    print(f">> 記録記事数        : {len(parsed_articles)} 件")
    print(f"[OK] 時間別推移CSV   : {hourly_csv}")
    print(f"[OK] 日時付き記事CSV : {csv_file_timestamped}")
    print("-" * 65)

    return csv_file_timestamped


if __name__ == "__main__":
    if process_latest_raw(capture_type="manual") is None:
        sys.exit(1)
