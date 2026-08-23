#!/usr/bin/env bash
# =============================================================================
#  install-selfmark1.sh — selfmark（シンプルなブックマーク管理）インストールスクリプト
#  - GitHub (https://github.com/hirogura/selfmark) からアプリ本体 (app.py) を取得
#  - ポート 3356 で公開（tailscale 接続環境では Tailscale Serve により HTTPS 化）
#  - app.run() を threaded=True にして並列リクエスト処理に対応
#  - 実行例: sudo bash install-selfmark1.sh
# =============================================================================
set -euo pipefail

# === 設定値 ===
GITHUB_REPO="https://github.com/hirogura/selfmark"
GITHUB_RAW="https://raw.githubusercontent.com/hirogura/selfmark/main"
INSTALL_DIR="/opt/lxd-data/selfmark"
SERVICE_NAME="selfmark"
SELFMARK_PORT=3356
VENV_DIR="${INSTALL_DIR}/venv"

# ── Tailscale 検出（あれば Tailscale Serve で HTTPS 化する）──
TS_HTTPS=0
if command -v tailscale >/dev/null 2>&1 && tailscale status >/dev/null 2>&1; then
  TS_HTTPS=1
fi

info()  { echo -e "\033[1;34m[INFO]\033[0m  $*"; }
ok()    { echo -e "\033[1;32m[ OK ]\033[0m  $*"; }
warn()  { echo -e "\033[1;33m[WARN]\033[0m  $*"; }
die()   { echo -e "\033[1;31m[ERR ]\033[0m  $*" >&2; exit 1; }

info "前提確認..."
command -v python3 >/dev/null 2>&1 || die "python3 が見つかりません"
command -v curl >/dev/null 2>&1 || die "curl が見つかりません"

info "ディレクトリ作成..."
mkdir -p "${INSTALL_DIR}"

info "GitHub からアプリ本体 (app.py) をダウンロード..."
if curl -fsSL --connect-timeout 10 --max-time 60 "${GITHUB_RAW}/app.py" -o "${INSTALL_DIR}/app.py"; then
  ok "アプリ本体のダウンロード完了 (${GITHUB_REPO})"
else
  die "GitHub からのダウンロードに失敗しました: ${GITHUB_RAW}/app.py"
fi

NEED_VENV_SETUP=false
if [[ ! -f "${VENV_DIR}/bin/python" ]]; then
  NEED_VENV_SETUP=true
elif ! "${VENV_DIR}/bin/python" -c "import flask" >/dev/null 2>&1; then
  warn "既存の venv に flask が見つかりません。再セットアップします..."
  NEED_VENV_SETUP=true
fi

if [[ "${NEED_VENV_SETUP}" == "true" ]]; then
  if [[ ! -f "${VENV_DIR}/bin/python" ]]; then
    warn "venv が見つかりません。新規作成します..."
    mkdir -p "$(dirname "${VENV_DIR}")"
    TMPDIR_VENV=$(mktemp -d)
    VENV_OK=false
    if python3 -m venv "${TMPDIR_VENV}/test" 2>/dev/null && "${TMPDIR_VENV}/test/bin/python" -c "import sys; print(sys.version)" >/dev/null 2>&1; then
      VENV_OK=true
    fi
    rm -rf "${TMPDIR_VENV}"
    if [[ "${VENV_OK}" == "false" ]]; then
      warn "python3-venv が見つかりません。インストールします..."
      PYVER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null)
      apt-get update -qq 2>/dev/null
      apt-get install -y -qq "python3.${PYVER}-venv" 2>/dev/null || \
      apt-get install -y -qq python3-venv 2>/dev/null || \
      die "python3-venv のインストールに失敗しました"
    fi
    python3 -m venv "${VENV_DIR}" || die "venv の作成に失敗しました"
  fi
  "${VENV_DIR}/bin/pip" install --quiet --upgrade flask Pillow
fi

if ! "${VENV_DIR}/bin/python" -c "import sys; print(sys.version)" >/dev/null 2>&1; then
  die "venv が正しく作成されませんでした"
fi
if ! "${VENV_DIR}/bin/python" -c "import flask" >/dev/null 2>&1; then
  die "venv に flask が正しくインストールされませんでした"
fi
ok "Python 環境 OK ($("${VENV_DIR}/bin/python" -c "import sys; print(sys.version)"))"

info "アプリ本体の構文チェック..."
if "${VENV_DIR}/bin/python" -m py_compile "${INSTALL_DIR}/app.py" 2>/dev/null; then
  ok "構文チェック OK"
  rm -rf "${INSTALL_DIR}/__pycache__"
else
  die "アプリ本体 (app.py) の構文エラーが検出されました"
fi

info "依存パッケージを確認..."
if [[ -f "${VENV_DIR}/bin/pip" ]]; then
  "${VENV_DIR}/bin/pip" install --quiet --upgrade flask Pillow 2>/dev/null
else
  die "venv の pip が見つかりません"
fi
ok "依存パッケージ OK"

info "systemd ユニットファイルを生成..."
# Tailscale Serve が TLS 終端用に <tailscale IP>:PORT をバインドできるよう、
# tailscale 接続環境ではアプリを 127.0.0.1 のみで待機させる
HOST_ENV=""
if [[ "${TS_HTTPS}" == "1" ]]; then
  HOST_ENV="Environment=HOST=127.0.0.1"
