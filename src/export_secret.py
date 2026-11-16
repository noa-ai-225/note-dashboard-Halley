"""GitHub Actions 用認証シークレット出力ツール (src/export_secret.py)"""

import base64
import sys
from typing import Optional

try:
    from .config import SESSION_FILE, ensure_utf8_stdio
except ImportError:
    from config import SESSION_FILE, ensure_utf8_stdio

ensure_utf8_stdio()


def encode_session_base64() -> Optional[str]:
    """session_state.json を Base64 文字列へ変換して返す"""
    if not SESSION_FILE.exists():
        return None
    return base64.b64encode(SESSION_FILE.read_bytes()).decode("ascii")


if __name__ == "__main__":
    encoded = encode_session_base64()
    if not encoded:
        print(f"[ERROR] セッションファイルが見つかりません: {SESSION_FILE}")
        sys.exit(1)

    print("=" * 65)
    print(" GitHub Secrets (NOTE_SESSION_STATE_B64) 登録用データ")
    print(" ※ GitHubリポジトリは必ず Private（非公開）で使用してください。")
    print("=" * 65)
    print(encoded)
    print("=" * 65)
