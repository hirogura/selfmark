#!/bin/bash
# install-selfmark-sub2.sh
# selfmark-sub2 (閲覧専用・キャッシュ対応) インストールスクリプト
# Usage: ./install-selfmark-sub2.sh [bookmarks.jsonのパス] [ポート番号]
#
# 修正内容 (iOS/iPad初回表示遅延対策):
#  1. apple-touch-icon等のアイコン系パスを即404で返すガードを追加
#  2. <head>にapple-touch-iconを明示してSafariの自動探索リクエスト自体を抑制
#  3. HTTPServerをThreadingMixIn化し、複数リクエストを並列処理できるように変更

set -e

BOOKMARKS_JSON="${1:-/opt/lxd-data/selfmark/bookmarks.json}"
PORT="${2:-81}"
INSTALL_DIR="/opt/lxd-data/selfmark-sub"
SERVICE_NAME="selfmark-sub"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

echo "=== selfmark-sub2 installer ==="
echo "bookmarks.json: $BOOKMARKS_JSON"
echo "port: $PORT"
echo ""

echo "[1/3] インストール先: $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"

cat > "$INSTALL_DIR/app.py" << 'APPEOF'
#!/usr/bin/env python3
"""selfmark-view: 閲覧専用の静的ブックマークページ"""

import json
import os
import socket
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn

DATA_FILE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "selfmark", "bookmarks.json")

_cache_html = None
_cache_mtime = 0

# iOS/iPadOS Safariが自動で探索してくるアイコン系パス
# (apple-touch-iconをHTML側で明示していても念のため即404で返す)
ICON_PATHS = {
    "/favicon.ico",
    "/apple-touch-icon.png",
    "/apple-touch-icon-precomposed.png",
}

ICON_DATA_URI = "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect width='100' height='100' rx='20' fill='%23f5f5f5'/><text x='50' y='65' font-size='42' font-weight='bold' fill='%230f0f23' text-anchor='middle' font-family='Arial'>S</text></svg>"


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;").replace("'", "&#39;")


def build_html():
    try:
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
    except Exception:
        return "<html><body><p>bookmarks.json not found</p></body></html>"

    bookmarks = data.get("bookmarks", [])
    cat_order = data.get("cat_order", [])
    starred_order = data.get("starred_order", [])

    starred = [bm for bm in bookmarks if bm.get("starred")]

    ordered_starred = []
    if starred_order:
        url_map = {bm["url"]: bm for bm in starred}
        ordered_starred = [url_map[u] for u in starred_order if u in url_map]

    categories = {}
    for bm in bookmarks:
        cat = bm.get("category", "") or "未分類"
        categories.setdefault(cat, []).append(bm)

    if cat_order:
        sorted_cats = list(dict.fromkeys(cat_order + [c for c in categories if c not in cat_order]))
    else:
        sorted_cats = sorted(categories.keys())

    rows = []

    if ordered_starred:
        rows.append('<h2 class="star">⭐ お気に入り</h2>')
        rows.append("<ul>")
        for bm in ordered_starred:
            rows.append(f'<li><a href="{esc(bm["url"])}" target="_blank">{esc(bm["name"])}</a></li>')
        rows.append("</ul>")

    for cat in sorted_cats:
        items = categories.get(cat, [])
        if not items:
            continue
        rows.append(f'<h2>{esc(cat)}</h2>')
        rows.append("<ul>")
        for bm in items:
            rows.append(f'<li><a href="{esc(bm["url"])}" target="_blank">{esc(bm["name"])}</a></li>')
        rows.append("</ul>")

    body = "".join(rows)
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>selfmark</title>
<link rel="icon" href="{ICON_DATA_URI}">
<link rel="apple-touch-icon" href="{ICON_DATA_URI}">
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f5; color: #333; padding: 16px; max-width: 600px; margin: 0 auto; }}
h1 {{ font-size: 20px; margin-bottom: 16px; color: #0f0f23; }}
h2 {{ font-size: 13px; color: #666; margin: 24px 0 8px; }}
h2.star {{ color: #f39c12; }}
ul {{ list-style: none; }}
li {{ margin-bottom: 2px; }}
a {{ display: block; padding: 10px 12px; background: #fff; border-radius: 8px; text-decoration: none; color: #333; font-size: 15px; border: 1px solid #eee; }}
a:active {{ background: #e8e8f0; }}
</style>
</head>
<body>
<h1>selfmark</h1>
{body}
</body>
</html>"""


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        global _cache_html, _cache_mtime

        # iOS/iPadOS Safariのアイコン自動探索リクエストは即404で返し、
        # 本体処理(build_html)を挟まないようにする
        path = self.path.split("?")[0]
        if path in ICON_PATHS or path.startswith("/apple-touch-icon"):
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        try:
            mtime = os.path.getmtime(DATA_FILE)
        except Exception:
            mtime = 0
        if mtime != _cache_mtime:
            _cache_html = build_html().encode("utf-8")
            _cache_mtime = mtime
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(_cache_html)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(_cache_html)

    def log_message(self, format, *args):
        pass


# ThreadingMixInで複数リクエストを並列処理
# (Safariが同時に複数本リクエストを送ってきても直列待ちにならないようにする)
class DualStackHTTPServer(ThreadingMixIn, HTTPServer):
    address_family = socket.AF_INET6
    daemon_threads = True


if __name__ == "__main__":
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 81
    print(f"[INFO] selfmark-view: http://[::]:{port}", flush=True)

    DualStackHTTPServer(("", port), Handler).serve_forever()
APPEOF
chmod +x "$INSTALL_DIR/app.py"
echo "  app.py を書き込みました"

echo "[2/3] systemdサービス設定: $SERVICE_FILE"
cat > "$SERVICE_FILE" << SVCEOF
[Unit]
Description=selfmark-sub (read-only bookmark viewer)
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 $INSTALL_DIR/app.py $BOOKMARKS_JSON $PORT
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
SVCEOF
echo "  サービスファイルを書き込みました"

echo "[3/3] サービス有効化・起動"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

sleep 1
if systemctl is-active --quiet "$SERVICE_NAME"; then
    echo ""
    echo "=== 完了 ==="
    echo "URL: http://$(hostname -I | awk '{print $1}'):$PORT/"
    echo "状態: systemctl status $SERVICE_NAME"
    echo "停止: systemctl stop $SERVICE_NAME"
    echo "解除: systemctl disable --now $SERVICE_NAME"
else
    echo "[ERROR] サービス起動に失敗"
    systemctl status "$SERVICE_NAME" --no-pager
    exit 1
fi
