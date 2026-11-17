#!/usr/bin/env python3
"""selfmark — シンプルなブックマーク管理"""

import json
import os
import subprocess
import sys
import shutil
import threading
import time
from flask import Flask, Response, request, jsonify

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bookmarks.json")
APP_PATH = os.path.abspath(__file__)
APP_VERSION = "1.3.1"
SERVICE_NAME = os.environ.get("SELFMARK_SERVICE", "selfmark")
GITHUB_RAW_APP = "https://raw.githubusercontent.com/hirogura/selfmark/main/app.py"
GITHUB_RAW_BASE = "https://raw.githubusercontent.com/hirogura/selfmark/main"
SUB_INSTALLER_URL = "https://raw.githubusercontent.com/hirogura/selfmark/main/install-selfmark-sub1.sh"
SUB_PORT = "3357"
EXTENSION_URL = "https://raw.githubusercontent.com/hirogura/selfmark/main/selfmark-extension-v17.zip"
EXTENSION_FILENAME = "selfmark-extension-v17.zip"
SYNC_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sync_config.json")
SYNC_ROLE_SOURCE = "source"
SYNC_ROLE_DEST = "destination"
DEFAULT_SYNC_CONFIG = {
    "role": SYNC_ROLE_SOURCE,
    "peer": "",
    "peer_name": "",
    "sync_time": "03:00",
    "last_sync": "",
    "last_result": "",
}
app = Flask(__name__)


DEFAULT_DATA = {"bookmarks": [], "cat_order": [], "starred_order": [], "categories": [], "favicon_mode": 0, "item_order": {}}

_DATA_LOCK = threading.RLock()
_SYNC_LOCK = threading.RLock()
_SYNC_RUN_LOCK = threading.Lock()
_SYNC_THREAD_STARTED = False


def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                data = json.load(f)
        except (OSError, ValueError, UnicodeDecodeError):
            #破損時は空データで起動（上書き保存されるまで元ファイルは残る）
            return dict(DEFAULT_DATA)
        if not isinstance(data, dict):
            return dict(DEFAULT_DATA)
        for key, default in DEFAULT_DATA.items():
            data.setdefault(key, default)
        return data
    return dict(DEFAULT_DATA)


def save_data(data):
    tmp = DATA_FILE + ".tmp"
    with _DATA_LOCK:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, DATA_FILE)


import hashlib
import base64
import datetime
import re
import urllib.request
import ssl
from PIL import Image
from io import BytesIO

FAVICON_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "favicon_cache")

ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon")
ICON_ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico"}
ICON_MAX_BYTES = 5 * 1024 * 1024


def sanitize_icon_filename(name):
    import re
    base = os.path.basename(name or "icon")
    stem, ext = os.path.splitext(base)
    ext = ext.lower()
    if ext not in ICON_ALLOWED_EXTS:
        ext = ".png"
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", stem).strip("._") or "icon"
    stem = stem[:80]
    return stem + ext


def unique_icon_filename(filename):
    os.makedirs(ICON_DIR, exist_ok=True)
    stem, ext = os.path.splitext(filename)
    candidate = filename
    n = 1
    while os.path.exists(os.path.join(ICON_DIR, candidate)):
        candidate = f"{stem}_{n}{ext}"
        n += 1
        if n > 1000:
            candidate = f"{stem}_{int(time.time())}{ext}"
            break
    return candidate