fi
cat > /etc/systemd/system/${SERVICE_NAME}.service << EOF
[Unit]
Description=selfmark Bookmark Manager
After=network.target

[Service]
Type=simple
ExecStart=${VENV_DIR}/bin/python ${INSTALL_DIR}/app.py
WorkingDirectory=${INSTALL_DIR}
Restart=on-failure
RestartSec=3
Environment=PORT=${SELFMARK_PORT}
${HOST_ENV}

[Install]
WantedBy=multi-user.target
EOF
ok "systemd ユニットファイル生成完了"

# ポート使用中のプロセスを停止（tailscaled は Tailscale Serve の TLS 終端で使用中のため除外）
PIDS=$(ss -tlnp 2>/dev/null | grep ":${SELFMARK_PORT} " | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u || true)
for pid in ${PIDS}; do
  CMDNAME=$(ps -o comm= -p "${pid}" 2>/dev/null || true)
  if [[ -n "${CMDNAME}" && "${CMDNAME}" != "tailscaled" ]]; then
    warn "ポート ${SELFMARK_PORT} を使用中 (${CMDNAME}, PID ${pid})。停止します..."
    kill "${pid}" 2>/dev/null || true
  fi
done

info "サービスを起動..."
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}" 2>/dev/null
systemctl restart "${SERVICE_NAME}"

STARTED=false
for i in $(seq 1 10); do
  sleep 1
  if curl -s --max-time 2 "http://127.0.0.1:${SELFMARK_PORT}/" >/dev/null 2>&1; then
    STARTED=true
    break
  fi
done

if [[ "${STARTED}" == "true" ]]; then
  ok "サービス起動完了"
else
  warn "サービスの起動を確認できません。ログを確認します..."
  journalctl -u ${SERVICE_NAME} --no-pager -n 10 2>/dev/null || true
  sleep 3
  if curl -s --max-time 2 "http://127.0.0.1:${SELFMARK_PORT}/" >/dev/null 2>&1; then
    ok "サービス起動完了（遅延あり）"
  else
    die "サービスの起動に失敗しました"
  fi
fi

info "Tailscale 情報を取得..."
TS_IP=""
TS_HOSTNAME=""
if command -v tailscale >/dev/null 2>&1; then
  TS_IP=$(tailscale ip -4 2>/dev/null || true)
  TS_HOSTNAME=$(tailscale status --json 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin).get('Self', {}).get('DNSName', '').rstrip('.'))" 2>/dev/null || true)
fi
TS_DOMAIN="${TS_HOSTNAME}"

# ── Tailscale Serve で HTTPS 公開（冪等）──
if [[ "${TS_HTTPS}" == "1" && -n "${TS_DOMAIN}" ]]; then
  info "Tailscale Serve (HTTPS) を設定..."
  tailscale serve --https=${SELFMARK_PORT} off >/dev/null 2>&1 || true
  if tailscale serve --bg --https=${SELFMARK_PORT} "http://127.0.0.1:${SELFMARK_PORT}" >/dev/null; then
    ok "Tailscale Serve 設定完了"
    info "HTTPS 応答を待機中（最大60秒・初回は証明書発行に時間がかかります）..."
    for i in $(seq 1 12); do
      HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "https://${TS_DOMAIN}:${SELFMARK_PORT}/" 2>/dev/null || echo "000")
      if [[ "${HTTP_CODE}" != "000" ]]; then ok "HTTPS 応答 OK (HTTP ${HTTP_CODE})"; break; fi
      sleep 5
    done
  else
    warn "tailscale serve の設定に失敗しました（HTTP のみで動作します）"
  fi
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
ok "セットアップ完了！"
echo ""
if [[ "${TS_HTTPS}" == "1" && -n "${TS_DOMAIN}" ]]; then
  echo "  Web UI : https://${TS_DOMAIN}:${SELFMARK_PORT}  (Tailscale Serve / HTTPS)"
elif [[ -n "${TS_IP}" ]]; then
  echo "  Web UI : http://${TS_IP}:${SELFMARK_PORT}"
fi
if [[ "${TS_HTTPS}" != "1" && -n "${TS_HOSTNAME}" ]]; then
  echo "  Web UI : http://${TS_HOSTNAME%%.*}:${SELFMARK_PORT}  (MagicDNS)"
fi
if [[ -z "${TS_IP}" && -z "${TS_HOSTNAME}" ]]; then
  warn "Tailscale IP/Hostname を取得できませんでした。"
  echo "  Web UI : http://127.0.0.1:${SELFMARK_PORT}  (コンテナ内ローカル確認用)"
fi
echo ""
echo "  インストール先: ${INSTALL_DIR}"
echo "  データファイル : ${INSTALL_DIR}/bookmarks.json"
echo "  GitHub        : ${GITHUB_REPO}"
echo ""
echo "  管理コマンド:"
echo "    systemctl status ${SERVICE_NAME}"
echo "    systemctl restart ${SERVICE_NAME}"
echo ""
echo "  アンインストール:"
echo "    systemctl disable --now ${SERVICE_NAME} && rm /etc/systemd/system/${SERVICE_NAME}.service"
echo "    systemctl daemon-reload && rm -rf ${INSTALL_DIR}"
echo "    ※ ${INSTALL_DIR} にはブックマークデータ (bookmarks.json) も含まれます。"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
