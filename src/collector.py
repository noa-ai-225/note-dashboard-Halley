"""データ収集エンジン (src/collector.py)

保存された認証セッション (data/auth/session_state.json) を使用して:
1. https://note.com/dashboard からインプレッション/PV/スキ/コメントと全記事一覧を取得
2. https://note.com/api/v2/current_user から現在の総フォロワー数・記事数を取得
3. data/raw/stats_raw_YYYYMMDD_HHMMSS.json へ保存
"""

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from playwright.sync_api import sync_playwright

try:
    from .config import RAW_DIR, SESSION_FILE, ensure_data_dirs, ensure_utf8_stdio, load_settings
except ImportError:
    from config import RAW_DIR, SESSION_FILE, ensure_data_dirs, ensure_utf8_stdio, load_settings

ensure_utf8_stdio()


def _parse_summary(body_text: str) -> Dict[str, str]:
    """ダッシュボード全体の過去28日間サマリー数値を正規表現で抽出"""
    summary = {
        "period": "過去28日間",
        "impressions": "0",
        "page_views": "0",
        "likes": "0",
        "comments": "0",
        "follower_growth": "0",
        "aggregation_at": "",
    }

    m_summary = re.search(
        r"インプレッション\s*\n+([\d,]+|-)\s*\n+ページビュー\s*\n+([\d,]+|-)\s*\n+スキ\s*\n+([\d,]+|-)\s*\n+コメント\s*\n+([\d,]+|-)",
        body_text,
    )
    if m_summary:
        summary["impressions"] = m_summary.group(1).replace(",", "")
        summary["page_views"] = m_summary.group(2).replace(",", "")
        summary["likes"] = m_summary.group(3).replace(",", "")
        summary["comments"] = m_summary.group(4).replace(",", "")

    m_fol = re.search(r"フォロワー増加\s+([\d,]+)\s+人", body_text)
    if m_fol:
        summary["follower_growth"] = m_fol.group(1).replace(",", "")

    m_agg = re.search(r"(\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2})\s+集計", body_text)
    if m_agg:
        summary["aggregation_at"] = m_agg.group(1)

    return summary


def _detect_article_status(text: str) -> str:
    """記事行テキストから公開ステータスを判定"""
    for status in ("公開中", "下書き", "公開予約", "公開停止"):
        if status in text:
            return status
    return "不明"


def _fetch_current_user_profile(page) -> Dict[str, Any]:
    """認証済みコンテキストで /api/v2/current_user を叩き、総フォロワー数等を取得"""
    profile: Dict[str, Any] = {
        "urlname": "",
        "nickname": "",
        "follower_count": 0,
        "following_count": 0,
        "note_count": 0,
    }
    try:
        resp = page.request.get("https://note.com/api/v2/current_user", timeout=15000)
        if resp.status == 200:
            payload = resp.json()
            data = payload.get("data", {}) if isinstance(payload, dict) else {}
            if isinstance(data, dict):
                profile["urlname"] = str(data.get("urlname") or "")
                profile["nickname"] = str(data.get("nickname") or "")
                profile["follower_count"] = int(data.get("follower_count") or 0)
                profile["following_count"] = int(data.get("following_count") or 0)
                profile["note_count"] = int(data.get("note_count") or 0)
    except Exception as e:
        print(f"[WARN] フォロワー数API取得で例外が発生しました: {e}")
    return profile


