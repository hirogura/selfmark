# selfmark

シンプルなブックマーク管理 Web アプリです。Flask 製の単一ファイル (`app.py`) で動作し、LXD コンテナなどの Ubuntu 環境に 1 コマンドでインストールできます。

- GitHub: https://github.com/hirogura/selfmark
- ポート 80 で公開（Tailscale の IP / MagicDNS からアクセス可能）
- ブックマークデータは `/opt/lxd-data/selfmark/bookmarks.json` に保存されます（このファイルは **GitHub には公開されません**）

## 主な機能

- ブックマークの追加・編集・削除
- カテゴリー分類、カテゴリーごとの並び替え（ドラッグ＆ドロップ）
- お気に入り（スター）登録
- ファビコン表示（自動取得・手動アップロード）
- JSON エクスポート / インポート
- Google Chrome 拡張機能からの追加・閲覧（下記参照）

## インストール方法（GitHub から）

必要なもの: `python3` と `curl`（Ubuntu / Debian）

```bash
curl -fsSL -o install-selfmark1.sh \
  https://raw.githubusercontent.com/hirogura/selfmark/main/install-selfmark1.sh

sudo bash install-selfmark1.sh
```

インストールが完了すると、以下の情報が表示されます。

- Web UI の URL（Tailscale の IP または MagicDNS ホスト名）
- インストール先: `/opt/lxd-data/selfmark`
- 管理コマンド（`systemctl status selfmark` / `systemctl restart selfmark`）

スクリプトの内容は以下の処理を行います。

1. GitHub から `app.py` をダウンロード
2. Python 仮想環境 (`venv`) の作成と依存パッケージ（Flask, Pillow）のインストール
3. systemd サービス (`selfmark.service`) の登録と起動
4. Tailscale の IP / ホスト名の確認

### アップデート方法

GitHub に最新版が公開された場合、`app.py` を再ダウンロードして再起動するだけで反映されます。

```bash
sudo curl -fsSL \
  https://raw.githubusercontent.com/hirogura/selfmark/main/app.py \
  -o /opt/lxd-data/selfmark/app.py
sudo systemctl restart selfmark
```

## Google Chrome 拡張機能（selfmark-extension-v15.zip）

ブラウザからブックマークを追加・検索・閲覧できる Chrome 拡張機能です（Manifest V3）。

**主な機能**

- 現在開いているタブの URL を 1 クリックで selfmark に追加
- ブックマークの検索・閲覧（カテゴリー / お気に入りで分類表示）
- ブラウザ上から編集・削除・お気に入り登録が可能

**導入方法**

1. 拡張機能 ZIP をダウンロードします。

   ```bash
   curl -fsSL -o selfmark-extension-v15.zip \
     https://raw.githubusercontent.com/hirogura/selfmark/main/selfmark-extension-v15.zip
   unzip -d selfmark-extension selfmark-extension-v15.zip
   ```

2. Chrome で `chrome://extensions` を開きます。
3. 右上の「デベロッパーモード」を ON にします。
4. 「パッケージ化されていない拡張機能を読み込む」をクリックし、解凍した `selfmark-extension` フォルダを選択します。
5. ツールバーの selfmark アイコンをクリック → 設定（歯車）を開き、selfmark の Web UI の URL を入力して「保存」します。

これでブラウザから直接ブックマークを操作できます。

## selfmark-sub（閲覧専用ビュー）のインストール方法

selfmark-sub は `bookmarks.json` を読み込んで、編集不可の軽量な閲覧専用ページを表示します。既定のポートは 81 です。

```bash
curl -fsSL -o install-selfmark-sub1.sh \
  https://raw.githubusercontent.com/hirogura/selfmark/main/install-selfmark-sub1.sh

sudo bash install-selfmark-sub1.sh /opt/lxd-data/selfmark/bookmarks.json 81
```

引数: 第 1 引数 = `bookmarks.json` のパス（既定: `/opt/lxd-data/selfmark/bookmarks.json`）、第 2 引数 = ポート番号（既定: 81）。

インストール後、表示された URL（`http://<ホストIP>:81/`）にアクセスしてください。

**管理コマンド**

```bash
systemctl status selfmark-sub    # 状態確認
systemctl restart selfmark-sub   # 再起動
systemctl stop selfmark-sub      # 停止
```

## アンインストール方法

### selfmark（Web アプリ本体）

```bash
sudo systemctl disable --now selfmark
sudo rm /etc/systemd/system/selfmark.service
sudo systemctl daemon-reload
sudo rm -rf /opt/lxd-data/selfmark
```

> **注意:** `/opt/lxd-data/selfmark` にはブックマークデータ（`bookmarks.json`）とファビコンキャッシュも含まれます。削除前に必要なデータは Web UI の「エクスポート」で JSON ファイルにバックアップしてください。

### selfmark-sub（閲覧専用ビュー）

```bash
sudo systemctl disable --now selfmark-sub
sudo rm /etc/systemd/system/selfmark-sub.service
sudo systemctl daemon-reload
sudo rm -rf /opt/lxd-data/selfmark-sub
```

## データについて

- ブックマークデータはインストール先の `bookmarks.json` に保存されます。
- このリポジトリには個人情報（`bookmarks.json`・フィードの OPML 等）は含まれていません。`.gitignore` で除外しています。
- バックアップは Web UI の「📥 エクスポート」で JSON ファイルをダウンロードできます。
