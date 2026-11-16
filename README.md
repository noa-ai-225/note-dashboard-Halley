# ハリーのnote実験記録 — 時間別観測＆バックアップラボ (`harry-note-lab`)

**「一緒に試して、noteを育てよう」**  
[ハリー | note実験記録](https://note.com/hurry_room) の世界観・実験スタイルに合わせて設計された、noteダッシュボードの時間別自動観測・増減記録ツールです。

毎日 **9:00 / 14:00 / 20:00 / 23:00** の4回、**インプレッション・PV・スキ数・コメント数・フォロワー数** を自動取得し、時間帯ごとの推移と前回比増減を一覧・グラフで確認できます。

---

## 主な特徴

- **5指標を自動取得**: ダッシュボードの **インプレッション / ページビュー(PV) / スキ数 / コメント数** に加え、アカウントの現在 **フォロワー数** を自動で同時取得します。
- **時間ごとの記録 ＆ 増減表示**: `09:00` / `14:00` / `20:00` / `23:00` の4スロット比較カードと、全観測タイムライン・ミニ推移グラフで「どの時間帯に伸びたか」を一目で把握できます。
- **PCは初回ログインのみ、以後はスマホ・iPad対応**:
  - **パターンA（自宅PC常駐 ＋ スマホ/iPad操作）**: スマホ・iPadのブラウザから定時観測のON/OFF・時刻変更・手動取得・CSV/DBダウンロードが可能。
  - **パターンB（GitHub Actions 完全クラウド実行）**: Privateリポジトリに Secrets を1回登録すれば、PC電源オフでも毎日 9時・14時・20時・23時（JST）に自動観測され、GitHubスマホアプリから閲覧できます。
- **Excel対応CSV ＆ SQLite時系列保存**:
  - `data/exports/note_hourly_history.csv`: 時間ごとの5指標＋増減履歴
  - `data/exports/note_articles_latest.csv`: 記事別最新スナップショット＋増減
  - `data/note_stats.db`: 上書きしないSQLite時系列データベース

---

## セットアップ（Windows）

1. 本リポジトリをダウンロード（または `git clone`）します。
2. `scripts\setup_windows.bat` をダブルクリックして仮想環境（`.venv`）と Playwright Chromium を自動導入します。
3. `run_ui_lan.bat` をダブルクリックして管理UIを起動します（PCブラウザは `http://127.0.0.1:8766`、スマホ／iPadは画面内のLAN URL）。
4. PC画面上で **「方法1: PCブラウザ」**（Google連携／2段階認証対応）または **「方法2: Cookie貼り付け」** から1回だけログインします。

### macOS / Linux の場合

```bash
git clone https://github.com/noa-ai-225/note-dashboard-Halley.git
cd note-dashboard-Halley
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
python src/ui_server.py --lan
```

---

## スマホ・iPadでの運用方法

### パターンA：自宅PC常駐 ＋ スマホ／iPad操作（おすすめ）
1. PCで初回ログイン完了後、以下を実行するとPCログオン時にUIサーバーと4時刻スケジューラが裏で自動起動します：
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\register_ui_autostart_windows.ps1
   ```
2. スマホ・iPadのブラウザで `http://<PCのIPアドレス>:8766` を開き、「ホーム画面に追加」しておけば、以後は携帯端末から記録・増減・グラフ・CSV保存・時刻変更をすべて操作できます。

### パターンB：PC電源オフでも動く完全クラウド運用（GitHub Actions）
1. 本リポジトリを **Private（非公開）** リポジトリとして複製します（※Publicリポジトリ誤公開防止のため、Publicでは自動コミットが停止します）。
2. PCで初回ログイン後、管理UIの詳細設定にある「`NOTE_SESSION_STATE_B64` を生成」（または `python src/export_secret.py`）の文字列をコピーし、GitHubリポジトリの **Settings → Secrets and variables → Actions** へ登録します。
3. `.github/workflows/scheduled_backup.yml` により毎日 JST **09:00 / 14:00 / 20:00 / 23:00** に自動実行され、`data/exports/note_hourly_history.csv` と `note_articles_latest.csv` をGitHubスマホアプリからそのまま閲覧できます。

---

## 出力データ仕様 (`data/exports/`)

- **`note_hourly_history.csv`**（時間別推移＋増減）:
  `captured_at`, `snapshot_date`, `time_slot`, `capture_type`, `impressions`, `diff_impressions`, `page_views`, `diff_page_views`, `likes`, `diff_likes`, `comments`, `diff_comments`, `follower_count`, `diff_follower_count`, `follower_growth_28d`, `note_count`, `aggregation_at`
- **`note_articles_latest.csv`**（記事別最新＋増減）:
  `captured_at`, `time_slot`, `capture_type`, `article_id`, `status`, `title`, `published_at`, `impressions`, `diff_impressions`, `page_views`, `diff_pv`, `likes`, `diff_likes`, `comments`, `diff_comments`, `sales`, `url`

---

## セキュリティ・注意事項

- `data/auth/`, `data/raw/`, `data/exports/`, `data/logs/`, `data/*.db`, `config/settings.local.json` は `.gitignore` により除外されています。認証情報や実データを公開リポジトリへコミットしないでください。
- 9時・14時・20時・23時（最短3時間間隔）を確実に観測するため、過剰アクセス防止のクールダウン下限を `2.0時間` としています。

## License

[MIT License](LICENSE)