def sanitize_svg(data):
    """SVG内のスクリプト・イベントハンドラ・外部参照を簡易除去（完全ではない）"""
    import re
    try:
        text = data.decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return None
    # XML宣言・DOCTYPE（外部実体を持ち込める）を除去
    text = re.sub(r"<\?.*?\?>", "", text, flags=re.DOTALL)
    text = re.sub(r"<!DOCTYPE.*?>", "", text, flags=re.DOTALL)
    text = re.sub(r"<!ENTITY.*?>", "", text, flags=re.DOTALL)
    # script / handler 系要素を除去
    text = re.sub(r"<script.*?</script\s*>", "", text, flags=re.DOTALL | re.IGNORECASE)
    for tag in ("handler", "listener", "foreignObject", "iframe", "embed", "object"):
        text = re.sub(rf"<{tag}\b.*?</{tag}\s*>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(rf"<{tag}\b[^>]*/>", "", text, flags=re.IGNORECASE)
    # on* イベント属性を除去
    text = re.sub(r"""\s+on[a-zA-Z]+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""", "", text)
    # javascript: 疑似プロトコルを除去
    text = re.sub(r"(?i)\b(javascript|data:text/html)\s*:", "removed:", text)
    return text.encode("utf-8")


def save_icon_file(data, filename):
    """保存し、実際に書き込んだファイル名を返す（形式正規化で変わる場合あり）"""
    os.makedirs(ICON_DIR, exist_ok=True)
    stem, ext = os.path.splitext(filename)
    ext = ext.lower()
    if ext == ".svg":
        cleaned = sanitize_svg(data)
        if cleaned is None:
            raise ValueError("invalid svg")
        with open(os.path.join(ICON_DIR, filename), "wb") as f:
            f.write(cleaned)
        return filename
    if ext == ".ico":
        with open(os.path.join(ICON_DIR, filename), "wb") as f:
            f.write(data)
        return filename
    try:
        img = Image.open(BytesIO(data))
        img.thumbnail((128, 128), Image.LANCZOS)
        if ext in (".jpg", ".jpeg"):
            if img.mode in ("RGBA", "LA", "P"):
                bg = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode == "P":
                    img = img.convert("RGBA")
                if img.mode == "RGBA":
                    bg.paste(img, mask=img.split()[3])
                    img = bg
                else:
                    img = img.convert("RGB")
            out_name = stem + ext
            img.save(os.path.join(ICON_DIR, out_name), "JPEG", quality=90)
            return out_name
        # PNG/GIF/WebP等は透過保持のためPNG内容で保存し拡張子も.pngに統一
        out_name = stem + ".png"
        img.save(os.path.join(ICON_DIR, out_name), "PNG")
        return out_name
    except Exception:
        # Pillow で読めなければ生データで保存
        with open(os.path.join(ICON_DIR, filename), "wb") as f:
            f.write(data)
        return filename


def list_icon_files():
    os.makedirs(ICON_DIR, exist_ok=True)
    try:
        names = sorted(
            f for f in os.listdir(ICON_DIR)
            if os.path.isfile(os.path.join(ICON_DIR, f))
            and os.path.splitext(f)[1].lower() in ICON_ALLOWED_EXTS
        )
    except Exception:
        names = []
    return names


def get_favicon_path(url):
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
    return os.path.join(FAVICON_CACHE_DIR, f"{url_hash}.ico")


def resize_and_save_image(data, path):
    os.makedirs(FAVICON_CACHE_DIR, exist_ok=True)
    img = Image.open(BytesIO(data))
    img = img.convert("RGBA")
    img.thumbnail((48, 48), Image.LANCZOS)
    img.save(path, "PNG")
    return True


def fetch_and_cache_favicon(url):
    try:
        origin = urllib.request.urlsplit(url)._replace(path="", query="", fragment="").geturl()
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        for path in ["/favicon.ico", "/favicon.png", "/favicon.svg"]:
            try:
                favicon_url = origin + path
                req = urllib.request.Request(favicon_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=5, context=ctx) as resp:
                    data = resp.read(65536)
                    if data and len(data) > 0:
                        try:
                            resize_and_save_image(data, get_favicon_path(url))
                            return True
                        except Exception:
                            continue
            except Exception:
                continue
    except Exception:
        pass
    return False


HTML = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="icon" href="/favicon.png">
<link rel="apple-touch-icon" href="/selfmark.png">
<title>selfmark</title>
<style>
  :root{--bg:#f6f7f9;--surface:#fff;--border:#e2e5ea;--text:#1c2430;--muted:#6b7686;
        --accent:#2563eb;--accent-soft:#e8effc;--star:#f5a623;--radius:12px}
  *{box-sizing:border-box}
  body{margin:0;font-family:system-ui,-apple-system,"Hiragino Sans","Noto Sans JP",sans-serif;
       background:var(--bg);color:var(--text);line-height:1.5}
  header{display:flex;align-items:center;gap:10px;padding:10px 16px;background:var(--surface);
         border-bottom:1px solid var(--border);position:sticky;top:0;z-index:5;flex-wrap:wrap}
  header h1{font-size:17px;margin:0;font-weight:700;white-space:nowrap}
  header .spacer{flex:1}
  .btn{border:1px solid var(--border);background:var(--surface);border-radius:8px;
       padding:6px 12px;font-size:12px;font-weight:600;cursor:pointer;white-space:nowrap;color:var(--text)}
  .btn:hover{border-color:var(--accent);color:var(--accent)}
  .btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}
  .btn.primary:hover{filter:brightness(.95);color:#fff}
  .btn.danger{color:#d64550}
  .btn.danger:hover{border-color:#d64550;color:#d64550}
  .btn:disabled{opacity:.5;cursor:wait}
  .version-label{font-size:12px;font-weight:600;color:var(--muted);white-space:nowrap}
  .favicon-toggle-wrap{display:flex;align-items:center}
  .cat-select{border:1px solid var(--border);border-radius:8px;padding:6px 10px;font-size:12px;
       background:var(--surface);cursor:pointer;max-width:160px;color:var(--text)}
  main{max-width:1200px;margin:0 auto;padding:14px 16px 40px}
  .quickadd{display:flex;gap:8px;background:var(--surface);border:1px solid var(--border);
       border-radius:var(--radius);padding:10px;margin-bottom:10px;flex-wrap:wrap}
  .quickadd input[type=url]{flex:1;border:1px solid var(--border);border-radius:8px;
       padding:8px 10px;font-size:14px;min-width:180px}
  .preview{display:none;align-items:center;gap:8px;font-size:13px;color:var(--muted);padding:0 4px 10px}
  .preview img{width:20px;height:20px;border-radius:4px;object-fit:contain}
  .preview .dup{color:#d64550;font-weight:600}
  .searchbar{margin-bottom:10px}
  .searchbar input{width:100%;border:1px solid var(--border);border-radius:8px;
       padding:9px 12px;font-size:14px;background:var(--surface)}
  .chips{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px}
  .chip{border:1px solid var(--border);background:var(--surface);border-radius:999px;
       padding:6px 14px;font-size:13px;cursor:pointer;user-select:none;white-space:nowrap}
  .chip.active{background:var(--accent);border-color:var(--accent);color:#fff}
  .chip.dragging{opacity:.4}
  .chip.dragover{outline:2px dashed var(--accent);outline-offset:2px}
  .section{margin-bottom:22px}
  .sec-head{display:flex;align-items:center;gap:8px;cursor:pointer;padding:4px 2px}
  .sec-head h2{font-size:15px;margin:0}
  .sec-head .count{color:var(--muted);font-size:12px}
  .sec-head .arrow{color:var(--muted);font-size:12px;transition:transform .15s}
  .section.collapsed .sec-head .arrow{transform:rotate(-90deg)}
  .section.collapsed .grid{display:none}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(104px,1fr));gap:10px}
  .card.tile{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
       padding:12px 8px 10px;text-align:center;cursor:pointer;position:relative;
       transition:border-color .15s,box-shadow .15s}
  .card.tile:hover{border-color:var(--accent);box-shadow:0 2px 8px rgba(20,40,80,.08)}
  .card.tile.dragging{opacity:.4}
  .card.tile.drag-over{border-top:3px solid var(--accent)}
  .tile .icon{width:36px;height:36px;margin:0 auto;display:flex;align-items:center;justify-content:center}
  .tile .icon img{width:36px;height:36px;border-radius:8px;object-fit:contain;
       background:#fff;border:1px solid #eef0f4;cursor:pointer}
  .tile .letter{width:36px;height:36px;border-radius:8px;background:var(--accent-soft);
       color:var(--accent);font-weight:700;font-size:17px;display:flex;align-items:center;justify-content:center}
  .tile .name{margin-top:7px;font-size:12px;line-height:1.35;height:2.7em;overflow:hidden;word-break:break-word}
  .tile .star,.tile .edit{position:absolute;top:5px;border:none;background:none;cursor:pointer;
       font-size:13px;color:#c6ccd6;padding:0;line-height:1}
  .tile .star{right:7px}
  .tile .edit{left:7px}
  .tile .star:hover,.tile .edit:hover{color:var(--accent)}
  .tile .star.on{color:var(--star)}
  .empty{color:var(--muted);font-size:13px;padding:8px 2px}
  .footer{display:flex;gap:8px;justify-content:center;margin-top:24px;padding-top:16px;border-top:1px solid var(--border)}
  .toast{position:fixed;bottom:24px;right:24px;background:var(--text);color:#fff;
       padding:12px 20px;border-radius:10px;font-size:13px;display:none;z-index:300}
  .add-form{display:none;background:var(--surface);border:1px solid var(--border);
       border-radius:var(--radius);padding:16px;margin-bottom:10px}
  .add-form.show{display:block}
  .add-form h3{font-size:14px;margin-bottom:12px}
  .form-row{display:flex;gap:8px;margin-bottom:8px;flex-wrap:wrap}
  .form-row input{flex:1;min-width:160px;border:1px solid var(--border);border-radius:8px;padding:8px 10px}
  .cat-edit-item{display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--border)}
  .cat-edit-item input{flex:1;border:1px solid var(--border);border-radius:8px;padding:8px 10px}
  .cat-count{font-size:11px;color:var(--muted);min-width:40px}
  .modal-overlay{position:fixed;inset:0;background:rgba(15,15,35,.55);display:none;
       align-items:center;justify-content:center;z-index:200;padding:20px}
  .modal-overlay.show{display:flex}
  /* 編集モーダルから開くアイコン選択は常に手前で表示する */
  #iconPickerModal{z-index:220}
  .modal{background:var(--surface);border-radius:14px;padding:22px;width:640px;max-width:100%;
       max-height:85vh;overflow-y:auto;box-shadow:0 20px 60px rgba(0,0,0,.3)}
  .modal h3{font-size:15px;margin-bottom:6px;font-weight:700}
  .modal .modal-desc{font-size:12px;color:var(--muted);margin-bottom:14px;word-break:break-all}
  .edit-fields{display:grid;gap:12px}
  .edit-fields label{font-size:12px;font-weight:600;color:var(--muted);display:block}
  .edit-fields input[type=text],.edit-fields input[type=url],.edit-fields select{width:100%;
       margin-top:4px;border:1px solid var(--border);border-radius:8px;padding:9px 11px;font-size:14px;color:var(--text)}
  .edit-fields .check{display:flex;align-items:center;gap:8px;font-size:14px;color:var(--text)}
  .icon-row{display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:13px}
  .modal-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:18px}
  .icon-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(96px,1fr));gap:10px;margin:12px 0}
  .icon-cell{border:1px solid var(--border);border-radius:10px;padding:10px 6px;text-align:center;
       cursor:pointer;background:var(--bg)}
  .icon-cell:hover{border-color:var(--accent)}
  .icon-cell.selected{border-color:var(--accent);background:var(--accent-soft)}
  .icon-cell img{width:48px;height:48px;object-fit:contain;border-radius:8px;background:#fff;border:1px solid #eee}
  .icon-cell .icon-name{font-size:10px;color:var(--muted);margin-top:6px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .icon-cell .icon-del{font-size:10px;color:var(--muted);background:none;border:none;cursor:pointer;margin-top:4px}
  .icon-cell .icon-del:hover{color:#d64550}
  .sync-row{display:flex;align-items:center;gap:10px;margin-bottom:12px;flex-wrap:wrap}
  .sync-row label{font-size:13px;font-weight:600;color:var(--muted);min-width:90px}
  .sync-role-toggle{display:flex;border:1px solid var(--border);border-radius:8px;overflow:hidden}
  .sync-role-toggle button{border:none;background:var(--bg);padding:8px 18px;font-size:13px;cursor:pointer;color:var(--muted)}
  .sync-role-toggle button.active{background:var(--accent);color:#fff}
  .sync-status{font-size:12px;color:var(--muted);background:var(--bg);border-radius:8px;padding:10px 12px}
  .sync-time-input{border:1px solid var(--border);border-radius:8px;padding:8px 12px}
  ::-webkit-scrollbar{width:6px}
  ::-webkit-scrollbar-track{background:transparent}
  ::-webkit-scrollbar-thumb{background:#cdd2da;border-radius:3px}
    </style>
</head>
<body>
<header>
  <h1>🔖 selfmark</h1>
  <div class="spacer"></div>
  <button class="btn" onclick="openIconManager()" title="ブックマーク用アイコンをアップロード・管理">アイコン置き場</button>
  <button class="btn" onclick="showCatEditor()" title="カテゴリーの追加・改名・削除">カテゴリー編集</button>
  <label class="favicon-toggle-wrap">
    <select class="cat-select" id="faviconModeSelect" onchange="onFaviconModeChange(this.value)" title="ファビコン取得設定">
      <option value="0">ファビコン取得しない</option>
      <option value="1">キャッシュ利用</option>
      <option value="2">ファビコン取得</option>
    </select>
  </label>
  <button class="btn" id="saveBtn" style="display:none" onclick="saveAll()">💾 保存</button>
  <button class="btn" id="btnExtension" onclick="downloadExtension()" title="Chrome拡張機能をダウンロード">拡張機能</button>
  <button class="btn" id="btnInstallSub" onclick="installSub()" title="閲覧専用ビューをインストール">subインストール</button>
  <button class="btn" id="btnSync" onclick="openSyncSettings()" title="別PCとブックマークを同期">同期</button>
  <button class="btn" id="btnAdminUpdate" onclick="adminUpdate()" title="GitHubから最新版を取得">更新</button>
  <button class="btn" id="btnAdminRestart" onclick="adminRestart()" title="selfmarkを再起動">再起動</button>
  <span class="version-label" id="appVersion"></span>
</header>
<main>
  <form class="quickadd" id="quickadd" onsubmit="quickAdd(event)">
    <input type="url" id="qa-url" placeholder="URLを貼り付けて追加（例：https://example.com）" required>
    <select class="cat-select" id="qa-cat"></select>
    <button class="btn" type="button" onclick="openQuickIconPicker()" title="アイコンを指定">🖼️ アイコン</button>
    <button class="btn primary" type="submit">追加</button>
  </form>
  <div class="preview" id="qa-preview">
    <img id="qa-icon" alt="">
    <span id="qa-title"></span>
  </div>

  <div class="searchbar"><input id="search" type="search" placeholder="検索（名前・URL）"></div>
  <div class="chips" id="chips"></div>
  <div id="bookmarkList"></div>

  <div class="add-form" id="addForm">
    <h3>ブックマークを追加（詳細）</h3>
    <div class="form-row">
      <input type="text" id="addUrl" placeholder="URL (例: https://example.com)">
      <input type="text" id="addName" placeholder="名前 (例: Nextcloud)">
    </div>
    <div class="form-row">
      <select class="cat-select" id="addCategorySelect" onchange="document.getElementById('addCategory').value=''">
        <option value="">-- カテゴリー選択 --</option>
      </select>
      <input type="text" id="addCategory" placeholder="または新規入力">
    </div>
    <button class="btn primary" onclick="addBookmark()">追加</button>
    <button class="btn" onclick="toggleAddForm()">キャンセル</button>
  </div>

  <div class="add-form" id="catEditor">
    <h3>カテゴリー編集</h3>
    <div id="catEditorList"></div>
    <div class="form-row" style="margin-top:12px">
      <input type="text" id="newCatName" placeholder="新しいカテゴリー名">
      <button class="btn primary" onclick="addCategory()">追加</button>
    </div>
    <button class="btn" onclick="closeCatEditor()" style="margin-top:8px">閉じる</button>
  </div>

  <div class="footer">
    <button class="btn" onclick="exportData()">📥 エクスポート</button>
    <button class="btn" onclick="document.getElementById('importFile').click()">📤 インポート</button>
    <input type="file" id="importFile" accept=".json" style="display:none" onchange="importData(event)">
  </div>
</main>
<div class="toast" id="toast"></div>
<input type="file" id="faviconFileInput" accept="image/*" style="display:none" onchange="handleFaviconUpload(event)">
<input type="file" id="iconStoreFileInput" accept="image/*,.ico,.svg" multiple style="display:none" onchange="handleIconStoreUpload(event)">
<input type="file" id="iconPickerFileInput" accept="image/*,.ico,.svg" style="display:none" onchange="handleIconPickerUpload(event)">

<div class="modal-overlay" id="iconStoreModal">
  <div class="modal">
    <h3>🖼️ アイコン置き場</h3>
    <div class="modal-desc">ブックマーク用アイコンをアップロードして保存します（保存先: <code>icon/</code>）。一覧の左側サムネイルから割り当てできます。</div>
    <div style="display:flex;gap:8px;margin-bottom:8px;flex-wrap:wrap;">
      <button class="btn btn-success" onclick="document.getElementById('iconStoreFileInput').click()">＋ アイコンをアップロード</button>
      <button class="btn btn-secondary" onclick="closeIconManager()">閉じる</button>
    </div>
    <div class="icon-grid" id="iconStoreGrid"></div>
    <div id="iconStoreEmpty" style="color:#aaa;font-size:13px;padding:12px 0;">まだアイコンがありません。「＋ アイコンをアップロード」から追加してください。</div>
  </div>
</div>

<div class="modal-overlay" id="iconPickerModal">
  <div class="modal">
    <h3>アイコンを選択</h3>
    <div class="modal-desc" id="iconPickerTarget"></div>
    <div class="icon-grid" id="iconPickerGrid"></div>
    <div id="iconPickerEmpty" style="color:#aaa;font-size:13px;padding:12px 0;display:none;">アイコン置き場に画像がありません。先に「アイコン置き場」からアップロードしてください。</div>
    <div style="display:flex;gap:8px;margin-top:12px;flex-wrap:wrap;">
      <button class="btn btn-success" onclick="document.getElementById('iconPickerFileInput').click()">＋ 新規アップロードして設定</button>
      <button class="btn btn-secondary" onclick="uploadFaviconForPickerTarget()" title="従来のファビコン直接アップロード（URL単位）">📎 ファビコンを直接アップロード</button>
      <button class="btn btn-secondary" onclick="clearBookmarkIcon()">アイコンを外す（ファビコン表示に戻す）</button>
      <button class="btn btn-secondary" onclick="closeIconPicker()">キャンセル</button>
    </div>
    <div class="modal-desc" style="margin-top:8px;margin-bottom:0;">設定後は「💾 保存」を押してください（Escでも閉じられます）。</div>
  </div>
</div>

<div class="modal-overlay" id="syncModal">
  <div class="modal">
    <h3>🔄 selfmark 同期</h3>
    <div class="modal-desc">別PCの selfmark と1日1回・片方向で同期します（予備機用途・同期先は1箇所のみ）。相手PCも最新版の selfmark に更新してください。</div>
    <div class="sync-row">
      <label>役割</label>
      <div class="sync-role-toggle">
        <button id="syncRoleSource" onclick="setSyncRole('source')">同期元（送る側）</button>
        <button id="syncRoleDest" onclick="setSyncRole('destination')">同期先（受ける側）</button>
      </div>
    </div>
    <div class="modal-desc" id="syncRoleDesc" style="margin-top:-6px;"></div>
    <div class="sync-row">
      <label>同期相手</label>
      <select class="cat-select" id="syncPeer" style="max-width:100%;flex:1;min-width:220px;font-size:13px;padding:8px 10px;">
        <option value="">-- 選択してください --</option>
      </select>
    </div>
    <div class="sync-row">
      <label>同期時刻</label>
      <input type="time" class="sync-time-input" id="syncTime" value="03:00">
      <span style="font-size:12px;color:#888;">1日1回この時刻以降に自動同期</span>
    </div>
    <div class="sync-status" id="syncStatus">読み込み中…</div>
    <div style="display:flex;gap:8px;margin-top:12px;flex-wrap:wrap;">
      <button class="btn btn-primary" onclick="saveSyncSettings()">保存</button>
      <button class="btn btn-success" id="btnSyncNow" onclick="runSyncNow()">⚡ 今すぐ同期</button>
      <button class="btn btn-secondary" onclick="closeSyncSettings()">閉じる</button>
      <button class="btn btn-secondary" id="btnSyncStop" onclick="stopSync()">同期を停止</button>
    </div>
  </div>
</div>


<div class="modal-overlay" id="editModal">
  <div class="modal">
    <h3>ブックマークを編集</h3>
    <div class="modal-desc" id="editTarget"></div>
    <div class="edit-fields">
      <label>名前<input type="text" id="editName"></label>
      <label>URL<input type="url" id="editUrl"></label>
      <label>カテゴリー<select id="editCat"></select></label>
      <label class="check"><input type="checkbox" id="editStar"> ★ お気に入り</label>
      <div class="icon-row">
        <span>アイコン:</span>
        <span id="editIconName"></span>
        <button class="btn" type="button" onclick="editPickIcon()">置き場から選択</button>
        <button class="btn" type="button" onclick="editClearIcon()">自動取得に戻す</button>
      </div>
      <div class="icon-row">
        <span>並び順:</span>
        <button class="btn" type="button" onclick="moveEdit(-1)">▲ 上へ</button>
        <button class="btn" type="button" onclick="moveEdit(1)">▼ 下へ</button>
      </div>
    </div>
    <div class="modal-actions">
      <button class="btn primary" type="button" onclick="saveEdit()">保存</button>
      <button class="btn danger" type="button" onclick="deleteEdit()">削除</button>
      <button class="btn" type="button" onclick="closeEdit()">キャンセル</button>
    </div>
  </div>
</div>

<script>
let bookmarks = [];
let editedCats = {};
let dragSrc = null;
let allCategories = [];
let catOrder = [];
let starredOrder = [];
let itemOrder = {};
let catCollapsed = {};
let dragCatSrc = null;
let dirty = false;
let faviconMode = 0;
let customIcons = [];
let iconPickerTargetUrl = null;
let iconPickerMode = 'bookmark';
let quickAddIcon = '';
let editingIndex = -1;
let query = '';
let chipOrder = [];
let activeChip = '__all__';
let collapsed = new Set();

const CHIP_ORDER_KEY = 'selfmark_chip_order';
const COLLAPSED_KEY = 'selfmark_collapsed_sections';

function escapeHtml(s) {
  const d = document.createElement('div');
  d.appendChild(document.createTextNode(s));
  return d.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function jsSq(s) {
  // HTML属性内のシングルクォートJS文字列に埋め込む用。JSエスケープ後にHTMLエスケープする。
  return String(s ?? '').replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/\n/g, '\\n').replace(/\r/g, '\\r')
    .replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function safeHttpUrl(u) {
  // javascript: 等の疑似スキームを排除し、http(s)のみ許可
  try {
    const p = new URL(u, location.origin);
    if (p.protocol === 'http:' || p.protocol === 'https:') return escapeHtml(u);
  } catch(e) {}
  return '#';
}

function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.style.display = 'block';
  setTimeout(() => t.style.display = 'none', 2000);
}

function markDirty() {
  dirty = true;
  const b = document.getElementById('saveBtn');
  if (b) b.style.display = 'inline-block';
}

function toggleAddForm() {
  const form = document.getElementById('addForm');
  form.classList.toggle('show');
  if (form.classList.contains('show')) {
    const sel = document.getElementById('addCategorySelect');
    const cur = document.getElementById('addCategory').value.trim();
    sel.innerHTML = '<option value="">-- カテゴリー選択 --</option>' +
      getSortedCategories().map(c => `<option value="${escapeHtml(c)}" ${c === cur ? 'selected' : ''}>${escapeHtml(c)}</option>`).join('');
  }
}

function onFaviconModeChange(mode) {
  faviconMode = parseInt(mode);
  persistFaviconSetting();
  render();
}

function persistFaviconSetting() {
  fetch('/api/favicon-setting', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({favicon_mode: faviconMode}),
  }).then(() => {
    if (faviconMode === 2) {
      fetchAllFavicons();
    }
  }).catch(() => {});
}

function fetchAllFavicons() {
  const urls = bookmarks.map(s => s.url);
  fetch('/api/favicon-fetch-all', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({urls}),
  }).catch(() => {});
}

let pendingFaviconUrl = '';
function triggerFaviconUpload(url) {
  pendingFaviconUrl = url;
  document.getElementById('faviconFileInput').click();
}

function handleFaviconUpload(e) {
  const file = e.target.files[0];
  if (!file || !pendingFaviconUrl) return;
  const formData = new FormData();
  formData.append('url', pendingFaviconUrl);
  formData.append('file', file);
  fetch('/api/favicon-upload', { method: 'POST', body: formData })
    .then(r => r.json())
    .then(data => {
      if (data.ok) {
        showToast('ファビコンをアップロードしました');
        render();
      } else {
        showToast('アップロードに失敗しました');
      }
    })
    .catch(() => showToast('アップロードに失敗しました'));
  pendingFaviconUrl = '';
  e.target.value = '';
}

function refresh() {
  fetch('/api/bookmarks').then(r => r.json()).then(data => {
    bookmarks = data.bookmarks || [];
    editedCats = {};
    catOrder = data.cat_order || [];
    starredOrder = data.starred_order || [];
    itemOrder = data.item_order || {};
    allCategories = data.categories || [];
    faviconMode = data.favicon_mode || 0;
    document.getElementById('faviconModeSelect').value = faviconMode;
    collectCategories();
    collapsed = loadCollapsed();
    ensureChipOrder();
    activeChip = '__all__';
    const b = document.getElementById('saveBtn');
    if (b) b.style.display = 'none';
    dirty = false;
    render();
  });
  refreshIconList();
}

function refreshIconList() {
  fetch('/api/icons').then(r => r.json()).then(data => {
    customIcons = (data.icons || []).map(x => x.name);
    renderIconStore();
    if (document.getElementById('iconPickerModal').classList.contains('show')) renderIconPicker();
  }).catch(() => {});
}

function openIconManager() {
  document.getElementById('iconStoreModal').classList.add('show');
  refreshIconList();
}

function closeIconManager() {
  document.getElementById('iconStoreModal').classList.remove('show');
}

function renderIconStore() {
  const grid = document.getElementById('iconStoreGrid');
  const empty = document.getElementById('iconStoreEmpty');
  if (!grid) return;
  if (customIcons.length === 0) {
    grid.innerHTML = '';
    if (empty) empty.style.display = 'block';
    return;
  }
  if (empty) empty.style.display = 'none';
  grid.innerHTML = customIcons.map(name => `
    <div class="icon-cell">
      <img loading="lazy" src="/icon/${encodeURIComponent(name)}" alt="${escapeHtml(name)}" title="${escapeHtml(name)}">
      <div class="icon-name">${escapeHtml(name)}</div>
      <button class="icon-del" onclick="deleteStoreIcon('${jsSq(name)}')">削除</button>
    </div>`).join('');
}

function handleIconStoreUpload(e) {
  const files = e.target.files;
  if (!files || files.length === 0) return;
  const uploads = [...files].map(f => {
    const fd = new FormData();
    fd.append('file', f);
    return fetch('/api/icon-upload', { method: 'POST', body: fd }).then(r => r.json());
  });
  Promise.all(uploads).then(results => {
    const okCount = results.filter(r => r && r.ok).length;
    showToast(okCount > 0 ? `${okCount} 件アップロードしました` : 'アップロードに失敗しました');
    refreshIconList();
    render();
  }).catch(() => showToast('アップロードに失敗しました'));
  e.target.value = '';
}

function deleteStoreIcon(name) {
  if (!confirm(`「${name}」を削除しますか？\nこのアイコンを使っているブックマークはファビコン表示に戻ります。`)) return;
  fetch('/api/icon-delete', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name}),
  }).then(r => r.json()).then(data => {
    if (data.ok) {
      let changed = false;
      bookmarks.forEach(s => { if (s.icon === name) { delete s.icon; changed = true; } });
      if (changed) { markDirty(); saveAll(); }
      showToast('削除しました');
      refreshIconList();
      renderIconPicker();
      render();
    } else {
      showToast('削除に失敗しました');
    }
  }).catch(() => showToast('削除に失敗しました'));
}

function resolvePickerIndex() {
  // ピッカーを開いた後に並び替え・削除されても別ブックマークに当たらないようURLで特定
  if (iconPickerTargetUrl === null) return -1;
  return bookmarks.findIndex(s => s.url === iconPickerTargetUrl);
}

function openIconPicker(i) {
  const s = bookmarks[i];
  if (!s) return;
  iconPickerMode = 'bookmark';
  iconPickerTargetUrl = s.url;
  const label = `${s.name}（${s.url}）`;
  document.getElementById('iconPickerTarget').textContent = `対象: ${label}`;
  document.getElementById('iconPickerModal').classList.add('show');
  renderIconPicker();
}

function closeIconPicker() {
  document.getElementById('iconPickerModal').classList.remove('show');
  iconPickerTargetUrl = null;
  iconPickerMode = 'bookmark';
}

function openQuickIconPicker() {
  iconPickerMode = 'quickadd';
  iconPickerTargetUrl = null;
  document.getElementById('iconPickerTarget').textContent = '追加するブックマークのアイコンを選択';
  document.getElementById('iconPickerModal').classList.add('show');
  renderIconPicker();
}

function renderIconPicker() {
  const grid = document.getElementById('iconPickerGrid');
  const empty = document.getElementById('iconPickerEmpty');
  if (!grid || !empty) return;
  const idx = resolvePickerIndex();
  const cur = iconPickerMode === 'quickadd' ? quickAddIcon : (idx >= 0 ? (bookmarks[idx].icon || '') : '');
  if (customIcons.length === 0) {
    grid.innerHTML = '';
    empty.style.display = 'block';
    return;
  }
  empty.style.display = 'none';
  grid.innerHTML = customIcons.map(name => `
    <div class="icon-cell ${name === cur ? 'selected' : ''}" onclick="selectBookmarkIcon('${jsSq(name)}')">
      <img loading="lazy" draggable="false" src="/icon/${encodeURIComponent(name)}" alt="${escapeHtml(name)}">
      <div class="icon-name">${escapeHtml(name)}</div>
    </div>`).join('');
}

function selectBookmarkIcon(name) {
  if (iconPickerMode === 'quickadd') {
    quickAddIcon = name;
    closeIconPicker();
    updateQuickPreview();
    showToast('アイコンを設定しました');
    return;
  }
  const idx = resolvePickerIndex();
  if (idx < 0) { closeIconPicker(); return; }
  bookmarks[idx].icon = name;
  markDirty();
  closeIconPicker();
  render();
  if (editingIndex >= 0) renderEditIcon();
  showToast('アイコンを設定しました。「💾 保存」を押してください');
}

function clearBookmarkIcon() {
  if (iconPickerMode === 'quickadd') {
    quickAddIcon = '';
    closeIconPicker();
    updateQuickPreview();
    showToast('アイコンを外しました');
    return;
  }
  const idx = resolvePickerIndex();
  if (idx < 0) { closeIconPicker(); return; }
  delete bookmarks[idx].icon;
  markDirty();
  closeIconPicker();
  render();
  if (editingIndex >= 0) renderEditIcon();
  showToast('アイコンを外しました。「💾 保存」を押してください');
}

function uploadFaviconForPickerTarget() {
  if (iconPickerMode === 'quickadd') {
    document.getElementById('iconPickerFileInput').click();
    return;
  }
  // 従来のファビコン直接アップロード導線（URL単位のキャッシュに保存）
  const idx = resolvePickerIndex();
  if (idx < 0) { closeIconPicker(); return; }
  triggerFaviconUpload(bookmarks[idx].url);
}

function handleIconPickerUpload(e) {
  const file = e.target.files[0];
  e.target.value = '';
  if (!file) return;
  const fd = new FormData();
  fd.append('file', file);
  fetch('/api/icon-upload', { method: 'POST', body: fd })
    .then(r => r.json())
    .then(data => {
      if (data.ok) {
        refreshIconList();
        if (iconPickerMode === 'quickadd') {
          quickAddIcon = data.name;
          closeIconPicker();
          updateQuickPreview();
          showToast('アップロードして設定しました');
          return;
        }
        // そのまま対象ブックマークに設定
        const idx = resolvePickerIndex();
        if (idx >= 0) {
          bookmarks[idx].icon = data.name;
          markDirty();
          closeIconPicker();
          render();
          showToast('アップロードして設定しました。「💾 保存」を押してください');
        } else {
          closeIconPicker();
          render();
          showToast('アップロードしました');
        }
      } else {
        showToast('アップロードに失敗しました');
      }
    })
    .catch(() => showToast('アップロードに失敗しました'));
}

function collectCategories() {
  const cats = new Set(allCategories);
  bookmarks.forEach((s, i) => {
    const cat = editedCats[i] !== undefined ? editedCats[i] : (s.category || '');
    if (cat) cats.add(cat);
  });
  allCategories = [...cats];
}

function getCat(i) {
  return editedCats[i] !== undefined ? editedCats[i] : (bookmarks[i].category || '');
}

function getSortedCategories() {
  const all = [...new Set([...allCategories])];
  if (catOrder.length > 0) {
    return catOrder.filter(c => all.includes(c)).concat(all.filter(c => !catOrder.includes(c)));
  }
  return all.sort();
}

function getItemOrderKey(cat) {
  return cat || '__none__';
}

function applyItemOrderToGroup(items, cat) {
  const key = getItemOrderKey(cat);
  const order = itemOrder[key];
  if (!order || order.length === 0) return items;
  const found = order.map(url => items.find(({s}) => s.url === url)).filter(Boolean);
  const rest = items.filter(({s}) => !order.includes(s.url));
  return found.concat(rest);
}

function advanceFavicon(img) {
  const sources = JSON.parse(decodeURIComponent(img.dataset.fallbackSources));
  if (sources.length) {
    const next = sources.shift();
    img.dataset.fallbackSources = encodeURIComponent(JSON.stringify(sources));
    img.src = next;
  } else {
    img.onerror = null;
    img.style.display = 'none';
    img.nextElementSibling.style.display = 'flex';
  }
}


// ---- チップ並び順・折りたたみ状態（UI状態は localStorage、カテゴリー順は cat_order と共有） ----
function defaultChipOrder() {
  return ['__all__', '__starred__', ...getSortedCategories(), '__none__'];
}

function ensureChipOrder() {
  const cats = getSortedCategories();
  const valid = ['__all__', '__starred__', '__none__', ...cats];
  let stored = null;
  try { stored = JSON.parse(localStorage.getItem(CHIP_ORDER_KEY) || 'null'); } catch(e) {}
  let order = Array.isArray(stored) ? stored.filter(k => valid.includes(k)) : [];
  if (order.length === 0) order = defaultChipOrder();
  const noneIdx = order.indexOf('__none__');
  cats.forEach(c => { if (!order.includes(c)) order.splice(noneIdx < 0 ? order.length : noneIdx, 0, c); });
  ['__all__', '__starred__', '__none__'].forEach(k => { if (!order.includes(k)) order.push(k); });
  chipOrder = order;
}

function saveChipOrder() {
  try { localStorage.setItem(CHIP_ORDER_KEY, JSON.stringify(chipOrder)); } catch(e) {}
  const cats = chipOrder.filter(k => k !== '__all__' && k !== '__starred__' && k !== '__none__');
  if (JSON.stringify(cats) !== JSON.stringify(catOrder)) {
    catOrder = cats;
    markDirty();
    saveAll();
  }
}

function loadCollapsed() {
  try { return new Set(JSON.parse(localStorage.getItem(COLLAPSED_KEY) || '[]')); } catch(e) { return new Set(); }
}

function saveCollapsed() {
  try { localStorage.setItem(COLLAPSED_KEY, JSON.stringify([...collapsed])); } catch(e) {}
}

function toggleSection(key) {
  if (collapsed.has(key)) collapsed.delete(key); else collapsed.add(key);
  saveCollapsed();
  render();
}

function chipLabel(key) {
  if (key === '__all__') return 'すべて';
  if (key === '__starred__') return '★ お気に入り';
  if (key === '__none__') return '未分類';
  return key;
}

function renderChips() {
  const wrap = document.getElementById('chips');
  if (!wrap) return;
  wrap.innerHTML = '';
  chipOrder.forEach(key => {
    if (key === '__none__' && !bookmarks.some((s, i) => !getCat(i))) return;
    const el = document.createElement('button');
    el.type = 'button';
    el.className = 'chip' + (activeChip === key ? ' active' : '');
    el.textContent = chipLabel(key);   // 件数はチップに出さない（見出し側にのみ表示）
    el.draggable = true;
    el.dataset.key = key;
    el.addEventListener('click', () => { activeChip = key; render(); });
    el.addEventListener('dragstart', e => {
      el.classList.add('dragging');
      e.dataTransfer.setData('text/plain', key);
    });
    el.addEventListener('dragend', () => el.classList.remove('dragging'));
    el.addEventListener('dragover', e => { e.preventDefault(); el.classList.add('dragover'); });
    el.addEventListener('dragleave', () => el.classList.remove('dragover'));
    el.addEventListener('drop', e => {
      e.preventDefault();
      el.classList.remove('dragover');
      const from = e.dataTransfer.getData('text/plain');
      const fi = chipOrder.indexOf(from), ti = chipOrder.indexOf(key);
      if (fi < 0 || ti < 0 || fi === ti) return;
      chipOrder.splice(fi, 1);
      chipOrder.splice(ti, 0, from);
      saveChipOrder();                  // ドロップ確定時に保存（セクション順も連動）
      render();
    });
    wrap.appendChild(el);
  });
}

function sectionItems(key) {
  let items = bookmarks.map((s, i) => ({s, i}));
  if (key === '__starred__') items = items.filter(({s}) => s.starred);
  else if (key === '__none__') items = items.filter(({i}) => !getCat(i));
  else items = items.filter(({i}) => getCat(i) === key);

  if (key === '__starred__') {
    // 旧データで starredOrder に無いお気に入りを補完（ドラッグ時の位置特定に必要）
    items.forEach(({s}) => { if (!starredOrder.includes(s.url)) starredOrder.push(s.url); });
    const found = starredOrder.map(url => items.find(x => x.s.url === url)).filter(Boolean);
    items = found.concat(items.filter(x => !starredOrder.includes(x.s.url)));
  } else {
    items = applyItemOrderToGroup(items, key === '__none__' ? '' : key);
  }
  if (query) {
    const q = query.toLowerCase();
    items = items.filter(({s}) => s.name.toLowerCase().includes(q) || s.url.toLowerCase().includes(q));
  }
  return items;
}

function tileIconHtml(s) {
  const letter = ((s.name || '?').trim().charAt(0) || '?');
  const letterHtml = `<div class="letter">${escapeHtml(letter)}</div>`;
  if (s.icon) {
    const src = '/icon/' + encodeURIComponent(s.icon);
    return `<img class="tile-icon" loading="lazy" draggable="false" src="${src}" alt=""
      title="${escapeHtml(s.icon)}"
      onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">`
      + letterHtml.replace('class="letter"', 'class="letter" style="display:none"');
  }
  if (faviconMode >= 1) {
    const cachedSrc = '/api/favicon?url=' + encodeURIComponent(s.url);
    const fallbackSources = [];
    if (faviconMode === 2) {
      try {
        const u = new URL(s.url);
        if (['http:', 'https:'].includes(u.protocol)) {
          ['ico', 'png', 'svg'].forEach(ext => fallbackSources.push(u.origin + '/favicon.' + ext));
        }
      } catch(e) {}
    }
    return `<img class="tile-icon" loading="lazy" draggable="false" src="${cachedSrc}"
      data-fallback-sources="${encodeURIComponent(JSON.stringify(fallbackSources))}" alt=""
      onerror="advanceFavicon(this)">`
      + letterHtml.replace('class="letter"', 'class="letter" style="display:none"');
  }
  return letterHtml;  // アイコンが無い場合は文字タイル
}

function renderCard(s, i, compact) {
  const isStarred = !!s.starred;
  const dragAttrs = compact
    ? `ondragstart="onStarDragStart(event)" ondragover="onStarDragOver(event)" ondragleave="onStarDragLeave(event)" ondrop="onStarDrop(event)" ondragend="onStarDragEnd(event)"`
    : `ondragstart="onDragStart(event)" ondragover="onDragOver(event)" ondragleave="onDragLeave(event)" ondrop="onDrop(event)" ondragend="onDragEnd(event)"`;
  const starAction = isStarred ? `unstarBookmark(${i})` : `starBookmark(${i})`;
  return `<div class="card tile" draggable="true" data-index="${i}" data-star-index="${i}"
    data-star-order="${starredOrder.indexOf(s.url)}" ${dragAttrs}
    ondblclick="openEdit(${i})" title="${escapeHtml(s.name)}">
    <button class="edit" onclick="event.stopPropagation();openEdit(${i})" title="編集">✎</button>
    <button class="star${isStarred ? ' on' : ''}" onclick="event.stopPropagation();${starAction}" title="お気に入り">★</button>
    <div class="icon">${tileIconHtml(s)}</div>
    <div class="name">${escapeHtml(s.name)}</div>
  </div>`;
}

function render() {
  const containerEl = document.getElementById('bookmarkList');
  if (!containerEl) return;
  ensureChipOrder();
  if (!chipOrder.includes(activeChip)) activeChip = '__all__';
  renderChips();
  fillQaCats();

  const keys = activeChip === '__all__' ? chipOrder.filter(k => k !== '__all__') : [activeChip];
  let html = '';
  keys.forEach(key => {
    const items = sectionItems(key);
    if (activeChip === '__all__' && items.length === 0 && !query) return;
    const collapsedCls = collapsed.has(key) ? ' collapsed' : '';
    const bodyHtml = items.length === 0
      ? '<div class="empty">該当するブックマークがありません</div>'
      : `<div class="grid">${items.map(({s, i}) => renderCard(s, i, key === '__starred__')).join('')}</div>`;
    html += `<section class="section${collapsedCls}" data-key="${jsSq(key)}">
      <div class="sec-head" onclick="toggleSection('${jsSq(key)}')">
        <span class="arrow">▼</span><h2>${chipLabel(key)}</h2><span class="count">${items.length}件</span>
      </div>${bodyHtml}</section>`;
  });
  if (html === '') {
    html = '<div class="empty">ブックマークがありません。上のURL欄から追加してください。</div>';
  }
  containerEl.innerHTML = html;
}

// ---- クイック追加（URL のみ入力。タイトル／アイコンは自動取得・選択） ----
function updateQuickPreview() {
  const pv = document.getElementById('qa-preview');
  if (!pv) return;
  const v = document.getElementById('qa-url').value.trim();
  if (!v) { pv.style.display = 'none'; return; }
  try {
    const u = new URL(v);
    const iconSrc = quickAddIcon
      ? '/icon/' + encodeURIComponent(quickAddIcon)
      : '/api/favicon?url=' + encodeURIComponent(v);
    document.getElementById('qa-icon').src = iconSrc;
    const dup = bookmarks.some(s => s.url === v);
    const title = document.getElementById('qa-title');
    title.textContent = u.hostname + ' を追加' + (dup ? '（既に追加済みです）' : '');
    title.className = dup ? 'dup' : '';
    pv.style.display = 'flex';
  } catch(e) {
    pv.style.display = 'none';
  }
}

function fillQaCats() {
  const sel = document.getElementById('qa-cat');
  if (!sel) return;
  const cur = sel.value;
  sel.innerHTML = '<option value="">カテゴリーなし</option>' +
    getSortedCategories().map(c => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join('');
  if (cur && [...sel.options].some(o => o.value === cur)) sel.value = cur;
}

function quickAdd(e) {
  e.preventDefault();
  const url = document.getElementById('qa-url').value.trim();
  if (!url) return;
  if (bookmarks.some(s => s.url === url)) { showToast('このURLは既に追加済みです'); return; }
  let name;
  try { name = new URL(url).hostname; } catch(err) { name = url; }
  const category = document.getElementById('qa-cat').value;
  const entry = {name, url, category, starred: false};
  if (quickAddIcon) entry.icon = quickAddIcon;
  bookmarks.push(entry);
  if (category && !allCategories.includes(category)) allCategories.push(category);
  const key = getItemOrderKey(category);
  if (!itemOrder[key]) itemOrder[key] = [];
  itemOrder[key].push(url);
  if (category && !chipOrder.includes(category)) {
    const noneIdx = chipOrder.indexOf('__none__');
    chipOrder.splice(noneIdx < 0 ? chipOrder.length : noneIdx, 0, category);
    saveChipOrder();
  }
  document.getElementById('qa-url').value = '';
  quickAddIcon = '';
  document.getElementById('qa-preview').style.display = 'none';
  render();
  saveAll();
}

// ---- 編集モーダル ----
function openEdit(i) {
  const s = bookmarks[i];
  if (!s) return;
  editingIndex = i;
  document.getElementById('editTarget').textContent = s.url;
  document.getElementById('editName').value = s.name;
  document.getElementById('editUrl').value = s.url;
  const cat = getCat(i);
  const sel = document.getElementById('editCat');
  sel.innerHTML = '<option value="">未分類</option>' +
    getSortedCategories().map(c => `<option value="${escapeHtml(c)}"${c === cat ? ' selected' : ''}>${escapeHtml(c)}</option>`).join('');
  document.getElementById('editStar').checked = !!s.starred;
  renderEditIcon();
  document.getElementById('editModal').classList.add('show');
}

function renderEditIcon() {
  const s = bookmarks[editingIndex];
  if (!s) return;
  document.getElementById('editIconName').textContent = s.icon || '自動取得ファビコン';
}

function closeEdit() {
  document.getElementById('editModal').classList.remove('show');
  editingIndex = -1;
}

function editPickIcon() {
  if (editingIndex >= 0) openIconPicker(editingIndex);
}

function editClearIcon() {
  if (editingIndex < 0) return;
  clearBookmarkIcon();
  renderEditIcon();
}

function moveEdit(direction) {
  if (editingIndex < 0) return;
  moveInCategory(editingIndex, direction);
  render();
}

function saveEdit() {
  if (editingIndex < 0) return;
  const s = bookmarks[editingIndex];
  const name = document.getElementById('editName').value.trim();
  const url = document.getElementById('editUrl').value.trim();
  if (!name || !url) { showToast('名前とURLを入力してください'); return; }
  if (url !== s.url && bookmarks.some((x, idx) => idx !== editingIndex && x.url === url)) {
    showToast('このURLは既に登録されています'); return;
  }
  const cat = document.getElementById('editCat').value;
  if (url !== s.url) updateUrl(editingIndex, url);
  s.name = name;
  if (cat !== getCat(editingIndex)) updateCategory(editingIndex, cat);
  const wantStar = document.getElementById('editStar').checked;
  if (wantStar && !s.starred) starBookmark(editingIndex);
  if (!wantStar && s.starred) unstarBookmark(editingIndex);
  markDirty();
  closeEdit();
  render();
  saveAll();
}

function deleteEdit() {
  if (editingIndex < 0) return;
  const i = editingIndex;
  closeEdit();
  removeBookmark(i);
}

function showCatEditor() {
  document.getElementById('catEditor').classList.add('show');
  renderCatEditorList();
}

function closeCatEditor() {
  document.getElementById('catEditor').classList.remove('show');
}

function renderCatEditorList() {
  const counts = {};
  bookmarks.forEach(s => {
    const c = s.category || '';
    if (c) counts[c] = (counts[c] || 0) + 1;
  });
  const cats = [...new Set([...allCategories, ...Object.keys(counts)])].sort();
  const el = document.getElementById('catEditorList');
  if (cats.length === 0) {
    el.innerHTML = '<div style="color:#999;font-size:13px;padding:8px 0;">\u30ab\u30c6\u30b4\u30ea\u30fc\u304c\u3042\u308a\u307e\u305b\u3093</div>';
    return;
  }
  el.innerHTML = cats.map(c => {
    const n = counts[c] || 0;
    return `<div class="cat-edit-item">
      <input type="text" value="${escapeHtml(c)}" data-old="${escapeHtml(c)}" onchange="renameCategory(this)">
      <span class="cat-count">${n}\u4ef6</span>
      <button class="btn btn-danger" onclick="deleteCategory('${jsSq(c)}')" title="\u524a\u9664"><span class="trash-icon">\u{1F5D1}\uFE0F</span></button>
    </div>`;
  }).join('');
}

function addCategory() {
  const input = document.getElementById('newCatName');
  const name = input.value.trim();
  if (!name) return;
  if (allCategories.includes(name)) { showToast('\u540c\u3058\u540d\u524d\u306e\u30ab\u30c6\u30b4\u30ea\u30fc\u304c\u65e2\u306b\u3042\u308a\u307e\u3059'); return; }
  allCategories.push(name);
  input.value = '';
  renderCatEditorList();
  markDirty();
  render();
  showToast('\u30ab\u30c6\u30b4\u30ea\u30fc\u3092\u8ffd\u52a0\u3057\u307e\u3057\u305f');
}

function renameCategory(inputEl) {
  const oldName = inputEl.dataset.old;
  const newName = inputEl.value.trim();
  if (!newName || newName === oldName) { renderCatEditorList(); return; }
  if (allCategories.includes(newName)) { showToast('\u540c\u3058\u540d\u524d\u306e\u30ab\u30c6\u30b4\u30ea\u30fc\u304c\u65e2\u306b\u3042\u308a\u307e\u3059'); inputEl.value = oldName; return; }
  const idx = allCategories.indexOf(oldName);
  if (idx >= 0) allCategories[idx] = newName;
  bookmarks.forEach(s => { if (s.category === oldName) s.category = newName; });
  Object.keys(catCollapsed).forEach(k => {
    if (k === oldName) { catCollapsed[newName] = catCollapsed[k]; delete catCollapsed[k]; }
  });
  if (collapsed.has(oldName)) { collapsed.delete(oldName); collapsed.add(newName); saveCollapsed(); }
  catOrder = catOrder.map(c => c === oldName ? newName : c);
  if (itemOrder[oldName]) {
    itemOrder[newName] = itemOrder[oldName];
    delete itemOrder[oldName];
  }
  collectCategories();
  markDirty();
  renderCatEditorList();
  render();
  showToast('\u30ab\u30c6\u30b4\u30ea\u30fc\u540d\u3092\u5909\u66F4\u3057\u307e\u3057\u305F');
}

function deleteCategory(catName) {
  const n = bookmarks.filter(s => (s.category || '') === catName).length;
  if (n > 0 && !confirm(`\u300C${catName}\u300D\u306B\u5c5e\u3059\u308b\u30d6\u30c3\u30af\u30de\u30fc\u30af\u304C${n}\u4ef6\u3042\u308A\u307e\u3059\u3002\u672a\u5206\u985E\u306B\u5909\u66F4\u3057\u307e\u3059\u304B\uFF1F`)) return;
  if (n === 0 && !confirm(`\u300C${catName}\u300D\u3092\u524a\u9664\u3057\u307E\u3059\u304B\uFF1F`)) return;
  bookmarks.forEach(s => { if (s.category === catName) s.category = ''; });
  allCategories = allCategories.filter(c => c !== catName);
  delete catCollapsed[catName];
  if (collapsed.has(catName)) { collapsed.delete(catName); saveCollapsed(); }
  if (activeChip === catName) activeChip = '__all__';
  catOrder = catOrder.filter(c => c !== catName);
  delete itemOrder[catName];
  collectCategories();
  markDirty();
  renderCatEditorList();
  render();
  showToast('\u30ab\u30c6\u30b4\u30ea\u30fc\u3092\u524a\u9664\u3057\u307e\u3057\u305F');
}

function showCatSuggestions(id) {
  const el = document.getElementById('sug_' + id);
  if (el && el.children.length > 0) el.classList.add('show');
}

function hideCatSuggestions(id) {
  const el = document.getElementById('sug_' + id);
  if (el) el.classList.remove('show');
}

function toggleCategory(catId, catName) {
  catCollapsed[catName] = !catCollapsed[catName];
  const bodyEl = document.getElementById(catId);
  if (bodyEl) {
    bodyEl.previousElementSibling.classList.toggle('collapsed');
    bodyEl.classList.toggle('collapsed');
  }
}

function moveCategory(catName, direction) {
  const currentOrder = getDisplayCatOrder();
  const idx = currentOrder.indexOf(catName);
  if (idx === -1) return;
  const newIdx = idx + direction;
  if (newIdx < 0 || newIdx >= currentOrder.length) return;
  currentOrder.splice(idx, 1);
  currentOrder.splice(newIdx, 0, catName);
  catOrder = currentOrder;
  markDirty();
  render();
}

function onCatDragStart(e) {
  if (!e.target.classList.contains('drag-handle-cat') && e.target.closest('.card')) return;
  dragCatSrc = e.target.closest('.category-group').dataset.cat;
  e.target.closest('.category-group').classList.add('dragging');
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', 'category');
}

function onCatDragOver(e) {
  e.preventDefault();
  if (!dragCatSrc) return;
  const group = e.target.closest('.category-group');
  if (group && group.dataset.cat !== dragCatSrc) group.classList.add('drag-over-cat');
}

function onCatDragLeave(e) {
  const group = e.target.closest('.category-group');
  if (group) group.classList.remove('drag-over-cat');
}

function onCatDrop(e) {
  e.preventDefault();
  const group = e.target.closest('.category-group');
  if (group) group.classList.remove('drag-over-cat');
  if (!dragCatSrc) return;
  const dstCat = group.dataset.cat;
  if (dragCatSrc === dstCat) return;
  const currentOrder = getDisplayCatOrder();
  const srcIdx = currentOrder.indexOf(dragCatSrc);
  const dstIdx = currentOrder.indexOf(dstCat);
  if (srcIdx === -1 || dstIdx === -1) return;
  currentOrder.splice(srcIdx, 1);
  currentOrder.splice(dstIdx, 0, dragCatSrc);
  catOrder = currentOrder;
  markDirty();
  render();
}

function onCatDragEnd(e) {
  document.querySelectorAll('.category-group').forEach(el => {
    el.classList.remove('dragging');
    el.classList.remove('drag-over-cat');
  });
  dragCatSrc = null;
}

function getDisplayCatOrder() {
  const groups = {};
  bookmarks.forEach((s, i) => {
    const cat = getCat(i);
    if (cat) groups[cat] = true;
  });
  const allCats = Object.keys(groups);
  if (catOrder.length > 0) {
    return catOrder.filter(c => allCats.includes(c)).concat(allCats.filter(c => !catOrder.includes(c)));
  }
  return allCats.sort();
}

function setCategory(i, cat) {
  editedCats[i] = cat;
  markDirty();
  render();
}

function updateCategory(i, val) {
  const cat = val.trim();
  editedCats[i] = cat;
  if (cat && !allCategories.includes(cat)) allCategories.push(cat);
  markDirty();
  render();
}

function onDragStart(e) {
  dragSrc = parseInt(e.target.dataset.index);
  e.target.classList.add('dragging');
  e.dataTransfer.effectAllowed = 'move';
}

function onDragOver(e) {
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
  const card = e.target.closest('.card');
  if (card && !card.classList.contains('dragging')) card.classList.add('drag-over');
}

function onDragLeave(e) {
  const card = e.target.closest('.card');
  if (card) card.classList.remove('drag-over');
}

function onDrop(e) {
  e.preventDefault();
  const card = e.target.closest('.card');
  if (card) card.classList.remove('drag-over');
  if (dragSrc === null || !card) return;
  const dstIndex = parseInt(card.dataset.index);
  if (dragSrc === dstIndex) return;

  const srcUrl = bookmarks[dragSrc].url;
  const dstUrl = bookmarks[dstIndex].url;
  const cat = getCat(dragSrc);

  const item = bookmarks.splice(dragSrc, 1)[0];
  const adjustedIndex = dstIndex > dragSrc ? dstIndex - 1 : dstIndex;
  bookmarks.splice(adjustedIndex, 0, item);
  editedCats = {};
  collectCategories();

  // 同一カテゴリー内でのドラッグ並び替えを itemOrder にも反映
  updateItemOrderFromDrag(cat, srcUrl, dstUrl);

  markDirty();
  render();
}

function updateItemOrderFromDrag(cat, srcUrl, dstUrl) {
  const key = getItemOrderKey(cat);
  // 現在のそのカテゴリー内表示順を基準に、srcUrlをdstUrlの位置へ移動
  const visibleInCat = bookmarks
    .map((s, i) => ({s, i}))
    .filter(({s, i}) => getCat(i) === cat)
    .map(({s}) => s.url);
  let order = itemOrder[key] && itemOrder[key].length > 0
    ? itemOrder[key].filter(u => visibleInCat.includes(u)).concat(visibleInCat.filter(u => !itemOrder[key].includes(u)))
    : visibleInCat.slice();
  const filtered = order.filter(u => u !== srcUrl);
  const dstIdx = filtered.indexOf(dstUrl);
  if (dstIdx === -1) {
    filtered.push(srcUrl);
  } else {
    filtered.splice(dstIdx, 0, srcUrl);
  }
  itemOrder[key] = filtered;
}

function onDragEnd(e) {
  e.target.classList.remove('dragging');
  document.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over'));
  dragSrc = null;
}

function startEdit(i) {
  bookmarks[i]._editing = true;
  render();
  const inp = document.getElementById('edit_' + i);
  if (inp) { inp.focus(); inp.select(); }
}

function stopEdit(i) {
  bookmarks[i]._editing = false;
  render();
}

function updateName(i, val) {
  bookmarks[i].name = val.trim();
  bookmarks[i]._editing = false;
  markDirty();
  render();
}

function startUrlEdit(i) {
  bookmarks[i]._urlEditing = true;
  render();
  const inp = document.getElementById('url_edit_' + i);
  if (inp) { inp.focus(); inp.select(); }
}

function stopUrlEdit(i) {
  bookmarks[i]._urlEditing = false;
  render();
}

function updateUrl(i, val) {
  const newUrl = val.trim();
  if (!newUrl) { stopUrlEdit(i); return; }
  const oldUrl = bookmarks[i].url;
  bookmarks[i].url = newUrl;
  bookmarks[i]._urlEditing = false;
  // itemOrder / starredOrder 内のURL参照も更新
  Object.keys(itemOrder).forEach(key => {
    itemOrder[key] = itemOrder[key].map(u => u === oldUrl ? newUrl : u);
  });
  starredOrder = starredOrder.map(u => u === oldUrl ? newUrl : u);
  markDirty();
  render();
}

function addBookmark() {
  const name = document.getElementById('addName').value.trim();
  const url = document.getElementById('addUrl').value.trim();
  const categoryInput = document.getElementById('addCategory').value.trim();
  const categorySelect = document.getElementById('addCategorySelect').value;
  const category = categoryInput || categorySelect;
  if (!name || !url) { showToast('\u540d\u524d\u3068URL\u3092\u5165\u529b\u3057\u3066\u304f\u3060\u3055\u3044'); return; }
  bookmarks.push({name, url, category, starred: false});
  if (category && !allCategories.includes(category)) allCategories.push(category);
  const key = getItemOrderKey(category);
  if (!itemOrder[key]) itemOrder[key] = [];
  itemOrder[key].push(url);
  document.getElementById('addName').value = '';
  document.getElementById('addUrl').value = '';
  document.getElementById('addCategory').value = '';
  document.getElementById('addCategorySelect').value = '';
  document.getElementById('addForm').classList.remove('show');
  markDirty();
  render();
  showToast('\u8ffd\u52a0\u3057\u307e\u3057\u305F');
}

function removeBookmark(i) {
  if (!confirm('\u3053\u306E\u30d6\u30c3\u30AF\u30DE\u30FC\u30AF\u3092\u524a\u9664\u3057\u307E\u3059\u304B\uFF1F')) return;
  const url = bookmarks[i].url;
  bookmarks.splice(i, 1);
  editedCats = {};
  collectCategories();
  Object.keys(itemOrder).forEach(key => {
    itemOrder[key] = itemOrder[key].filter(u => u !== url);
  });
  starredOrder = starredOrder.filter(u => u !== url);
  markDirty();
  render();
}

function starBookmark(i) {
  bookmarks[i].starred = true;
  if (!starredOrder.includes(bookmarks[i].url)) starredOrder.push(bookmarks[i].url);
  markDirty();
  render();
}

function unstarBookmark(i) {
  bookmarks[i].starred = false;
  starredOrder = starredOrder.filter(u => u !== bookmarks[i].url);
  markDirty();
  render();
}

function moveStarred(orderIdx, direction) {
  if (orderIdx < 0 || orderIdx >= starredOrder.length) return;
  const newIdx = orderIdx + direction;
  if (newIdx < 0 || newIdx >= starredOrder.length) return;
  const url = starredOrder[orderIdx];
  starredOrder.splice(orderIdx, 1);
  starredOrder.splice(newIdx, 0, url);
  markDirty();
  render();
}

function moveInCategory(i, direction) {
  const cat = getCat(i);
  const key = getItemOrderKey(cat);
  const catItems = bookmarks
    .map((s, idx) => ({s, idx}))
    .filter(({s, idx}) => getCat(idx) === cat);
  const catUrls = catItems.map(({s}) => s.url);
  const url = bookmarks[i].url;
  let order = itemOrder[key] && itemOrder[key].length > 0
    ? itemOrder[key].filter(u => catUrls.includes(u)).concat(catUrls.filter(u => !itemOrder[key].includes(u)))
    : catUrls.slice();
  const idx = order.indexOf(url);
  if (idx === -1) return;
  const newIdx = idx + direction;
  if (newIdx < 0 || newIdx >= order.length) return;
  order.splice(idx, 1);
  order.splice(newIdx, 0, url);
  itemOrder[key] = order;
  markDirty();
  render();
}

let starDragSrc = null;

function onStarDragStart(e) {
  const card = e.target.closest('.card');
  if (!card) return;
  starDragSrc = parseInt(card.dataset.starOrder);
  card.classList.add('dragging');
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', 'star');
}

function onStarDragOver(e) {
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
  const card = e.target.closest('.card');
  if (card && !card.classList.contains('dragging')) card.classList.add('drag-over');
}

function onStarDragLeave(e) {
  const card = e.target.closest('.card');
  if (card) card.classList.remove('drag-over');
}

function onStarDrop(e) {
  e.preventDefault();
  const card = e.target.closest('.card');
  if (card) card.classList.remove('drag-over');
  if (starDragSrc === null || !card) return;
  const dstOrderIdx = parseInt(card.dataset.starOrder);
  if (starDragSrc === dstOrderIdx) return;
  const url = starredOrder[starDragSrc];
  starredOrder.splice(starDragSrc, 1);
  starredOrder.splice(dstOrderIdx, 0, url);
  markDirty();
  render();
}

function onStarDragEnd(e) {
  const card = e.target.closest('.card');
  if (card) card.classList.remove('dragging');
  document.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over'));
  starDragSrc = null;
}

function saveAll() {
  bookmarks.forEach((s, i) => {
    delete s._editing;
    delete s._urlEditing;
    if (editedCats[i] !== undefined) s.category = editedCats[i];
  });
  editedCats = {};
  collectCategories();
  fetch('/api/bookmarks', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({bookmarks, cat_order: catOrder, starred_order: starredOrder, categories: allCategories, favicon_mode: faviconMode, item_order: itemOrder}),
  }).then(r => r.json()).then(() => {
    const b = document.getElementById('saveBtn');
    if (b) b.style.display = 'none';
    dirty = false;
    showToast('\u4fdd\u5b58\u3057\u307e\u3057\u305F');
  });
}

function exportData() {
  const data = { bookmarks, exportedAt: new Date().toISOString() };
  const blob = new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `selfmark-${new Date().toISOString().slice(0,10)}.json`;
  a.click();
  URL.revokeObjectURL(url);
  showToast('\u30a8\u30af\u30b9\u30dd\u30fc\u30c8\u3057\u307e\u3057\u305f');
}

function importData(e) {
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = function(ev) {
    try {
      const data = JSON.parse(ev.target.result);
      const items = data.bookmarks || data.manualServices || [];
      const existingUrls = new Set(bookmarks.map(s => s.url));
      let added = 0;
      items.forEach(s => {
        if (!existingUrls.has(s.url)) {
          const entry = {name: s.name || '', url: s.url || '', category: s.category || '', starred: s.starred || false};
          if (s.icon) entry.icon = s.icon;
          bookmarks.push(entry);
          added++;
        }
      });
      collectCategories();
      markDirty();
      render();
      showToast(added > 0 ? `${added} \u4ef6\u30a4\u30f3\u30dd\u30fc\u30c8\u3057\u307e\u3057\u305F\u3002\u4fdd\u5b58\u3057\u3066\u304f\u3060\u3055\u3044\u3002` : '\u65b0\u3057\u3044\u30d6\u30c3\u30af\u30de\u30fc\u30af\u306f\u3042\u308a\u307e\u305b\u3093\u3067\u3057\u305F');
    } catch(err) {
      showToast('\u30d5\u30a1\u30a4\u30eb\u306E\u8aad\u307f\u8fbc\u307f\u306b\u5931\u6557\u3057\u307e\u3057\u305F');
    }
  };
  reader.readAsText(file);
  e.target.value = '';
}

window.addEventListener('beforeunload', e => {
  if (dirty) { e.preventDefault(); e.returnValue = ''; }
});

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    if (document.getElementById('iconPickerModal').classList.contains('show')) closeIconPicker();
    else if (document.getElementById('editModal').classList.contains('show')) closeEdit();
    else if (document.getElementById('iconStoreModal').classList.contains('show')) closeIconManager();
    else if (document.getElementById('syncModal').classList.contains('show')) closeSyncSettings();
  }
});

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
  if (!confirm('selfmark サービスを再起動しますか？')) return;
  const b = document.getElementById('btnAdminRestart');
  b.disabled = true; b.textContent = '再起動中…';
  try { await fetch('/api/admin/restart', { method: 'POST' }); } catch(e) {}
  await waitForServer();
  location.reload();
}

