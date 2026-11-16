"""統合バックアップ実行スクリプト (src/main.py)

9時・14時・20時・23時の定時観測向けに:
1. クールダウン安全弁チェック（最低2時間間隔）
2. collector.py で5指標（インプ/PV/スキ/コメント/フォロワー数）＋記事一覧を収集
3. parser.py で SQLite 追記 ＆ 時間別増減CSV・記事別CSVを出力
4. 前回観測からの増減を含むラボレポートを表示
"""

import argparse
import sqlite3
import sys
import time
from datetime import datetime
from typing import Optional, Tuple

try:
    from .collector import collect_dashboard_data
    from .config import DB_FILE, SESSION_FILE, ensure_data_dirs, ensure_utf8_stdio, load_settings
    from .parser import format_diff, process_latest_raw
except ImportError:
    from collector import collect_dashboard_data
    from config import DB_FILE, SESSION_FILE, ensure_data_dirs, ensure_utf8_stdio, load_settings
    from parser import format_diff, process_latest_raw

ensure_utf8_stdio()


def check_cooldown(min_safe_hours: float) -> Tuple[bool, float]:
    """前回の実行からの経過時間を検査し、安全弁（最短間隔）を満たしているか判定"""
    if not DB_FILE.exists():
        return True, 999.0

    try:
        conn = sqlite3.connect(DB_FILE)
        try:
            cur = conn.cursor()
            cur.execute("SELECT captured_at FROM account_snapshots ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
        finally:
            conn.close()

        if not row or not row[0]:
            return True, 999.0

        last_time_str = row[0]
        try:
            last_dt = datetime.strptime(last_time_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            last_dt = datetime.fromisoformat(last_time_str.replace("Z", "+00:00")).replace(tzinfo=None)

        elapsed_hours = (datetime.now() - last_dt).total_seconds() / 3600.0
        if elapsed_hours < min_safe_hours:
            return False, elapsed_hours
        return True, elapsed_hours
    except Exception:
        return True, 999.0


def print_status() -> None:
    """現在の認証・DB蓄積・クールダウン状態を表示"""
    settings = load_settings()
    min_safe_hours = settings["min_safe_interval_hours"]

    print("=" * 70)
    print(" ハリーのnote実験記録 観測システム：現在ステータス")
    print("=" * 70)
    print(f"  認証セッション : {'設定済み' if SESSION_FILE.exists() else '未設定'} ({SESSION_FILE})")
    print(f"  データベース   : {'存在' if DB_FILE.exists() else '未作成'} ({DB_FILE})")
    print(f"  定時スロット   : {', '.join(settings.get('schedule_times', []))}")

    if DB_FILE.exists():
        conn = sqlite3.connect(DB_FILE)
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM articles")
            art_cnt = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*), MAX(captured_at) FROM account_snapshots")
            snap_cnt, last_cap = cur.fetchone()
        finally:
            conn.close()

        is_safe, elapsed = check_cooldown(min_safe_hours)
        print(f"  蓄積記事数     : {art_cnt} 件")
        print(f"  蓄積観測回数   : {snap_cnt} 回 (最終観測: {last_cap or '-'})")
        if is_safe:
            print("  クールダウン   : 解除済み（次回取得可能）")
        else:
            rem_min = int((min_safe_hours - elapsed) * 60) + 1
            print(f"  クールダウン   : 安全弁作動中（あと約 {rem_min} 分）")
    print("=" * 70)


def _print_summary_report(csv_path) -> None:
    """最新スナップショットの5指標＋前回比増減を表示"""
    try:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT time_slot, period, impressions, page_views, likes, comments,
                       follower_count, follower_growth, aggregation_at, captured_at
                FROM account_snapshots
                ORDER BY captured_at DESC, id DESC LIMIT 2
            """)
            rows = [dict(r) for r in cur.fetchall()]

            cur.execute("""
                SELECT a.title, s.impressions, s.page_views, s.likes, s.comments
                FROM article_snapshots s
                JOIN articles a ON s.article_id = a.article_id
                WHERE s.captured_at = (SELECT MAX(captured_at) FROM article_snapshots)
                ORDER BY s.page_views DESC LIMIT 5
            """)
            top_articles = cur.fetchall()
        finally:
            conn.close()

        if rows:
            latest = rows[0]
            prev = rows[1] if len(rows) > 1 else None
            d_imp = format_diff(latest["impressions"] - prev["impressions"]) if prev else "-"
            d_pv = format_diff(latest["page_views"] - prev["page_views"]) if prev else "-"
            d_likes = format_diff(latest["likes"] - prev["likes"]) if prev else "-"
            d_comm = format_diff(latest["comments"] - prev["comments"]) if prev else "-"
            d_fol = (
                format_diff(latest["follower_count"] - prev["follower_count"])
                if (prev and latest["follower_count"] > 0 and prev["follower_count"] > 0)
                else "-"
            )

            print(
                f"【実験サマリー [{latest['time_slot'] or '-'}] ({latest['period']})】"
                f"（note集計: {latest['aggregation_at'] or '-'} / 取得: {latest['captured_at']}）"
            )
            print(f"  ・インプレッション : {latest['impressions']:,} (前回比: {d_imp})")
            print(f"  ・ページビュー(PV) : {latest['page_views']:,} (前回比: {d_pv})")
            print(f"  ・スキ数           : {latest['likes']:,} (前回比: {d_likes})")
            print(f"  ・コメント数       : {latest['comments']:,} (前回比: {d_comm})")
            print(f"  ・フォロワー数     : {latest['follower_count']:,} 人 (前回比: {d_fol})")
            print("-" * 70)

        print("【PVランキング トップ5】")
        for rank, (title, imp, pv, likes, comm) in enumerate(top_articles, 1):
            short_title = title if len(title) <= 35 else title[:32] + "..."
            print(f"  {rank}位. {short_title}")
            print(f"       PV: {pv:,} | Imp: {imp:,} | スキ: {likes} | コメント: {comm}")
    except Exception as e:
        print(f"[WARN] サマリー表示中にエラーが発生しました: {e}")

    print("=" * 70)
    print(f">> 記事別CSV         : {csv_path}")
    print(f">> SQLiteデータベース: {DB_FILE}")
    print("=" * 70 + "\n")


def run_full_backup(
    capture_type: str = "manual",
    force: bool = False,
    parse_only: bool = False,
    explicit_slot: Optional[str] = None,
) -> bool:
    """バックアップの一連の処理を実行"""
    ensure_data_dirs()

    print("=" * 70)
    print(" ハリーのnote実験記録：時間別バックアップシステム")
    print(f"    実行日時: {datetime.now().strftime('%Y年%m月%d日 %H:%M:%S')}")
    print(f"    取得種別: {capture_type}")
    print("=" * 70)

    settings = load_settings()
    min_safe_hours = settings["min_safe_interval_hours"]

    if not parse_only and not force:
        is_safe, elapsed = check_cooldown(min_safe_hours)
        if not is_safe:
            print(f"\n[SKIP] 【安全弁作動】前回の取得から {elapsed:.1f} 時間しか経過していません。")
            print(f"       最低 {min_safe_hours:.1f} 時間の間隔制限により今回の取得をスキップしました。")
            print("       ※今すぐ取得したい場合は '--force' を指定して実行してください。\n")
            print("=" * 70)
            return True

    start_time = time.time()

    raw_path = None
    if not parse_only:
        print("\n[Step 1/2] ダッシュボードとフォロワー数を受信中...")
        raw_path = collect_dashboard_data()
        if not raw_path:
            print("[ERROR] データ収集に失敗しました。処理を中断します。")
            return False
    else:
        print("\n[Step 1/2] --parse-only 指定のためデータ収集をスキップします。")

    print("\n[Step 2/2] データの正規化・DB時系列追記・時間別CSV出力を実行中...")
    csv_path = process_latest_raw(
        capture_type=capture_type,
        raw_file=raw_path,
        explicit_slot=explicit_slot,
    )
    if not csv_path:
        print("[ERROR] データ処理に失敗しました。")
        return False

    elapsed_sec = round(time.time() - start_time, 1)
    print("\n" + "=" * 70)
    print(f" [OK] 観測・記録完了 (処理時間: {elapsed_sec} 秒)")
    print("=" * 70)
    _print_summary_report(csv_path)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="ハリーのnote実験記録 時間別バックアップツール")
    parser.add_argument("--type", default="manual", choices=["scheduled", "auto", "manual"], help="取得種別")
    parser.add_argument("--slot", default=None, help="時間帯スロット明示指定 (例: 09:00)")
    parser.add_argument("--force", action="store_true", help="安全弁を無視して強制実行")
    parser.add_argument("--parse-only", action="store_true", help="最新 raw JSON から再生成のみ実行")
    parser.add_argument("--status", action="store_true", help="現在の認証状態・蓄積状況を表示")
    args = parser.parse_args()

    if args.status:
        print_status()
        return 0

    success = run_full_backup(
        capture_type=args.type,
        force=args.force,
        parse_only=args.parse_only,
        explicit_slot=args.slot,
    )
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