def collect_dashboard_data() -> Optional[Path]:
    """noteダッシュボードとプロフィールAPIから最新データを収集し、生JSONのパスを返す"""
    ensure_data_dirs()

    if not SESSION_FILE.exists():
        print(f"[ERROR] 認証セッションが見つかりません: {SESSION_FILE}")
        print("        先に python src/login.py または python src/import_cookie.py で初回ログインしてください。")
        return None

    settings = load_settings()
    headless = bool(settings.get("headless", True))
    max_clicks = int(settings.get("max_load_more_clicks", 50))

    print("=" * 65)
    print("【ハリーのnote実験記録：定期データ収集】")
    print(f">> 実行時刻    : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f">> 認証ファイル: {SESSION_FILE}")
    print("=" * 65)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
            ],
        )
        try:
            context = browser.new_context(
                storage_state=str(SESSION_FILE),
                viewport={"width": 1280, "height": 900},
                locale="ja-JP",
            )
            page = context.new_page()

            print(">> https://note.com/dashboard にアクセス中...")
            try:
                page.goto("https://note.com/dashboard", wait_until="networkidle", timeout=30000)
            except Exception:
                page.goto("https://note.com/dashboard", wait_until="domcontentloaded", timeout=30000)
            try:
                page.wait_for_selector("a[href*='/n/'], text=インプレッション, text=アクセス状況", timeout=8000)
            except Exception:
                pass
            time.sleep(2.5)

            if "login" in page.url or "ログイン" in page.title():
                print("[ERROR] ログインセッションが切れています。再ログインしてください。")
                return None

            # フォロワー数・クリエイタープロフィール取得
            account_profile = _fetch_current_user_profile(page)
            print(
                f">> アカウント取得: {account_profile['nickname'] or '-'} "
                f"(@{account_profile['urlname'] or '-'}) / フォロワー: {account_profile['follower_count']:,} 人"
            )

            # 記事一覧の「もっとみる」を全件展開
            print(">> 記事一覧を展開中...")
            click_count = 0
            consecutive_misses = 0
            while click_count < max_clicks and consecutive_misses < 3:
                more_btn = page.locator("button:has-text('もっとみる')")
                if more_btn.count() > 0 and more_btn.first.is_visible():
                    try:
                        more_btn.first.scroll_into_view_if_needed()
                        time.sleep(0.5)
                        more_btn.first.click()
                        time.sleep(2.0)
                        click_count += 1
                        consecutive_misses = 0
                    except Exception:
                        consecutive_misses += 1
                        time.sleep(1.0)
                else:
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    time.sleep(1.2)
                    consecutive_misses += 1
            print(f">> 『もっとみる』を {click_count} 回クリック（全記事展開完了）")

            extracted = page.evaluate("""() => {
                const data = {
                    title: document.title,
                    url: location.href,
                    captured_at: new Date().toISOString(),
                    body_text: document.body.innerText,
                    articles: []
                };

                const rows = Array.from(document.querySelectorAll('tr, div[role="row"]'));
                const seen = new Set();

                for (const row of rows) {
                    const link = row.querySelector('a[href*="/n/"]');
                    if (!link) continue;

                    const href = link.href;
                    const noteId = href.split('/n/')[1]?.split('?')[0] || '';
                    if (!noteId || seen.has(noteId)) continue;
                    seen.add(noteId);

                    data.articles.push({
                        note_id: noteId,
                        title: link.innerText.trim(),
                        url: href,
                        full_text: row.innerText
                    });
                }

                return data;
            }""")

            try:
                context.storage_state(path=str(SESSION_FILE))
            except Exception:
                pass
        finally:
            browser.close()

    body_text = extracted.get("body_text", "")
    summary = _parse_summary(body_text)
    extracted["summary"] = summary
    extracted["account"] = account_profile

    for article in extracted.get("articles", []):
        text = article.get("full_text", "")
        m_date = re.search(r"(\d{4}年\d{1,2}月\d{1,2}日)", text)
        article["published_at"] = m_date.group(1) if m_date else ""
        article["status"] = _detect_article_status(text)

        lines = [line.strip() for line in text.split("\n") if line.strip()]
        last_line = lines[-1] if lines else ""
        article["raw_numbers"] = [part.strip() for part in last_line.split("\t")]

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = RAW_DIR / f"stats_raw_{timestamp}.json"

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(extracted, f, ensure_ascii=False, indent=2)

    print("-" * 65)
    print("[OK] データ取得完了")
    print(f">> 取得記事数    : {len(extracted.get('articles', []))} 件")
    print(f">> サマリー指標  : {summary}")
    print(f">> フォロワー数  : {account_profile['follower_count']:,} 人")
    print(f">> 生JSON保存先  : {output_file}")
    print("-" * 65)

    return output_file


if __name__ == "__main__":
    if collect_dashboard_data() is None:
        sys.exit(1)