async function installSub() {
  if (!confirm('selfmark-sub（閲覧専用ビュー・ポート3357）をインストールしますか？\n既にインストール済みの場合は最新版に入れ替わります。')) return;
  const b = document.getElementById('btnInstallSub');
  b.disabled = true; b.textContent = 'インストール中…';
  try {
    const res = await fetch('/api/admin/install-sub', { method: 'POST' });
    const d = await res.json();
    if (!res.ok || d.error) { alert('インストールに失敗しました\n' + (d.error || '')); }
    else { alert(d.message || 'selfmark-sub をインストールしました'); }
  } catch(e) { alert('インストールに失敗しました'); }
  b.disabled = false; b.textContent = 'selfmark-subインストール';
}

function downloadExtension() {
  const a = document.createElement('a');
  a.href = '/api/extension/download';
  a.download = 'selfmark-extension-v17.zip';
  document.body.appendChild(a);
  a.click();
  a.remove();
  showToast('Chrome 拡張機能（selfmark-extension-v17.zip）をダウンロードしました');
}

async function adminUpdate() {
  if (!confirm('GitHub から最新版を取得してアップデートしますか？\n完了後、サービスは自動で再起動されます。')) return;
  const b = document.getElementById('btnAdminUpdate');
  b.disabled = true; b.textContent = '更新中…';
  try {
    const res = await fetch('/api/admin/update', { method: 'POST' });
    const d = await res.json();
    if (!res.ok || d.error) { alert('アップデートに失敗しました\n' + (d.error || '')); b.disabled = false; b.textContent = 'アップデート'; return; }
  } catch(e) { alert('アップデートに失敗しました'); b.disabled = false; b.textContent = 'アップデート'; return; }
  b.textContent = '再起動中…';
  await waitForServer(120000);
  location.reload();
}

