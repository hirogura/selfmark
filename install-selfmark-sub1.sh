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

# ── Tailscale 検出（あれば Tailscale Serve で HTTPS 化する）──
TS_HTTPS=0
if command -v tailscale >/dev/null 2>&1 && tailscale status >/dev/null 2>&1; then
  TS_HTTPS=1
fi

BOOKMARKS_JSON="${1:-/opt/lxd-data/selfmark/bookmarks.json}"
PORT="${2:-3357}"
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
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn

DATA_FILE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "selfmark", "bookmarks.json")
APP_VERSION = "1.0.0"
SERVICE_NAME = os.environ.get("SELFMARK_SERVICE", "selfmark-sub")
INSTALLER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "install-selfmark-sub1.sh")
INSTALLER_URL = "https://raw.githubusercontent.com/hirogura/selfmark/main/install-selfmark-sub1.sh"

_cache_html = None
_cache_mtime = 0

# iOS/iPadOS Safariが自動で探索してくるアイコン系パス
# (apple-touch-iconをHTML側で明示していても念のため即404で返す)
ICON_PATHS = {
    "/favicon.ico",
    "/apple-touch-icon.png",
    "/apple-touch-icon-precomposed.png",
}

ICON_FILES = {
    "/favicon.png",
    "/selfmark.png",
}

ICON_DATA_URI = "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect width='100' height='100' rx='20' fill='%23f5f5f5'/><text x='50' y='65' font-size='42' font-weight='bold' fill='%230f0f23' text-anchor='middle' font-family='Arial'>S</text></svg>"

# 管理UI用の追加CSS・JS（通常文字列なので波括弧はそのまま使える）
ADMIN_CSS = """
.topbar { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }
.topbar h1 { margin-bottom: 0; }
.header-admin { margin-left: auto; display: flex; align-items: center; gap: 8px; padding-left: 12px; }
.btn-admin { padding: 5px 12px; border-radius: 8px; font-size: 12px; font-weight: 600; cursor: pointer; border: 1px solid #ccc; background: #fff; color: #333; transition: background .15s ease; white-space: nowrap; }
.btn-admin:hover { background: #e8e8f0; }
.btn-admin:disabled { opacity: .5; cursor: wait; }
.version-label { font-size: 12px; font-weight: 600; color: #888; white-space: nowrap; }
"""

ADMIN_JS = """
async function waitForServer(maxMs = 60000) {
  const deadline = Date.now() + maxMs;
  await new Promise(r => setTimeout(r, 2000));
  while (Date.now() < deadline) {
    try { const res = await fetch('/api/version', { cache: 'no-store' }); if (res.ok) return true; } catch(e) {}
    await new Promise(r => setTimeout(r, 1000));
  }
  return false;
}

async function adminRestart() {
  if (!confirm('selfmark-sub サービスを再起動しますか？')) return;
  const b = document.getElementById('btnAdminRestart');
  b.disabled = true; b.textContent = '再起動中…';
  try { await fetch('/api/admin/restart', { method: 'POST' }); } catch(e) {}
  await waitForServer();
  location.reload();
}

async function adminUpdate() {
  if (!confirm('GitHub から最新版を取得してアップデートしますか？\\n完了後、サービスは自動で再起動されます。')) return;
  const b = document.getElementById('btnAdminUpdate');
  b.disabled = true; b.textContent = '更新中…';
  try {
    const res = await fetch('/api/admin/update', { method: 'POST' });
    const d = await res.json();
    if (!res.ok || d.error) { alert('アップデートに失敗しました\\n' + (d.error || '')); b.disabled = false; b.textContent = 'アップデート'; return; }
    b.textContent = '再起動中…';
  } catch(e) { alert('アップデートに失敗しました'); b.disabled = false; b.textContent = 'アップデート'; return; }
  await waitForServer(120000);
  location.reload();
}

fetch('/api/version').then(res => res.json()).then(v => { document.getElementById('appVersion').textContent = 'v.' + v.version; }).catch(() => {});
"""


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;").replace("'", "&#39;")


