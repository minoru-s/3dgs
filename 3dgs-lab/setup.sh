#!/usr/bin/env bash
# 3dgs-lab セットアップスクリプト（冪等）
# macOS / Apple Silicon 前提。実行するたびに不足分だけ導入する。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${SCRIPT_DIR}/logs"
TOOLS_DIR="${SCRIPT_DIR}/.tools"
mkdir -p "${LOG_DIR}" "${TOOLS_DIR}" "${SCRIPT_DIR}/input" "${SCRIPT_DIR}/work" "${SCRIPT_DIR}/output"

BRUSH_VERSION="v0.3.0"
BRUSH_ASSET="brush-app-aarch64-apple-darwin.tar.xz"
BRUSH_URL="https://github.com/ArthurBrussee/brush/releases/download/${BRUSH_VERSION}/${BRUSH_ASSET}"
BRUSH_BIN="${TOOLS_DIR}/brush-app-aarch64-apple-darwin/brush_app"

log() { echo "[setup] $*"; }

# --- 0. アーキテクチャチェック ---
ARCH="$(uname -m)"
if [[ "${ARCH}" != "arm64" ]]; then
  echo "[setup] ERROR: Apple Silicon (arm64) が必須です。検出されたアーキテクチャ: ${ARCH}" >&2
  echo "[setup] Intel Mac の場合は README の『代替構成』を参照してください。" >&2
  exit 1
fi

# --- 1. Homebrew ---
if ! command -v brew >/dev/null 2>&1; then
  echo "[setup] ERROR: Homebrew が見つかりません。https://brew.sh の手順に従って導入してください。" >&2
  exit 1
fi

# --- 2. Xcode Command Line Tools ---
if ! xcode-select -p >/dev/null 2>&1; then
  log "Xcode Command Line Tools が未導入です。以下を実行してください:"
  echo "    xcode-select --install"
  exit 1
fi

# --- 3. ffmpeg / colmap (Homebrew) ---
for pkg in ffmpeg colmap; do
  if brew list --versions "${pkg}" >/dev/null 2>&1; then
    log "${pkg}: 導入済み ($(brew list --versions "${pkg}"))"
  else
    log "${pkg} を導入します..."
    brew install "${pkg}"
  fi
done

# --- 4. Brush (macOS arm64 リリースバイナリ) ---
if [[ -x "${BRUSH_BIN}" ]]; then
  log "brush: 導入済み (${BRUSH_BIN})"
else
  log "Brush ${BRUSH_VERSION} をダウンロードします..."
  curl -fL --progress-bar -o "${TOOLS_DIR}/brush-app.tar.xz" "${BRUSH_URL}"
  tar xf "${TOOLS_DIR}/brush-app.tar.xz" -C "${TOOLS_DIR}"
  rm -f "${TOOLS_DIR}/brush-app.tar.xz"
  chmod +x "${BRUSH_BIN}"
  # Gatekeeper: リリースバイナリは未署名/未公証のため quarantine 属性を外す
  xattr -d com.apple.quarantine "${BRUSH_BIN}" 2>/dev/null || true
fi

# --- 5. Python venv ---
VENV_DIR="${SCRIPT_DIR}/.venv"
if [[ ! -d "${VENV_DIR}" ]]; then
  log "Python venv を作成します (${VENV_DIR})..."
  python3 -m venv "${VENV_DIR}"
fi
# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"
pip install --quiet --upgrade pip
pip install --quiet pillow

# --- 6. 動作確認 ---
log "動作確認中..."
ffmpeg -version | head -n 1
colmap -h >/dev/null 2>&1 && echo "colmap: OK"
"${BRUSH_BIN}" --version

log "セットアップ完了。次のコマンドで実行できます:"
echo "    source .venv/bin/activate"
echo "    python3 splat.py input/<動画 or 画像フォルダ>"