let syncRole = 'source';
let syncPeers = [];

function setSyncRole(role) {
  syncRole = (role === 'destination') ? 'destination' : 'source';
  document.getElementById('syncRoleSource').classList.toggle('active', syncRole === 'source');
  document.getElementById('syncRoleDest').classList.toggle('active', syncRole === 'destination');
  document.getElementById('syncRoleDesc').textContent = syncRole === 'source'
    ? 'このPCが「同期元」: 相手（同期先）へデータを送ります。保存すると相手は自動で「同期先」になります。'
    : 'このPCが「同期先」: 相手（同期元）からデータを受け取ります。保存すると相手は自動で「同期元」になります。';
}

function fmtSyncDate(s) {
  try {
    const d = new Date(s);
    if (isNaN(d)) return s;
    const p = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}/${p(d.getMonth() + 1)}/${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  } catch { return s; }
}

function fmtSyncText(s) {
  if (typeof s !== 'string') return s;
  return s.replace(/\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?/g, m => fmtSyncDate(m));
}

function renderSyncStatus(cfg) {
  const el = document.getElementById('syncStatus');
  const roleLabel = (cfg.role === 'destination') ? '同期先' : '同期元';
  const peerLabel = cfg.peer_name ? `${cfg.peer_name}（${cfg.peer}）` : (cfg.peer || '未設定');
  const last = cfg.last_sync ? `最終同期: ${fmtSyncDate(cfg.last_sync)}` : '最終同期: まだありません';
  const result = cfg.last_result ? `結果: ${fmtSyncText(cfg.last_result)}` : '';
  el.textContent = `役割: ${roleLabel} ／ 相手: ${peerLabel} ／ 時刻: ${cfg.sync_time || '--:--'} ／ ${last}${result ? ' ／ ' + result : ''}`;
}