def build_html():
    try:
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
    except Exception:
        data = {}

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
{ADMIN_CSS}
</style>
</head>
<body>
<div class="topbar">
<h1>selfmark</h1>
<div class="header-admin">
<button class="btn-admin" id="btnAdminUpdate" title="GitHubから最新版を取得してアップデート" onclick="adminUpdate()">アップデート</button>
<button class="btn-admin" id="btnAdminRestart" title="selfmark-subサービスを再起動" onclick="adminRestart()">再起動</button>
<span class="version-label" id="appVersion"></span>
</div>
</div>
{body}
<script>{ADMIN_JS}</script>
</body>
</html>"""


class Handler(SimpleHTTPRequestHandler):
    def _send_json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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

        if path in ICON_FILES:
            file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), path.lstrip("/"))
            if os.path.exists(file_path):
                with open(file_path, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
            return

        if path == "/api/version":
            return self._send_json({"version": APP_VERSION})

        try:
            mtime = os.path.getmtime(DATA_FILE)
        except Exception:
            mtime = -1
        if mtime != _cache_mtime:
            _cache_html = build_html().encode("utf-8")
            _cache_mtime = mtime
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(_cache_html)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(_cache_html)

    def do_POST(self):
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)

        if path == "/api/admin/restart":
            def _restart():
                time.sleep(0.8)
                subprocess.run(["systemctl", "restart", SERVICE_NAME],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            threading.Thread(target=_restart, daemon=True).start()
            return self._send_json({"ok": True})

        if path == "/api/admin/update":
            try:
                req = urllib.request.Request(INSTALLER_URL, headers={"User-Agent": "selfmark-updater"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    new_src = resp.read().decode("utf-8")
                if not new_src.lstrip().startswith("#!/bin/bash"):
                    return self._send_json({"error": "ダウンロードしたスクリプトが不正です"}, 500)
                current = ""
                if os.path.exists(INSTALLER_PATH):
                    try:
                        with open(INSTALLER_PATH, encoding="utf-8") as f:
                            current = f.read()
                    except Exception:
                        current = ""
                if new_src == current:
                    return self._send_json({"ok": True, "updated": False, "message": "すでに最新版です"})
                tmp_path = INSTALLER_PATH + ".new"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    f.write(new_src)
                chk = subprocess.run(["bash", "-n", tmp_path], capture_output=True)
                if chk.returncode != 0:
                    os.remove(tmp_path)
                    return self._send_json({"error": "ダウンロードしたインストーラの構文チェックに失敗しました"}, 500)
                os.replace(tmp_path, INSTALLER_PATH)
            except Exception as e:
                return self._send_json({"error": str(e)}, 500)

            args = [INSTALLER_PATH] + sys.argv[1:]

            def _apply():
                time.sleep(1.0)
                subprocess.run(["bash"] + args,
                               stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            threading.Thread(target=_apply, daemon=True).start()
            return self._send_json({"ok": True, "updated": True, "message": "アップデート完了。サービスを再起動します"})

        return self._send_json({"error": "Not found"}, 404)

    def log_message(self, format, *args):
        pass


# ThreadingMixInで複数リクエストを並列処理
# (Safariが同時に複数本リクエストを送ってきても直列待ちにならないようにする)
class DualStackHTTPServer(ThreadingMixIn, HTTPServer):
    address_family = socket.AF_INET6
    daemon_threads = True


if __name__ == "__main__":
    port = int(sys.argv[2]) if len(sys.argv) > 2 else int(os.environ.get("PORT", "3357"))
    host = os.environ.get("HOST", "")
    print(f"[INFO] selfmark-view: http://{host or '[::]'}:{port}", flush=True)
    if host:
        HTTPServer((host, port), Handler).serve_forever()
    else:
        DualStackHTTPServer(("", port), Handler).serve_forever()
APPEOF
chmod +x "$INSTALL_DIR/app.py"
echo "  app.py を書き込みました"

echo "[1.5/3] アイコンファイルをコピー..."
SELFMARK_DIR="/opt/lxd-data/selfmark"
for ICON_FILE in favicon.png selfmark.png; do
  if [[ -f "${SELFMARK_DIR}/${ICON_FILE}" ]]; then
    cp "${SELFMARK_DIR}/${ICON_FILE}" "${INSTALL_DIR}/${ICON_FILE}"
    echo "  ${ICON_FILE} をコピーしました"
  else
    echo "  ${ICON_FILE} が見つかりません（スキップ）"
  fi
done

echo "[2/3] systemdサービス設定: $SERVICE_FILE"
# Tailscale Serve が TLS 終端用に <tailscale IP>:PORT をバインドできるよう、
# tailscale 接続環境ではアプリを 127.0.0.1 のみで待機させる
HOST_ENV=""
if [[ "${TS_HTTPS}" == "1" ]]; then
  HOST_ENV="Environment=HOST=127.0.0.1"
fi
cat > "$SERVICE_FILE" << SVCEOF
[Unit]
Description=selfmark-sub (read-only bookmark viewer)
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 $INSTALL_DIR/app.py $BOOKMARKS_JSON $PORT
Restart=on-failure
RestartSec=5
Environment=PORT=$PORT
${HOST_ENV}

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
    # ── Tailscale Serve で HTTPS 公開（冪等）──
    TS_DOMAIN=""
    if [[ "${TS_HTTPS}" == "1" ]]; then
        TS_DOMAIN=$(tailscale status --json 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin).get('Self', {}).get('DNSName', '').rstrip('.'))" 2>/dev/null || true)
        echo "[Tailscale] Serve (HTTPS) を設定..."
        tailscale serve --https=$PORT off >/dev/null 2>&1 || true
        if tailscale serve --bg --https=$PORT "http://127.0.0.1:$PORT" >/dev/null; then
            for i in $(seq 1 12); do
                HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "https://${TS_DOMAIN}:${PORT}/" 2>/dev/null || echo "000")
                [[ "${HTTP_CODE}" != "000" ]] && break
                sleep 5
            done
        else
            echo "[WARN] tailscale serve の設定に失敗しました（HTTP のみで動作します）"
        fi
    fi
    echo ""
    echo "=== 完了 ==="
    if [[ "${TS_HTTPS}" == "1" && -n "${TS_DOMAIN}" ]]; then
        echo "URL: https://${TS_DOMAIN}:${PORT}/  (Tailscale Serve / HTTPS)"
    else
        echo "URL: http://$(hostname -I | awk '{print $1}'):$PORT/"
    fi
    echo "状態: systemctl status $SERVICE_NAME"
    echo "停止: systemctl stop $SERVICE_NAME"
    echo "解除: systemctl disable --now $SERVICE_NAME && tailscale serve --https=$PORT off"
else
    echo "[ERROR] サービス起動に失敗"
    systemctl status "$SERVICE_NAME" --no-pager
    exit 1
fi
