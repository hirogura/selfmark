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
APP_VERSION = "1.0.0"
SERVICE_NAME = os.environ.get("SELFMARK_SERVICE", "selfmark")
GITHUB_RAW_APP = "https://raw.githubusercontent.com/hirogura/selfmark/main/app.py"
app = Flask(__name__)


def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
            data.setdefault("favicon_mode", 0)
            data.setdefault("item_order", {})
            return data
    return {"bookmarks": [], "cat_order": [], "starred_order": [], "categories": [], "favicon_mode": 0, "item_order": {}}


def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


import hashlib
import urllib.request
import ssl
import base64
from pathlib import Path
from PIL import Image
from io import BytesIO

FAVICON_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "favicon_cache")


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
        favicon_url = origin + "/favicon.ico"
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(favicon_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5, context=ctx) as resp:
            data = resp.read(65536)
            if data and len(data) > 0:
                return resize_and_save_image(data, get_favicon_path(url))
    except Exception:
        pass
    return False


HTML = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect width='100' height='100' rx='20' fill='%230f0f23'/><text x='50' y='65' font-size='42' font-weight='bold' fill='%2300c9a7' text-anchor='middle' font-family='Arial'>S</text></svg>">
<link rel="apple-touch-icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect width='100' height='100' rx='20' fill='%230f0f23'/><text x='50' y='65' font-size='42' font-weight='bold' fill='%2300c9a7' text-anchor='middle' font-family='Arial'>S</text></svg>">
<title>selfmark</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #fafafa; color: #1a1a2e; line-height: 1.5; }
  .header { background: #0f0f23; color: white; padding: 20px 32px; display: flex; align-items: center; gap: 16px; }
  .header h1 { font-size: 22px; font-weight: 700; letter-spacing: -0.5px; }
  .header-admin { margin-left: auto; display: flex; align-items: center; gap: 10px; padding-left: 16px; }
  .btn-admin { padding: 6px 14px; border-radius: 8px; font-size: 12px; font-weight: 600; cursor: pointer; border: 1px solid rgba(255,255,255,.25); background: transparent; color: #e8e8f0; transition: all 0.15s ease; white-space: nowrap; }
  .btn-admin:hover { background: rgba(255,255,255,.12); }
  .btn-admin:disabled { opacity: .5; cursor: wait; }
  .version-label { font-size: 12px; font-weight: 600; color: #8a8aa8; white-space: nowrap; }
  .container { max-width: 960px; margin: 32px auto; padding: 0 24px; }
  .toolbar { display: flex; gap: 12px; margin-bottom: 24px; flex-wrap: wrap; align-items: center; }
  .btn { padding: 10px 20px; border: none; border-radius: 8px; font-size: 13px; font-weight: 600; cursor: pointer; transition: all 0.15s ease; letter-spacing: 0.3px; }
  .btn-primary { background: #0f0f23; color: white; }
  .btn-primary:hover { background: #2d2d5e; transform: translateY(-1px); }
  .btn-secondary { background: #e8e8f0; color: #1a1a2e; }
  .btn-secondary:hover { background: #d8d8e5; }
  .btn-success { background: #00c9a7; color: white; }
  .btn-success:hover { background: #00b396; transform: translateY(-1px); }
  .btn-danger { background: #e8e8f0; color: #1a1a2e; }
  .btn-danger:hover { background: #ff6b6b; color: white; }
  .btn-danger .trash-icon { color: inherit; }
  .favicon-toggle-wrap { display: flex; align-items: center; gap: 10px; padding: 8px 14px; background: white; border-radius: 8px; font-size: 13px; color: #555; border: 1px solid #e8e8f0; }
  .switch { position: relative; display: inline-block; width: 38px; height: 22px; flex-shrink: 0; }
  .switch input { opacity: 0; width: 0; height: 0; }
  .switch-slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: #d8d8e5; transition: .2s; border-radius: 22px; }
  .switch-slider:before { position: absolute; content: ""; height: 16px; width: 16px; left: 3px; bottom: 3px; background-color: white; transition: .2s; border-radius: 50%; }
  .switch input:checked + .switch-slider { background-color: #00c9a7; }
  .switch input:checked + .switch-slider:before { transform: translateX(16px); }
  .card { background: white; border-radius: 12px; padding: 18px 20px; margin-bottom: 10px; display: flex; align-items: center; gap: 16px; cursor: grab; transition: all 0.2s ease; border: 1px solid #f0f0f5; }
  .card:hover { border-color: #e0e0ea; transform: translateY(-2px); }
  .card:active { cursor: grabbing; }
  .card.dragging { opacity: 0.4; transform: scale(0.98); }
  .card.drag-over { border-top: 3px solid #00c9a7; }
  .card .drag-handle { color: #d0d0dd; font-size: 16px; cursor: grab; user-select: none; flex-shrink: 0; }
  .card .drag-handle:hover { color: #999; }
  .card .icon { width: 48px; height: 48px; border-radius: 10px; display: flex; align-items: center; justify-content: center; font-size: 22px; flex-shrink: 0; }
  .card .info { flex: 1; min-width: 0; }
  .card .name { font-size: 15px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #1a1a2e; }
  .card .url { font-size: 12px; color: #888; margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .card .url a { color: #6c5ce7; text-decoration: none; }
  .card .url a:hover { text-decoration: underline; }
  .card .actions { display: flex; gap: 6px; flex-shrink: 0; align-items: center; }
  .card .cat-badge { background: #e8f8f5; color: #00b894; padding: 3px 10px; border-radius: 20px; font-size: 11px; font-weight: 600; margin-left: 4px; }
  .category-group { margin-bottom: 28px; }
  .category-title { font-size: 13px; font-weight: 700; color: #555; margin-bottom: 10px; padding: 12px 16px; display: flex; align-items: center; gap: 10px; cursor: pointer; background: white; border-radius: 10px; user-select: none; border: 1px solid #f0f0f5; transition: all 0.15s ease; }
  .category-title:hover { background: #f8f8fc; border-color: #e0e0ea; }
  .category-title .cat-icon { font-size: 15px; }
  .category-title .arrow { font-size: 10px; transition: transform 0.2s; }
  .category-title.collapsed .arrow { transform: rotate(-90deg); }
  .category-body { display: block; }
  .category-body.collapsed { display: none; }
  .category-group.dragging { opacity: 0.4; transform: scale(0.98); }
  .category-group.drag-over-cat { border-top: 3px solid #00c9a7; padding-top: 10px; }
  .drag-handle-cat { color: #d0d0dd; font-size: 14px; cursor: grab; user-select: none; flex-shrink: 0; }
  .drag-handle-cat:hover { color: #999; }
  .cat-move-btn { background: none; border: none; color: #bbb; font-size: 11px; cursor: pointer; padding: 4px 6px; border-radius: 6px; line-height: 1; transition: all 0.15s ease; }
  .cat-move-btn:hover { background: #f0f0f5; color: #1a1a2e; }
  .cat-input { border: 1px solid #e8e8f0; border-radius: 8px; padding: 8px 12px; font-size: 13px; width: 120px; transition: border-color 0.15s ease; }
  .cat-input:focus { outline: none; border-color: #6c5ce7; }
  .cat-suggestions { position: absolute; background: white; border: 1px solid #e8e8f0; border-radius: 8px; z-index: 10; max-height: 150px; overflow-y: auto; display: none; }
  .cat-suggestions.show { display: block; }
  .cat-suggestions div { padding: 8px 14px; cursor: pointer; font-size: 13px; }
  .cat-suggestions div:hover { background: #f8f8fc; }
  .edit-input { border: 1px solid #e8e8f0; border-radius: 8px; padding: 8px 12px; font-size: 14px; width: 200px; transition: border-color 0.15s ease; }
  .empty { text-align: center; padding: 48px; color: #aaa; font-size: 14px; }
  .icon-bg { background: #f0f0f8; color: #6c5ce7; }
  .toast { position: fixed; bottom: 24px; right: 24px; background: #1a1a2e; color: white; padding: 14px 24px; border-radius: 10px; font-size: 13px; display: none; z-index: 100; font-weight: 500; }
  .add-form { background: white; border-radius: 12px; padding: 20px; margin-bottom: 20px; display: none; border: 1px solid #f0f0f5; }
  .add-form.show { display: block; }
  .add-form h3 { font-size: 14px; margin-bottom: 16px; color: #555; font-weight: 600; }
  .form-row { display: flex; gap: 10px; margin-bottom: 10px; }
  .form-row input { flex: 1; padding: 10px 14px; border: 1px solid #e8e8f0; border-radius: 8px; font-size: 14px; transition: border-color 0.15s ease; }
  .form-row input:focus { outline: none; border-color: #6c5ce7; }
  .cat-edit-item { display: flex; align-items: center; gap: 10px; padding: 10px 0; border-bottom: 1px solid #f5f5fa; }
  .cat-edit-item:last-child { border-bottom: none; }
  .cat-edit-item input { flex: 1; padding: 8px 12px; border: 1px solid #e8e8f0; border-radius: 8px; font-size: 14px; transition: border-color 0.15s ease; }
  .cat-edit-item input:focus { outline: none; border-color: #6c5ce7; }
  .cat-edit-item .cat-count { font-size: 11px; color: #aaa; min-width: 40px; }
  .cat-select { border: 1px solid #e8e8f0; border-radius: 8px; padding: 6px 10px; font-size: 12px; max-width: 120px; background: white; cursor: pointer; transition: border-color 0.15s ease; }
  .cat-select:focus { outline: none; border-color: #6c5ce7; }
  .star-btn { background: none; border: none; font-size: 18px; cursor: pointer; padding: 4px 6px; border-radius: 6px; line-height: 1; color: #d0d0dd; transition: all 0.15s ease; }
  .star-btn:hover { color: #fdcb6e; }
  .star-btn.on { color: #fdcb6e; }
  .star-section { margin-bottom: 28px; }
  .star-title { font-size: 13px; font-weight: 700; color: #f39c12; margin-bottom: 10px; padding: 12px 16px; display: flex; align-items: center; gap: 10px; background: linear-gradient(135deg, #fff9e6 0%, #fff3cd 100%); border-radius: 10px; border: 1px solid #ffeaa7; }
  .star-title .cat-icon { font-size: 15px; }
  .card.starred-view .actions { gap: 6px; }
  .card.starred-view .info { min-width: 0; }
  .card.starred-view .name { font-size: 14px; }
  .favicon-upload { cursor: pointer; transition: opacity 0.2s; }
  .favicon-upload:hover { opacity: 1 !important; }
  ::-webkit-scrollbar { width: 6px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: #d0d0dd; border-radius: 3px; }
  ::-webkit-scrollbar-thumb:hover { background: #aaa; }
</style>
</head>
<body>
<div class="header">
  <h1>selfmark</h1>
  <div class="header-admin">
    <button class="btn-admin" id="btnAdminUpdate" title="GitHubから最新版を取得してアップデート" onclick="adminUpdate()">アップデート</button>
    <button class="btn-admin" id="btnAdminRestart" title="selfmarkサービスを再起動" onclick="adminRestart()">再起動</button>
    <span class="version-label" id="appVersion"></span>
  </div>
</div>
<div class="container">
  <div class="toolbar">
    <button class="btn btn-success" onclick="toggleAddForm()">＋ ブックマーク追加</button>
    <button class="btn btn-primary" onclick="showCatEditor()">📂 カテゴリー編集</button>
    <label class="favicon-toggle-wrap">
      <select class="cat-select" id="faviconModeSelect" onchange="onFaviconModeChange(this.value)" style="font-size:13px;padding:4px 8px;">
        <option value="0">ファビコン取得しない</option>
        <option value="1">キャッシュ利用</option>
        <option value="2">ファビコン取得</option>
      </select>
    </label>
    <button class="btn btn-success" onclick="saveAll()" id="saveBtn" style="display:none">💾 保存</button>
  </div>

  <div class="add-form" id="addForm">
    <h3>ブックマークを追加</h3>
    <div class="form-row">
      <input type="text" id="addUrl" placeholder="URL (例: https://example.com)">
      <input type="text" id="addName" placeholder="名前 (例: Nextcloud)">
    </div>
    <div class="form-row">
      <select class="cat-select" id="addCategorySelect" style="max-width:200px" onchange="document.getElementById('addCategory').value=''">
        <option value="">-- カテゴリー選択 --</option>
      </select>
      <input type="text" id="addCategory" placeholder="または新規入力">
    </div>
    <button class="btn btn-primary" onclick="addBookmark()">追加</button>
    <button class="btn btn-secondary" onclick="toggleAddForm()">キャンセル</button>
  </div>

  <div class="add-form" id="catEditor">
    <h3>カテゴリー編集</h3>
    <div id="catEditorList"></div>
    <div class="form-row" style="margin-top:12px">
      <input type="text" id="newCatName" placeholder="新しいカテゴリー名">
      <button class="btn btn-primary" onclick="addCategory()">追加</button>
    </div>
    <button class="btn btn-secondary" onclick="closeCatEditor()" style="margin-top:8px">閉じる</button>
  </div>

  <div id="bookmarkList"></div>

  <div style="margin-top:32px; padding-top:16px; border-top:1px solid #ddd; display:flex; gap:8px; justify-content:center;">
    <button class="btn btn-secondary" onclick="exportData()">📥 エクスポート</button>
    <button class="btn btn-secondary" onclick="document.getElementById('importFile').click()">📤 インポート</button>
    <input type="file" id="importFile" accept=".json" style="display:none" onchange="importData(event)">
  </div>
</div>
<div class="toast" id="toast"></div>
<input type="file" id="faviconFileInput" accept="image/*" style="display:none" onchange="handleFaviconUpload(event)">

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

function escapeHtml(s) {
  const d = document.createElement('div');
  d.appendChild(document.createTextNode(s));
  return d.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.style.display = 'block';
  setTimeout(() => t.style.display = 'none', 2000);
}

function markDirty() {
  dirty = true;
  document.getElementById('saveBtn').style.display = 'inline-block';
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
    allCategories.forEach(c => catCollapsed[c] = true);
    catCollapsed['_none'] = true;
    document.getElementById('saveBtn').style.display = 'none';
    dirty = false;
    render();
  });
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

function renderCard(s, i, compact) {
  const cat = getCat(i);
  const isEditing = s._editing;
  const isUrlEditing = s._urlEditing;
  const catBadge = cat ? `<span class="cat-badge">${escapeHtml(cat)}</span>` : '';
  const isStarred = s.starred;
  const starClass = isStarred ? 'on' : '';
  const starIcon = isStarred ? '\u2605' : '\u2606';
  const starAction = isStarred ? `unstarBookmark(${i})` : `starBookmark(${i})`;
  const escapedUrl = escapeHtml(s.url).replace(/'/g, "\\'");
  let faviconHtml = '<span style="cursor:pointer;font-size:20px;opacity:0.6;" onclick="triggerFaviconUpload(\'' + escapedUrl + '\')" title="ファビコンを設定">\u{1F4CC}</span>';
  if (faviconMode >= 1) {
    const cachedSrc = '/api/favicon?url=' + encodeURIComponent(s.url);
    const liveSrc = (() => { try { return new URL(s.url).origin + '/favicon.ico'; } catch(e) { return ''; }})();
    if (faviconMode === 2 && liveSrc) {
      faviconHtml = '<img loading="lazy" src="' + liveSrc + '" width="24" height="24" style="border-radius:4px;cursor:pointer;" onclick="triggerFaviconUpload(\'' + escapedUrl + '\')" title="ファビコンを設定" onerror="this.src=\'' + cachedSrc + '\';this.onerror=function(){this.style.display=\'none\';this.nextElementSibling.style.display=\'block\'}"><span style="display:none;cursor:pointer;font-size:20px;opacity:0.6;" onclick="triggerFaviconUpload(\'' + escapedUrl + '\')" title="ファビコンを設定">\u{1F4CC}</span>';
    } else {
      faviconHtml = '<img loading="lazy" src="' + cachedSrc + '" width="24" height="24" style="border-radius:4px;cursor:pointer;" onclick="triggerFaviconUpload(\'' + escapedUrl + '\')" title="ファビコンを設定" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'block\'"><span style="display:none;cursor:pointer;font-size:20px;opacity:0.6;" onclick="triggerFaviconUpload(\'' + escapedUrl + '\')" title="ファビコンを設定">\u{1F4CC}</span>';
    }
  }

  if (compact) {
    const starIdx = starredOrder.indexOf(s.url);
    return `<div class="card starred-view" draggable="true" data-star-index="${i}" data-star-order="${starIdx}"
      ondragstart="onStarDragStart(event)" ondragover="onStarDragOver(event)" ondragleave="onStarDragLeave(event)" ondrop="onStarDrop(event)" ondragend="onStarDragEnd(event)">
      <span class="drag-handle">\u2807</span>
      <div class="icon icon-bg">${faviconHtml}</div>
      <div class="info">
        <div class="name">${escapeHtml(s.name)}</div>
        <div class="url"><a href="${escapeHtml(s.url)}" target="_blank">${escapeHtml(s.url)}</a></div>
      </div>
      <div class="actions">
        <button class="star-btn on" onclick="unstarBookmark(${i})" title="\u30b9\u30bf\u30fc\u5916\u308b">\u2605</button>
        <button class="cat-move-btn" onclick="moveStarred(${starIdx}, -1)" title="\u4e0a\u306b\u79fb\u52d5">\u25B2</button>
        <button class="cat-move-btn" onclick="moveStarred(${starIdx}, 1)" title="\u4e0b\u306b\u79fb\u52d5">\u25BC</button>
      </div>
    </div>`;
  }

  return `<div class="card" draggable="true" data-index="${i}"
    ondragstart="onDragStart(event)" ondragover="onDragOver(event)" ondragleave="onDragLeave(event)" ondrop="onDrop(event)" ondragend="onDragEnd(event)">
    <span class="drag-handle">\u2807</span>
    <div class="icon icon-bg">${faviconHtml}</div>
    <div class="info">
      ${isEditing
        ? `<input class="edit-input" value="${escapeHtml(s.name)}" onchange="updateName(${i}, this.value)" onblur="stopEdit(${i})" id="edit_${i}">`
        : `<div class="name">${escapeHtml(s.name)}${catBadge}</div>`
      }
      ${isUrlEditing
        ? `<input class="edit-input" style="margin-top:4px;font-size:12px;width:100%;" value="${escapeHtml(s.url)}" onchange="updateUrl(${i}, this.value)" onblur="stopUrlEdit(${i})" id="url_edit_${i}">`
        : `<div class="url"><a href="${escapeHtml(s.url)}" target="_blank">${escapeHtml(s.url)}</a></div>`
      }
    </div>
    <div class="actions">
      <button class="star-btn ${starClass}" onclick="${starAction}" title="\u304a\u6c17\u306b\u5165\u308a">${starIcon}</button>
      <button class="cat-move-btn" onclick="moveInCategory(${i}, -1)" title="\u4e0a\u306b\u79fb\u52d5">\u25B2</button>
      <button class="cat-move-btn" onclick="moveInCategory(${i}, 1)" title="\u4e0b\u306b\u79fb\u52d5">\u25BC</button>
      <select class="cat-select" onchange="updateCategory(${i}, this.value)">
        <option value="">\u672a\u5206\u985e</option>
        ${getSortedCategories().map(c => `<option value="${escapeHtml(c)}" ${c === cat ? 'selected' : ''}>${escapeHtml(c)}</option>`).join('')}
      </select>
      <button class="btn btn-secondary" onclick="startEdit(${i})">\u270f\uFE0F</button>
      <button class="btn btn-secondary" onclick="startUrlEdit(${i})" title="URL\u7de8\u96c6">\u{1F517}</button>
      <button class="btn btn-danger" onclick="removeBookmark(${i})"><span class="trash-icon">\u{1F5D1}\uFE0F</span></button>
    </div>
  </div>`;
}

function render() {
  const visible = bookmarks.map((s, i) => ({s, i, cat: getCat(i)}));
  const groups = {};
  const noCategory = [];
  visible.forEach(({s, i, cat}) => {
    if (cat) {
      if (!groups[cat]) groups[cat] = [];
      groups[cat].push({s, i});
    } else {
      noCategory.push({s, i});
    }
  });

  // カテゴリー内のアイテム順を itemOrder で調整
  Object.keys(groups).forEach(cat => {
    groups[cat] = applyItemOrderToGroup(groups[cat], cat);
  });
  const noCategorySorted = applyItemOrderToGroup(noCategory, '');

  const containerEl = document.getElementById('bookmarkList');
  let html = '';

  const starredItems = bookmarks.map((s, i) => ({s, i})).filter(({s}) => s.starred);
  starredItems.forEach(({s}) => {
    if (!starredOrder.includes(s.url)) starredOrder.push(s.url);
  });
  const sortedStarred = starredOrder.length > 0
    ? starredOrder.map(url => starredItems.find(({s}) => s.url === url)).filter(Boolean)
    : starredItems;
  const starredInOrder = sortedStarred;

  if (starredInOrder.length > 0) {
    html += `<div class="star-section">
      <div class="star-title"><span class="cat-icon">\u2b50</span> \u304a\u6c17\u306b\u5165\u308a</div>
      ${starredInOrder.map(({s, i}) => renderCard(s, i, true)).join('')}
    </div>`;
  }

  const defaultCats = [...new Set([...Object.keys(groups), ...allCategories])].sort();
  const sortedCats = catOrder.length > 0
    ? catOrder.filter(c => defaultCats.includes(c)).concat(defaultCats.filter(c => !catOrder.includes(c)))
    : defaultCats;

  sortedCats.forEach((cat, catIdx) => {
    const catId = 'cat-' + catIdx;
    const isCollapsed = catCollapsed[cat] !== false;
    const collapsedClass = isCollapsed ? 'collapsed' : '';
    const catItems = groups[cat] || [];
    html += `<div class="category-group" draggable="true" data-cat="${escapeHtml(cat)}"
      ondragstart="onCatDragStart(event)" ondragover="onCatDragOver(event)" ondragleave="onCatDragLeave(event)" ondrop="onCatDrop(event)" ondragend="onCatDragEnd(event)">
      <div class="category-title ${collapsedClass}" onclick="toggleCategory('${catId}', '${escapeHtml(cat).replace(/'/g, "\\'")}')">
        <span class="arrow">\u25BC</span>
        <span class="drag-handle-cat" onclick="event.stopPropagation()">\u2807</span>
        <span class="cat-icon">\u{1F4C2}</span> ${escapeHtml(cat)}
        <span style="margin-left:auto;display:flex;align-items:center;gap:2px;">
          <button class="cat-move-btn" onclick="event.stopPropagation();moveCategory('${escapeHtml(cat).replace(/'/g, "\\'")}', -1)" title="\u4e0a\u306b\u79fb\u52d5">\u25B2</button>
          <button class="cat-move-btn" onclick="event.stopPropagation();moveCategory('${escapeHtml(cat).replace(/'/g, "\\'")}', 1)" title="\u4e0b\u306b\u79fb\u52d5">\u25BC</button>
          <span style="font-size:12px;color:#999;margin-left:4px;">${catItems.length}</span>
        </span>
      </div>
      <div class="category-body ${collapsedClass}" id="${catId}">
        ${catItems.length > 0 ? catItems.map(({s, i}) => renderCard(s, i)).join('') : '<div class="empty" style="padding:16px;font-size:13px;">\u3053\u306e\u30ab\u30c6\u30b4\u30ea\u30fc\u306b\u306f\u307e\u3060\u30d6\u30c3\u30af\u30de\u30fc\u30af\u304c\u3042\u308a\u307e\u305b\u3093</div>'}
      </div>
    </div>`;
  });

  if (noCategory.length > 0) {
    const noCatId = 'cat-none';
    const isCollapsedNone = catCollapsed['_none'] !== false;
    const collapsedClassNone = isCollapsedNone ? 'collapsed' : '';
    if (sortedCats.length > 0) {
      html += `<div class="category-group" draggable="true" data-cat="_none"
        ondragstart="onCatDragStart(event)" ondragover="onCatDragOver(event)" ondragleave="onCatDragLeave(event)" ondrop="onCatDrop(event)" ondragend="onCatDragEnd(event)">
        <div class="category-title ${collapsedClassNone}" onclick="toggleCategory('${noCatId}', '_none')">
          <span class="arrow">\u25BC</span>
          <span class="drag-handle-cat" onclick="event.stopPropagation()">\u2807</span>
          <span class="cat-icon">\u{1F4CB}</span> \u672a\u5206\u985e
          <span style="margin-left:auto;display:flex;align-items:center;gap:2px;">
            <button class="cat-move-btn" onclick="event.stopPropagation();moveCategory('_none', -1)" title="\u4e0a\u306b\u79fb\u52d5">\u25B2</button>
            <button class="cat-move-btn" onclick="event.stopPropagation();moveCategory('_none', 1)" title="\u4e0b\u306b\u79fb\u52d5">\u25BC</button>
            <span style="font-size:12px;color:#999;margin-left:4px;">${noCategorySorted.length}</span>
          </span>
        </div>
        <div class="category-body ${collapsedClassNone}" id="${noCatId}">
          ${noCategorySorted.map(({s, i}) => renderCard(s, i)).join('')}
        </div>
      </div>`;
    } else {
      html = noCategorySorted.map(({s, i}) => renderCard(s, i)).join('');
    }
  }

  if (bookmarks.length === 0) {
    html = '<div class="empty">\u30d6\u30c3\u30af\u30de\u30fc\u30af\u304c\u3042\u308a\u307e\u305b\u3093\u3002\u201c\uff0b \u30d6\u30c3\u30af\u30de\u30fc\u30af\u8ffd\u52a0\u201d\u304b\u3089\u8ffd\u52a0\u3057\u3066\u304f\u3060\u3055\u3044\u3002</div>';
  }

  containerEl.innerHTML = html;
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
      <button class="btn btn-danger" onclick="deleteCategory('${escapeHtml(c).replace(/'/g, "\\'")}')" title="\u524a\u9664"><span class="trash-icon">\u{1F5D1}\uFE0F</span></button>
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
    document.getElementById('saveBtn').style.display = 'none';
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
          bookmarks.push({name: s.name || '', url: s.url || '', category: s.category || '', starred: s.starred || false});
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

fetch('/api/version').then(res => res.json()).then(v => { document.getElementById('appVersion').textContent = 'v.' + v.version; }).catch(() => {});

refresh();
</script>
</body>
</html>"""


@app.route("/")
def index():
    return Response(HTML, content_type="text/html")


@app.route("/api/bookmarks", methods=["GET", "POST"])
def api_bookmarks():
    if request.method == "POST":
        data = request.json
        save_data({
            "bookmarks": data.get("bookmarks", []),
            "cat_order": data.get("cat_order", []),
            "starred_order": data.get("starred_order", []),
            "categories": data.get("categories", []),
            "favicon_mode": int(data.get("favicon_mode", 0)),
            "item_order": data.get("item_order", {}),
        })
        return jsonify({"ok": True})

    return jsonify(load_data())


@app.route("/api/favicon-setting", methods=["POST"])
def api_favicon_setting():
    data = request.json
    current = load_data()
    current["favicon_mode"] = int(data.get("favicon_mode", 0))
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
    if fetch_and_cache_favicon(url):
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
    urls = data.get("urls", [])
    for url in urls:
        path = get_favicon_path(url)
        if not os.path.exists(path):
            fetch_and_cache_favicon(url)
    return jsonify({"ok": True})


def _delayed_restart(delay=1.0):
    def _run():
        time.sleep(delay)
        subprocess.run(["systemctl", "restart", SERVICE_NAME],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    threading.Thread(target=_run, daemon=True).start()


@app.route("/api/version")
def api_version():
    return jsonify({"version": APP_VERSION})


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

    _delayed_restart()
    return jsonify({"ok": True, "updated": True, "message": "アップデート完了。サービスを再起動します"})


if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "3356"))
    print(f"[INFO] selfmark: http://{host}:{port}", flush=True)
    app.run(host=host, port=port, debug=False, threaded=True)