async function openSyncSettings() {
  document.getElementById('syncModal').classList.add('show');
  document.getElementById('syncStatus').textContent = '読み込み中…';
  setSyncRole('source');
  try {
    const [cfgRes, peerRes] = await Promise.all([fetch('/api/sync/config'), fetch('/api/sync/peers')]);
    const cfg = await cfgRes.json();
    const peers = await peerRes.json();
    syncPeers = peers.peers || [];
    setSyncRole(cfg.role === 'destination' ? 'destination' : 'source');
    const sel = document.getElementById('syncPeer');
    const cur = cfg.peer || '';
    // 稼働中（online）を先頭に表示
    sel.innerHTML = '<option value="">-- 選択してください --</option>' + syncPeers.map(p => {
      const state = p.online ? '' : '（オフライン）';
      const label = `${p.name} [${p.dns}]${state}`;
      const selected = (p.url === cur) ? ' selected' : '';
      return `<option value="${escapeHtml(p.url)}" data-name="${escapeHtml(p.name)}"${selected}>${escapeHtml(label)}</option>`;
    }).join('');
    if (cur && !syncPeers.some(p => p.url === cur)) {
      sel.innerHTML += `<option value="${escapeHtml(cur)}" selected>${escapeHtml(cfg.peer_name ? cfg.peer_name + ' [' + cur + ']' : cur)}（一覧外）</option>`;
    }
    document.getElementById('syncTime').value = cfg.sync_time || '03:00';
    renderSyncStatus(cfg);
  } catch(e) {
    document.getElementById('syncStatus').textContent = '設定の読み込みに失敗しました';
  }
}

