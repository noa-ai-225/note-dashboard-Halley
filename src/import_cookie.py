"""Cookie 手動インポートツール (src/import_cookie.py)"""

import argparse
import json
import re
import sys
import time
from typing import Dict, List
from playwright.sync_api import sync_playwright

try:
    from .config import SESSION_FILE, ensure_data_dirs, ensure_utf8_stdio
except ImportError:
    from config import SESSION_FILE, ensure_data_dirs, ensure_utf8_stdio

ensure_utf8_stdio()


def _build_session_payload(raw_input_str: str) -> Dict[str, List[dict]]:
    """入力文字列から Playwright 用 storage_state 辞書を構築"""
    cleaned = raw_input_str.strip().strip("\"'")

    m_v5 = re.search(r"_note_session_v5\s*=\s*([^;\s\"']+)", cleaned)
    m_v1 = re.search(r"(?<!_v5)_note_session\s*=\s*([^;\s\"']+)", cleaned)
    if m_v5:
        cookie_pairs = [("_note_session_v5", m_v5.group(1))]
    elif m_v1:
        cookie_pairs = [("_note_session", m_v1.group(1))]
    else:
        token = cleaned.split(";")[0].strip().strip("\"'")
        cookie_pairs = [
            ("_note_session_v5", token),
            ("_note_session", token),
        ]

    cookies = []
    for name, val in cookie_pairs:
        cookies.append(
            {
                "name": name,
                "value": val,
                "domain": ".note.com",
                "path": "/",
                "expires": -1,
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax",
            }
        )

    return {"cookies": cookies, "origins": []}


def _verify_and_save_payload(payload: Dict[str, List[dict]]) -> bool:
    """メモリ上の storage_state で note.com/dashboard へ疎通確認し、成功時のみ保存"""
    print(">> 取り込んだCookieの有効性を検証中 (https://note.com/dashboard)...")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        try:
            context = browser.new_context(
                storage_state=payload,  # type: ignore[arg-type]
                viewport={"width": 1280, "height": 850},
                locale="ja-JP",
            )
            page = context.new_page()
            page.goto("https://note.com/dashboard", wait_until="domcontentloaded", timeout=30000)
            time.sleep(2.5)

            if "login" in page.url:
                return False

            context.storage_state(path=str(SESSION_FILE))
            return True
        finally:
            browser.close()


def import_cookie(cookie_value: str | None = None, skip_verify: bool = False) -> bool:
    """Cookie値を受け取り、検証後に session_state.json を保存"""
    ensure_data_dirs()

    if not cookie_value:
        print("=" * 65)
        print(" [note セッション手動インポート（Cookie取り込み）]")
        print("=" * 65)
        print("1. 普段のブラウザで https://note.com （ログイン済み）を開きます。")
        print("2. [F12] キー -> [Application] -> [Cookies] -> https://note.com")
        print("3. 『_note_session_v5』 または 『_note_session』 の Value をコピーしてください。")
        print("-" * 65)
        cookie_value = input("コピーした Value（値）を貼り付けて Enter を押してください:\n> ").strip()

    if not cookie_value or not cookie_value.strip("\"' ;"):
        print("[ERROR] 値が入力されませんでした。処理を中断します。")
        return False

    payload = _build_session_payload(cookie_value)

    if skip_verify:
        with open(SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
    else:
        try:
            is_valid = _verify_and_save_payload(payload)
        except Exception as e:
            print(f"[WARN] 検証中にエラーが発生したため、未検証のまま保存します: {e}")
            with open(SESSION_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            return True

        if not is_valid:
            print("[ERROR] ログイン画面へリダイレクトされました。Cookieが期限切れまたは誤りの可能性があります。")
            return False

    print(f"[OK] セッションファイルを保存しました: {SESSION_FILE}")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="note セッションCookie手動インポート")
    parser.add_argument("--cookie", help="Cookie Value を直接指定")
    parser.add_argument("--cookie-stdin", action="store_true", help="標準入力から Cookie Value を読み取る")
    parser.add_argument("--skip-verify", action="store_true", help="疎通検証を省略")
    args = parser.parse_args()

    cookie_val = sys.stdin.read().strip() if args.cookie_stdin else args.cookie
    if not import_cookie(cookie_value=cookie_val, skip_verify=args.skip_verify):
        sys.exit(1)
