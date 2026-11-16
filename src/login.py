"""初回ログイン・認証セッション保存ツール (src/login.py)

1. PCデスクトップ上でブラウザウィンドウを開く対話型ログイン (引数なし: Google/X/Apple/2FA対応)
2. メールアドレス / note ID ＋ パスワードによるヘッドレス直接認証 (--credentials-stdin)
"""

import argparse
import json
import sys
import time
from playwright.sync_api import sync_playwright

try:
    from .config import SESSION_FILE, ensure_data_dirs, ensure_utf8_stdio
except ImportError:
    from config import SESSION_FILE, ensure_data_dirs, ensure_utf8_stdio

ensure_utf8_stdio()

LOGIN_URL_KEYWORDS = (
    "/login",
    "accounts.google.com",
    "appleid.apple.com",
    "twitter.com",
    "x.com",
)


def _launch_interactive_browser(playwright):
    """Google Chrome を優先起動し、未導入環境では同梱 Chromium にフォールバック"""
    launch_args = [
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
    ]
    common_kwargs = {
        "headless": False,
        "ignore_default_args": ["--enable-automation"],
        "args": launch_args,
    }
    try:
        return playwright.chromium.launch(channel="chrome", **common_kwargs)
    except Exception:
        print(">> Google Chrome が見つからないため、同梱 Chromium を起動します...")
        return playwright.chromium.launch(**common_kwargs)


def login_with_credentials(identifier: str, password: str) -> bool:
    """メールアドレス/note ID とパスワードを用いてヘッドレスでログインしセッションを保存"""
    ensure_data_dirs()

    print("=" * 65)
    print(" [ハリーのnote実験記録 バックアップ：ID・パスワード認証]")
    print("=" * 65)
    print(">> ヘッドレスブラウザで https://note.com/login に接続中...")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
            ],
        )
        try:
            context = browser.new_context(
                viewport={"width": 1280, "height": 850},
                locale="ja-JP",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            )
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
            )
            page = context.new_page()

            try:
                page.goto("https://note.com/login", wait_until="networkidle", timeout=30000)
            except Exception:
                page.goto("https://note.com/login", wait_until="domcontentloaded", timeout=30000)
            time.sleep(2.0)

            id_input = page.get_by_label("メールアドレス または note ID")
            pw_input = page.get_by_label("パスワード")
            id_input.click()
            time.sleep(0.2)
            id_input.fill(identifier)
            pw_input.click()
            time.sleep(0.2)
            pw_input.fill(password)
            time.sleep(0.3)

            submit_btn = page.get_by_role("button", name="ログイン", exact=True)
            if submit_btn.is_disabled():
                print("[ERROR] ログインボタンが有効化されませんでした。入力値を確認してください。")
                return False

            submit_btn.click()

            logged_in = False
            for _ in range(15):
                time.sleep(1.0)
                current_url = page.url
                cookies = context.cookies()
                has_session = any("note_session" in c.get("name", "") for c in cookies)
                if "note.com" in current_url and "/login" not in current_url and has_session:
                    logged_in = True
                    break

            if not logged_in:
                print("[ERROR] ログインに失敗しました。Google連携アカウントの場合は「ブラウザログイン」または「Cookie貼り付け」をご利用ください。")
                return False

            page.goto("https://note.com/dashboard", wait_until="domcontentloaded", timeout=30000)
            time.sleep(2.5)
            if "login" in page.url:
                print("[ERROR] ダッシュボードへの遷移確認に失敗しました。")
                return False

            context.storage_state(path=str(SESSION_FILE))
            print(f"[OK] セッション情報を保存しました: {SESSION_FILE}")
            return True
        finally:
            browser.close()


def run_login(timeout_seconds: int = 300) -> bool:
    """PCデスクトップ上のブラウザで手動ログインを実行し session_state.json を保存"""
    ensure_data_dirs()

    print("=" * 65)
    print(" [ハリーのnote実験記録 バックアップ：PCブラウザ手動ログイン]")
    print("=" * 65)
    print("1. PC画面に Chrome ウィンドウが開きます。")
    print("2. Googleログイン等で note にサインインしてください。")
    print("3. ダッシュボード遷移確認後、自動でセッションを保存して閉じます。")
    print("-" * 65)

    with sync_playwright() as p:
        browser = _launch_interactive_browser(p)
        try:
            context = browser.new_context(
                viewport={"width": 1280, "height": 850},
                locale="ja-JP",
            )
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
            )
            page = context.new_page()

            print(">> PC画面に note ログイン画面を開いています...")
            page.goto("https://note.com/login", wait_until="domcontentloaded")
            try:
                page.bring_to_front()
            except Exception:
                pass
            print(f">> ログイン完了を待機しています（最大 {timeout_seconds // 60} 分間）...")

            start_time = time.time()
            last_log_time = 0.0
            logged_in = False

            while time.time() - start_time < timeout_seconds:
                time.sleep(2)
                try:
                    current_url = page.url
                except Exception:
                    break

                if time.time() - last_log_time > 10:
                    last_log_time = time.time()
                    print(f"  [待機中] 現在のURL: {current_url[:70]}")

                is_login_page = any(kw in current_url for kw in LOGIN_URL_KEYWORDS)
                cookies = context.cookies()
                has_session = any("note_session" in c.get("name", "") for c in cookies)

                if "note.com" in current_url and not is_login_page and has_session:
                    print(">> ログイン成功を検知しました。")
                    logged_in = True
                    break

            if not logged_in:
                print("[ERROR] タイムアウト、またはブラウザが閉じられました。")
                return False

            time.sleep(2)
            print(">> ダッシュボード (https://note.com/dashboard) へのアクセスを確認中...")
            page.goto("https://note.com/dashboard", wait_until="domcontentloaded")
            time.sleep(3)

            if "login" in page.url:
                print("[ERROR] ダッシュボードへのアクセスに失敗しました。再度ログインをお試しください。")
                return False

            context.storage_state(path=str(SESSION_FILE))
            print(f"[OK] セッション情報を保存しました: {SESSION_FILE}")
            time.sleep(1)
            return True
        finally:
            browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="note ログイン・セッション保存ツール")
    parser.add_argument("--credentials-stdin", action="store_true", help="標準入力JSONから直接認証")
    args = parser.parse_args()

    if args.credentials_stdin:
        try:
            data = json.loads(sys.stdin.read())
            ident = str(data.get("identifier", "")).strip()
            pw = str(data.get("password", ""))
        except Exception as e:
            print(f"[ERROR] 認証情報の読み取りに失敗しました: {e}")
            sys.exit(1)
        if not ident or not pw or not login_with_credentials(ident, pw):
            sys.exit(1)
    elif not run_login():
        sys.exit(1)