function closeSyncSettings() {
  document.getElementById('syncModal').classList.remove('show');
}

async function saveSyncSettings() {
  const sel = document.getElementById('syncPeer');
  const peer = sel.value;
  const peerName = sel.selectedOptions.length > 0 ? (sel.selectedOptions[0].dataset.name || '') : '';
  const syncTime = document.getElementById('syncTime').value;
  if (!peer) { showToast('同期相手を選択してください'); return; }
  if (!syncTime) { showToast('同期時刻を指定してください'); return; }
  try {
    const res = await fetch('/api/sync/config', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({role: syncRole, peer, peer_name: peerName, sync_time: syncTime}),
    });
    const d = await res.json();
    if (!res.ok || !d.ok) { showToast(d.error || '保存に失敗しました'); return; }
    showToast(d.peer_message || '保存しました');
    const cfgRes = await fetch('/api/sync/config');
    renderSyncStatus(await cfgRes.json());
  } catch(e) {
    showToast('保存に失敗しました');
  }
}

async function runSyncNow() {
  const b = document.getElementById('btnSyncNow');
  b.disabled = true; b.textContent = '同期中…';
  try {
    const res = await fetch('/api/sync/run', { method: 'POST' });
    const d = await res.json();
    if (!res.ok || !d.ok) { showToast(fmtSyncText(d.error || '同期に失敗しました')); }
    else {
      showToast(fmtSyncText(d.message || '同期しました'));
      refresh();
    }
    const cfgRes = await fetch('/api/sync/config');
    renderSyncStatus(await cfgRes.json());
  } catch(e) {
    showToast('同期に失敗しました');
  }
  b.disabled = false; b.textContent = '⚡ 今すぐ同期';
}

async function stopSync() {
  if (!confirm('同期を停止しますか？\n相手の指定が外れ、自動同期も行われなくなります。')) return;
  const b = document.getElementById('btnSyncStop');
  b.disabled = true; b.textContent = '停止中…';
  try {
    const res = await fetch('/api/sync/stop', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({}),
    });
    const d = await res.json();
    if (!res.ok || !d.ok) { showToast(d.error || '停止に失敗しました'); }
    else { showToast(d.peer_message || '同期を停止しました'); }
    const cfgRes = await fetch('/api/sync/config');
    const cfg = await cfgRes.json();
    const sel = document.getElementById('syncPeer');
    if (sel) sel.value = '';
    renderSyncStatus(cfg);
  } catch(e) {
    showToast('停止に失敗しました');
  }
  b.disabled = false; b.textContent = '同期を停止';
}

document.getElementById('qa-url').addEventListener('input', updateQuickPreview);
document.getElementById('search').addEventListener('input', e => { query = e.target.value.trim(); render(); });

fetch('/api/version').then(res => res.json()).then(v => { document.getElementById('appVersion').textContent = 'v.' + v.version; }).catch(() => {});

refresh();
</script>
</body>
</html>"""


def load_sync_config():
    cfg = dict(DEFAULT_SYNC_CONFIG)
    if os.path.exists(SYNC_CONFIG_FILE):
        try:
            with open(SYNC_CONFIG_FILE, "r") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for key in DEFAULT_SYNC_CONFIG:
                    if isinstance(data.get(key), str):
                        cfg[key] = data[key]
        except (OSError, ValueError, UnicodeDecodeError):
            pass
    if cfg.get("role") not in (SYNC_ROLE_SOURCE, SYNC_ROLE_DEST):
        cfg["role"] = SYNC_ROLE_SOURCE
    if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", cfg.get("sync_time") or ""):
        cfg["sync_time"] = DEFAULT_SYNC_CONFIG["sync_time"]
    return cfg


def save_sync_config(cfg):
    tmp = SYNC_CONFIG_FILE + ".tmp"
    with _SYNC_LOCK:
        with open(tmp, "w") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, SYNC_CONFIG_FILE)


def _self_port():
    try:
        return str(int(os.environ.get("PORT", "3356")))
    except (TypeError, ValueError):
        return "3356"


def _tailscale_status_json():
    try:
        r = subprocess.run(["tailscale", "status", "--json"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return None
        data = json.loads(r.stdout or "{}")
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def get_self_base_url():
    data = _tailscale_status_json()
    try:
        if data:
            dns = (data.get("Self") or {}).get("DNSName", "").rstrip(".")
            if dns:
                return f"https://{dns}:{_self_port()}"
    except Exception:
        pass
    return ""


def get_self_host_name():
    data = _tailscale_status_json()
    try:
        if data:
            return (data.get("Self") or {}).get("HostName", "")
    except Exception:
        pass
    return ""


def get_sync_peer_list():
    """tailnet内のマシン一覧を返す（稼働中を先頭に）。"""
    data = _tailscale_status_json()
    if not data:
        return []
    peers = data.get("Peer") or {}
    self_dns = ((data.get("Self") or {}).get("DNSName", "") or "").rstrip(".")
    port = _self_port()
    result = []
    for p in peers.values():
        if not isinstance(p, dict):
            continue
        dns = (p.get("DNSName", "") or "").rstrip(".")
        if not dns or dns == self_dns:
            continue
        name = p.get("HostName", "") or dns
        ips = p.get("TailscaleIPs") or []
        result.append({
            "name": name,
            "dns": dns,
            "url": f"https://{dns}:{port}",
            "ip": ips[0] if ips else "",
            "os": p.get("OS", "") or "",
            "online": bool(p.get("Online")),
        })
    result.sort(key=lambda x: (not x["online"], x["name"].lower()))
    return result


def _http_get_json(url, timeout=30):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={"User-Agent": "selfmark-sync"})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_post_json(url, payload, timeout=60):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"User-Agent": "selfmark-sync",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        text = resp.read().decode("utf-8") or "{}"
        try:
            return json.loads(text)
        except ValueError:
            return {}


def _is_valid_peer_url(url):
    if not isinstance(url, str) or not url or len(url) > 500:
        return False
    try:
        parts = urllib.request.urlsplit(url)
        return parts.scheme in ("http", "https") and bool(parts.hostname)
    except Exception:
        return False


def _read_bundle_file(path, max_bytes):
    try:
        size = os.path.getsize(path)
        if size <= 0 or size > max_bytes:
            return None
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii")
    except OSError:
        return None


def build_sync_bundle():
    data = load_data()
    bundle_data = {
        "bookmarks": data.get("bookmarks", []),
        "cat_order": data.get("cat_order", []),
        "starred_order": data.get("starred_order", []),
        "categories": data.get("categories", []),
        "favicon_mode": data.get("favicon_mode", 0),
        "item_order": data.get("item_order", {}),
    }
    icons = []
    for name in list_icon_files():
        content = _read_bundle_file(os.path.join(ICON_DIR, name), ICON_MAX_BYTES)
        if content is not None:
            icons.append({"name": name, "content": content})
    favicons = []
    try:
        names = sorted(os.listdir(FAVICON_CACHE_DIR)) if os.path.isdir(FAVICON_CACHE_DIR) else []
    except OSError:
        names = []
    for name in names:
        if not re.match(r"^[0-9a-f]{16}\.ico$", name):
            continue
        content = _read_bundle_file(os.path.join(FAVICON_CACHE_DIR, name), 512 * 1024)
        if content is not None:
            favicons.append({"name": name, "content": content})
    return {
        "version": 1,
        "exported_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "data": bundle_data,
        "icons": icons,
        "favicons": favicons,
    }


def apply_sync_bundle(bundle):
    """同期バンドルを検証してローカルに反映する（片方向ミラー）。"""
    if not isinstance(bundle, dict):
        raise ValueError("invalid bundle")
    data = bundle.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("bookmarks"), list):
        raise ValueError("invalid bundle data")
    try:
        favicon_mode = int(data.get("favicon_mode", 0))
    except (TypeError, ValueError):
        raise ValueError("invalid favicon_mode")
    with _DATA_LOCK:
        save_data({
            "bookmarks": data.get("bookmarks", []),
            "cat_order": data.get("cat_order", []),
            "starred_order": data.get("starred_order", []),
            "categories": data.get("categories", []),
            "favicon_mode": favicon_mode,
            "item_order": data.get("item_order", {}),
        })
    # icon/ をミラー（送信側に無いファイルは削除）
    wanted_icons = {}
    for entry in bundle.get("icons", []) or []:
        if not isinstance(entry, dict):
            continue
        raw = entry.get("name", "")
        name = os.path.basename(raw) if isinstance(raw, str) else ""
        if name != raw or os.path.splitext(name)[1].lower() not in ICON_ALLOWED_EXTS:
            continue
        content = entry.get("content", "")
        if not isinstance(content, str) or not content:
            continue
        try:
            wanted_icons[name] = base64.b64decode(content, validate=True)
        except Exception:
            continue
        if len(wanted_icons[name]) > ICON_MAX_BYTES:
            del wanted_icons[name]
    os.makedirs(ICON_DIR, exist_ok=True)
    try:
        for name in list_icon_files():
            if name not in wanted_icons:
                try:
                    os.remove(os.path.join(ICON_DIR, name))
                except OSError:
                    pass
        for name, content in wanted_icons.items():
            try:
                with open(os.path.join(ICON_DIR, name), "wb") as f:
                    f.write(content)
            except OSError:
                pass
    except Exception:
        pass
    # favicon_cache をミラー
    wanted_favicons = {}
    for entry in bundle.get("favicons", []) or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name", "")
        if not isinstance(name, str) or not re.match(r"^[0-9a-f]{16}\.ico$", name):
            continue
        content = entry.get("content", "")
        if not isinstance(content, str) or not content:
            continue
        try:
            wanted_favicons[name] = base64.b64decode(content, validate=True)
        except Exception:
            continue
    os.makedirs(FAVICON_CACHE_DIR, exist_ok=True)
    try:
        try:
            existing = os.listdir(FAVICON_CACHE_DIR)
        except OSError:
            existing = []
        for name in existing:
            if re.match(r"^[0-9a-f]{16}\.ico$", name) and name not in wanted_favicons:
                try:
                    os.remove(os.path.join(FAVICON_CACHE_DIR, name))
                except OSError:
                    pass
        for name, content in wanted_favicons.items():
            try:
                with open(os.path.join(FAVICON_CACHE_DIR, name), "wb") as f:
                    f.write(content)
            except OSError:
                pass
    except Exception:
        pass


def _mark_sync_result(ok, message):
    try:
        cfg = load_sync_config()
        now = datetime.datetime.now().isoformat(timespec="seconds")
        if ok:
            cfg["last_sync"] = now
        cfg["last_result"] = message[:300]
        save_sync_config(cfg)
    except Exception:
        pass


def sync_push_to_peer(peer_url):
    """同期元→同期先へ push（同期元側で実行）。"""
    if not _is_valid_peer_url(peer_url):
        raise ValueError("同期先が未設定です")
    bundle = build_sync_bundle()
    try:
        _http_post_json(peer_url.rstrip("/") + "/api/sync/import", bundle, timeout=60)
    except Exception as e:
        raise RuntimeError(f"送信に失敗しました: {e}")
    _mark_sync_result(True, f"同期しました（送信・{bundle['exported_at']}）")
    return bundle.get("exported_at", "")


def sync_pull_from_peer(peer_url):
    """同期先が同期元から pull（同期先側で実行）。"""
    if not _is_valid_peer_url(peer_url):
        raise ValueError("同期元が未設定です")
    try:
        bundle = _http_get_json(peer_url.rstrip("/") + "/api/sync/export", timeout=60)
    except Exception as e:
        raise RuntimeError(f"取得に失敗しました: {e}")
    apply_sync_bundle(bundle)
    exported_at = bundle.get("exported_at", "") if isinstance(bundle, dict) else ""
    _mark_sync_result(True, f"同期しました（受信・{exported_at}）")
    return exported_at


def _sync_scheduler_tick(now=None):
    cfg = load_sync_config()
    peer = (cfg.get("peer") or "").strip()
    if not peer or not _is_valid_peer_url(peer):
        return False
    sync_time = cfg.get("sync_time") or DEFAULT_SYNC_CONFIG["sync_time"]
    if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", sync_time):
        return False
    now = now or datetime.datetime.now()
    if now.strftime("%H:%M") < sync_time:
        return False
    last = cfg.get("last_sync") or ""
    if len(last) >= 10 and last[:10] == now.date().isoformat():
        return False
    if not _SYNC_RUN_LOCK.acquire(blocking=False):
        return False
    try:
        role = cfg.get("role")
        if role == SYNC_ROLE_SOURCE:
            sync_push_to_peer(peer)
        elif role == SYNC_ROLE_DEST:
            sync_pull_from_peer(peer)
        else:
            return False
        return True
    except Exception as e:
        _mark_sync_result(False, f"自動同期に失敗しました: {e}")
        return False
    finally:
        try:
            _SYNC_RUN_LOCK.release()
        except RuntimeError:
            pass


def _sync_scheduler_loop():
    while True:
        try:
            time.sleep(30)
            _sync_scheduler_tick()
        except Exception:
            continue


def start_sync_scheduler():
    global _SYNC_THREAD_STARTED
    if _SYNC_THREAD_STARTED:
        return
    _SYNC_THREAD_STARTED = True
    threading.Thread(target=_sync_scheduler_loop, daemon=True).start()


@app.after_request
def disable_response_cache(response):
    if request.path in {
        "/api/bookmarks", "/api/favicon", "/api/icons", "/api/extension/download",
        "/api/sync/config", "/api/sync/peers", "/api/sync/export",
        "/favicon.png", "/favicon.ico", "/selfmark.png",
        "/apple-touch-icon.png", "/apple-touch-icon-precomposed.png",
    } or request.path.startswith("/icon/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/")
def index():
    return Response(HTML, content_type="text/html")


@app.route("/favicon.png")
def serve_favicon():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "favicon.png")
    if os.path.exists(path):
        with open(path, "rb") as f:
            return Response(f.read(), content_type="image/png")
    return "", 404


@app.route("/selfmark.png")
def serve_selfmark_icon():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "selfmark.png")
    if os.path.exists(path):
        with open(path, "rb") as f:
            return Response(f.read(), content_type="image/png")
    return "", 404


@app.route("/apple-touch-icon.png")
@app.route("/apple-touch-icon-precomposed.png")
def serve_apple_touch_icon():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "selfmark.png")
    if os.path.exists(path):
        with open(path, "rb") as f:
            return Response(f.read(), content_type="image/png")
    return "", 404


@app.route("/favicon.ico")
def serve_favicon_ico():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "favicon.png")
    if os.path.exists(path):
        with open(path, "rb") as f:
            return Response(f.read(), content_type="image/png")
    return "", 404


@app.route("/api/bookmarks", methods=["GET", "POST"])
def api_bookmarks():
    if request.method == "POST":
        data = request.json
        if not isinstance(data, dict):
            return jsonify({"ok": False, "error": "invalid request"}), 400
        try:
            favicon_mode = int(data.get("favicon_mode", 0))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "invalid favicon_mode"}), 400
        if not isinstance(data.get("bookmarks", []), list):
            return jsonify({"ok": False, "error": "invalid bookmarks"}), 400
        with _DATA_LOCK:
            save_data({
                "bookmarks": data.get("bookmarks", []),
                "cat_order": data.get("cat_order", []),
                "starred_order": data.get("starred_order", []),
                "categories": data.get("categories", []),
                "favicon_mode": favicon_mode,
                "item_order": data.get("item_order", {}),
            })
        return jsonify({"ok": True})

    return jsonify(load_data())


@app.route("/api/favicon-setting", methods=["POST"])
def api_favicon_setting():
    data = request.json
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "invalid request"}), 400
    try:
        favicon_mode = int(data.get("favicon_mode", 0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "invalid favicon_mode"}), 400
    with _DATA_LOCK:
        current = load_data()
        current["favicon_mode"] = favicon_mode
        save_data(current)
    return jsonify({"ok": True})


@app.route("/api/favicon")
def api_favicon():
    url = request.args.get("url", "")
    if not url:
        return "", 404
    path = get_favicon_path(url)
    if os.path.exists(path):
        with open(path, "rb") as f:
            data = f.read()
        return Response(data, content_type="image/png")
    if fetch_and_cache_favicon(url) and os.path.exists(path):
        with open(path, "rb") as f:
            data = f.read()
        return Response(data, content_type="image/png")
    return "", 404


@app.route("/api/favicon-upload", methods=["POST"])
def api_favicon_upload():
    url = request.form.get("url", "")
    if not url:
        return jsonify({"ok": False, "error": "no url"}), 400
    file = request.files.get("file")
    if not file:
        return jsonify({"ok": False, "error": "no file"}), 400
    data = file.read()
    if not data:
        return jsonify({"ok": False, "error": "empty file"}), 400
    path = get_favicon_path(url)
    try:
        resize_and_save_image(data, path)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/favicon-fetch-all", methods=["POST"])
def api_favicon_fetch_all():
    data = request.json
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "invalid request"}), 400
    urls = data.get("urls", [])
    if not isinstance(urls, list):
        return jsonify({"ok": False, "error": "invalid urls"}), 400
    for url in urls:
        if not isinstance(url, str) or not url:
            continue
        path = get_favicon_path(url)
        if not os.path.exists(path):
            fetch_and_cache_favicon(url)
    return jsonify({"ok": True})


@app.route("/api/icons")
def api_icons():
    import urllib.parse
    names = list_icon_files()
    return jsonify({"icons": [{"name": n, "url": f"/icon/{urllib.parse.quote(n)}"} for n in names]})


@app.route("/icon/<path:filename>")
def serve_icon_file(filename):
    # パストラバーサル対策: basename のみ許可
    safe = os.path.basename(filename)
    if safe != filename or os.path.splitext(safe)[1].lower() not in ICON_ALLOWED_EXTS:
        return "", 404
    path = os.path.join(ICON_DIR, safe)
    if not os.path.isfile(path):
        return "", 404
    import mimetypes
    ctype, _ = mimetypes.guess_type(safe)
    if not ctype:
        ctype = "application/octet-stream"
    with open(path, "rb") as f:
        return Response(f.read(), content_type=ctype)


@app.route("/api/icon-upload", methods=["POST"])
def api_icon_upload():
    file = request.files.get("file")
    if not file:
        return jsonify({"ok": False, "error": "no file"}), 400
    data = file.read(ICON_MAX_BYTES + 1)
    if not data:
        return jsonify({"ok": False, "error": "empty file"}), 400
    if len(data) > ICON_MAX_BYTES:
        return jsonify({"ok": False, "error": "file too large (max 5MB)"}), 400
    import urllib.parse
    # 保存形式に合わせて拡張子を正規化してから一意化（.gif/.webp等→.png）
    sanitized = sanitize_icon_filename(file.filename)
    stem, ext = os.path.splitext(sanitized)
    if ext.lower() not in (".jpg", ".jpeg", ".svg", ".ico"):
        sanitized = stem + ".png"
    filename = unique_icon_filename(sanitized)
    try:
        actual = save_icon_file(data, filename)
        return jsonify({"ok": True, "name": actual, "url": f"/icon/{urllib.parse.quote(actual)}"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/icon-delete", methods=["POST"])
def api_icon_delete():
    data = request.json
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "invalid request"}), 400
    raw_name = data.get("name", "")
    if not isinstance(raw_name, str) or not raw_name:
        return jsonify({"ok": False, "error": "no name"}), 400
    name = os.path.basename(raw_name)
    if name != raw_name or os.path.splitext(name)[1].lower() not in ICON_ALLOWED_EXTS:
        return jsonify({"ok": False, "error": "invalid name"}), 400
    path = os.path.join(ICON_DIR, name)
    # ディレクトリ外参照を拒否
    if os.path.dirname(os.path.abspath(path)) != os.path.abspath(ICON_DIR):
        return jsonify({"ok": False, "error": "invalid name"}), 400
    try:
        if os.path.isfile(path):
            os.remove(path)
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": "not found"}), 404
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def _delayed_restart(delay=1.0):
    def _run():
        time.sleep(delay)
        subprocess.run(["systemctl", "restart", SERVICE_NAME],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    threading.Thread(target=_run, daemon=True).start()


@app.route("/api/version")
def api_version():
    return jsonify({"version": APP_VERSION})


@app.route("/api/extension/download")
def api_extension_download():
    data = b""
    local = os.path.join(os.path.dirname(APP_PATH), EXTENSION_FILENAME)
    try:
        try:
            with open(local, "rb") as f:
                data = f.read()
        except FileNotFoundError:
            req = urllib.request.Request(EXTENSION_URL, headers={"User-Agent": "selfmark-updater"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
    except Exception:
        pass
    if not data:
        return jsonify({"error": "拡張機能の取得に失敗しました"}), 500
    return Response(data, content_type="application/zip",
                    headers={"Content-Disposition": f"attachment; filename={EXTENSION_FILENAME}"})


@app.route("/api/admin/restart", methods=["POST"])
def api_admin_restart():
    _delayed_restart()
    return jsonify({"ok": True})


@app.route("/api/admin/update", methods=["POST"])
def api_admin_update():
    try:
        req = urllib.request.Request(GITHUB_RAW_APP, headers={"User-Agent": "selfmark-updater"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            new_src = resp.read().decode("utf-8")
    except Exception as e:
        return jsonify({"error": f"GitHubからの取得に失敗しました: {e}"}), 500

    try:
        with open(APP_PATH, encoding="utf-8") as f:
            current_src = f.read()
    except Exception:
        current_src = ""
    if new_src == current_src:
        return jsonify({"ok": True, "updated": False, "message": "すでに最新版です"})

    tmp_path = APP_PATH + ".new"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(new_src)
    chk = subprocess.run([sys.executable, "-m", "py_compile", tmp_path], capture_output=True)
    shutil.rmtree(os.path.join(os.path.dirname(tmp_path), "__pycache__"), ignore_errors=True)
    if chk.returncode != 0:
        os.remove(tmp_path)
        return jsonify({"error": "ダウンロードしたapp.pyの構文チェックに失敗しました"}), 500
    try:
        shutil.copy2(APP_PATH, APP_PATH + ".bak")
        os.replace(tmp_path, APP_PATH)
    except Exception as e:
        return jsonify({"error": f"ファイルの置換に失敗しました: {e}"}), 500

    install_dir = os.path.dirname(APP_PATH)
    for icon_file in ["favicon.png", "selfmark.png"]:
        try:
            icon_url = f"{GITHUB_RAW_BASE}/{icon_file}"
            req = urllib.request.Request(icon_url, headers={"User-Agent": "selfmark-updater"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                icon_data = resp.read()
            if icon_data:
                with open(os.path.join(install_dir, icon_file), "wb") as f:
                    f.write(icon_data)
        except Exception:
            pass

    _delayed_restart()
    return jsonify({"ok": True, "updated": True, "message": "アップデート完了。サービスを再起動します"})


@app.route("/api/admin/install-sub", methods=["POST"])
def api_admin_install_sub():
    try:
        req = urllib.request.Request(SUB_INSTALLER_URL, headers={"User-Agent": "selfmark-updater"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            src = resp.read().decode("utf-8")
    except Exception as e:
        return jsonify({"error": f"GitHubからの取得に失敗しました: {e}"}), 500
    if not src.lstrip().startswith("#!/bin/bash"):
        return jsonify({"error": "ダウンロードしたインストーラが不正です"}), 500

    installer_path = os.path.join(os.path.dirname(APP_PATH), "install-selfmark-sub1.sh")
    tmp_path = installer_path + ".new"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(src)
    chk = subprocess.run(["bash", "-n", tmp_path], capture_output=True)
    if chk.returncode != 0:
        os.remove(tmp_path)
        return jsonify({"error": "インストーラの構文チェックに失敗しました"}), 500
    os.replace(tmp_path, installer_path)

    try:
        r = subprocess.run(["bash", installer_path, DATA_FILE, SUB_PORT],
                           capture_output=True, text=True, timeout=180,
                           stdin=subprocess.DEVNULL)
    except Exception as e:
        return jsonify({"error": f"インストーラの実行に失敗しました: {e}"}), 500
    if r.returncode != 0:
        detail = (r.stderr or r.stdout or "").strip()[-800:]
        return jsonify({"error": detail or "インストーラの実行に失敗しました"}), 500

    url = ""
    try:
        out = subprocess.run(["tailscale", "status", "--json"],
                             capture_output=True, text=True, timeout=10).stdout
        domain = json.loads(out).get("Self", {}).get("DNSName", "").rstrip(".")
        if domain:
            url = f"https://{domain}:{SUB_PORT}"
    except Exception:
        pass
    msg = "selfmark-sub のインストールが完了しました" + (f"（{url}）" if url else "")
    return jsonify({"ok": True, "url": url, "message": msg})


@app.route("/api/sync/config", methods=["GET"])
def api_sync_config():
    cfg = load_sync_config()
    cfg["self_url"] = get_self_base_url()
    cfg["self_name"] = get_self_host_name()
    return jsonify(cfg)


@app.route("/api/sync/config", methods=["POST"])
def api_sync_config_save():
    data = request.json
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "invalid request"}), 400
    role = data.get("role", "")
    peer = (data.get("peer", "") or "").strip()
    peer_name = (data.get("peer_name", "") or "").strip()[:100]
    sync_time = (data.get("sync_time", "") or "").strip()
    if role not in (SYNC_ROLE_SOURCE, SYNC_ROLE_DEST):
        return jsonify({"ok": False, "error": "同期元・同期先のいずれかを指定してください"}), 400
    if peer and not _is_valid_peer_url(peer):
        return jsonify({"ok": False, "error": "同期先のURLが不正です"}), 400
    if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", sync_time):
        return jsonify({"ok": False, "error": "同期時刻は HH:MM 形式で指定してください"}), 400
    cfg = load_sync_config()
    old_sync_time = cfg.get("sync_time", "")
    cfg["role"] = role
    cfg["peer"] = peer
    cfg["peer_name"] = peer_name
    cfg["sync_time"] = sync_time
    if sync_time != old_sync_time:
        # 同期時刻が変わったら当日の同期済みフラグをリセットし、
        # その日のうちに即時テストできるようにする。
        cfg["last_sync"] = ""
        cfg["last_result"] = "同期時刻を変更したため、当日の同期済みフラグをリセットしました"
    save_sync_config(cfg)
    # 相手側の役割を反対にそろえる（相手が旧バージョン等で失敗しても保存自体は成功扱い）
    peer_notified = False
    peer_message = ""
    notify = data.get("notify_peer", True)
    if peer and notify:
        opposite = SYNC_ROLE_DEST if role == SYNC_ROLE_SOURCE else SYNC_ROLE_SOURCE
        self_url = get_self_base_url()
        payload = {"role": opposite, "peer_url": self_url,
                   "peer_name": get_self_host_name(),
                   "sync_time": sync_time}
        try:
            _http_post_json(peer.rstrip("/") + "/api/sync/role", payload, timeout=10)
            peer_notified = True
            peer_message = "相手側を「%s」に切り替え、同期時刻（%s）を共有しました" % ("同期先" if opposite == SYNC_ROLE_DEST else "同期元", sync_time)
        except Exception as e:
            peer_message = f"相手側への通知に失敗しました（相手のselfmarkを最新版に更新してください）: {e}"
    return jsonify({"ok": True, "peer_notified": peer_notified,
                    "peer_message": peer_message})


@app.route("/api/sync/role", methods=["POST"])
def api_sync_role():
    """相手PCからの役割連動用。自分の役割を相手の反対に設定し、同期時刻も共有する。"""
    data = request.json
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "invalid request"}), 400
    role = data.get("role", "")
    peer_url = (data.get("peer_url", "") or "").strip()
    peer_name = (data.get("peer_name", "") or "").strip()[:100]
    if role not in (SYNC_ROLE_SOURCE, SYNC_ROLE_DEST):
        return jsonify({"ok": False, "error": "invalid role"}), 400
    if peer_url and not _is_valid_peer_url(peer_url):
        return jsonify({"ok": False, "error": "invalid peer_url"}), 400
    cfg = load_sync_config()
    old_sync_time = cfg.get("sync_time", "")
    cfg["role"] = role
    if peer_url:
        cfg["peer"] = peer_url
        cfg["peer_name"] = peer_name
    # 同期時刻も共有する（旧バージョンからは送られてこないため任意扱い）。
    # 形式が正しい場合のみ反映し、不正な値では役割の更新を妨げない。
    sync_time = (data.get("sync_time", "") or "").strip()
    if re.match(r"^([01]\d|2[0-3]):[0-5]\d$", sync_time):
        cfg["sync_time"] = sync_time
    if re.match(r"^([01]\d|2[0-3]):[0-5]\d$", sync_time) and sync_time != old_sync_time:
        # 相手側で時刻が変わった場合も当日の同期済みフラグをリセットし、
        # その日のうちに即時テストできるようにする。
        cfg["last_sync"] = ""
        cfg["last_result"] = "同期時刻を変更したため、当日の同期済みフラグをリセットしました"
    save_sync_config(cfg)
    return jsonify({"ok": True, "role": role, "sync_time": cfg.get("sync_time", "")})


@app.route("/api/sync/peers")
def api_sync_peers():
    return jsonify({"peers": get_sync_peer_list(),
                    "self_url": get_self_base_url(),
                    "self_name": get_self_host_name()})


@app.route("/api/sync/export")
def api_sync_export():
    return jsonify(build_sync_bundle())


@app.route("/api/sync/import", methods=["POST"])
def api_sync_import():
    bundle = request.json
    try:
        apply_sync_bundle(bundle)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"反映に失敗しました: {e}"}), 500
    exported_at = bundle.get("exported_at", "") if isinstance(bundle, dict) else ""
    _mark_sync_result(True, f"同期しました（受信・{exported_at}）")
    return jsonify({"ok": True})


@app.route("/api/sync/run", methods=["POST"])
def api_sync_run():
    cfg = load_sync_config()
    peer = (cfg.get("peer") or "").strip()
    if not peer:
        return jsonify({"ok": False, "error": "同期相手が未設定です。先に相手を選択して保存してください"}), 400
    if not _SYNC_RUN_LOCK.acquire(blocking=False):
        return jsonify({"ok": False, "error": "同期を実行中です。しばらく待ってください"}), 409
    try:
        if cfg.get("role") == SYNC_ROLE_SOURCE:
            exported_at = sync_push_to_peer(peer)
            return jsonify({"ok": True, "direction": "push",
                            "message": f"同期先へ送信しました（{exported_at}）"})
        elif cfg.get("role") == SYNC_ROLE_DEST:
            exported_at = sync_pull_from_peer(peer)
            return jsonify({"ok": True, "direction": "pull",
                            "message": f"同期元から取得しました（{exported_at}）"})
        return jsonify({"ok": False, "error": "役割が不正です"}), 400
    except (ValueError, RuntimeError) as e:
        _mark_sync_result(False, f"手動同期に失敗しました: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500
    except Exception as e:
        _mark_sync_result(False, f"手動同期に失敗しました: {e}")
        return jsonify({"ok": False, "error": f"同期に失敗しました: {e}"}), 500
    finally:
        try:
            _SYNC_RUN_LOCK.release()
        except RuntimeError:
            pass


@app.route("/api/sync/unlink", methods=["POST"])
def api_sync_unlink():
    """相手PCからの停止連動用。相手が同期を停止したら自分の相手指定も外す。"""
    data = request.json
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "invalid request"}), 400
    peer_url = (data.get("peer_url", "") or "").strip().rstrip("/")
    cfg = load_sync_config()
    unlinked = False
    if peer_url and (cfg.get("peer") or "").rstrip("/") == peer_url:
        cfg["peer"] = ""
        cfg["peer_name"] = ""
        cfg["last_result"] = "相手側で同期が停止されたため、相手指定を解除しました"
        save_sync_config(cfg)
        unlinked = True
    return jsonify({"ok": True, "unlinked": unlinked})


@app.route("/api/sync/stop", methods=["POST"])
def api_sync_stop():
    data = request.json
    if data is not None and not isinstance(data, dict):
        return jsonify({"ok": False, "error": "invalid request"}), 400
    cfg = load_sync_config()
    old_peer = (cfg.get("peer") or "").strip()
    cfg["peer"] = ""
    cfg["peer_name"] = ""
    cfg["last_result"] = f"同期を停止しました（{datetime.datetime.now().isoformat(timespec='seconds')}）"
    save_sync_config(cfg)
    # 相手側にも通知し、相手の相手指定を外す（ベストエフォート）
    peer_notified = False
    peer_message = ""
    if old_peer and (not data or data.get("notify_peer", True)):
        try:
            r = _http_post_json(old_peer.rstrip("/") + "/api/sync/unlink",
                                {"peer_url": get_self_base_url()}, timeout=10)
            peer_notified = bool(isinstance(r, dict) and r.get("unlinked"))
            peer_message = "相手側の同期設定も解除しました" if peer_notified else "相手側への通知は届きましたが、相手の相手指定は既に外れていました"
        except Exception as e:
            peer_message = f"相手側への通知に失敗しました（相手のselfmarkを最新版に更新するか、相手側でも停止してください）: {e}"
    return jsonify({"ok": True, "peer_notified": peer_notified,
                    "peer_message": peer_message})


start_sync_scheduler()


if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "3356"))
    print(f"[INFO] selfmark: http://{host}:{port}", flush=True)
    app.run(host=host, port=port, debug=False, threaded=True)
