#!/usr/bin/env bash
# ==============================================================================
# DOSH Browser — Official One-Line Installer
#
# Usage:
#   # Install official standalone pure-Rust browser (dosh):
#   curl -fsSL https://raw.githubusercontent.com/asish231/dosh-browser/main/install.sh | bash
#
#   # Install standalone browser AND optional Python companion SDK:
#   curl -fsSL https://raw.githubusercontent.com/asish231/dosh-browser/main/install.sh | bash -s -- -p
# ==============================================================================
set -euo pipefail

REPO="asish231/dosh-browser"
INSTALL_DIR="${HOME}/.dosh/bin"
BIN_NAME="dosh"
TARGET_FILE="${INSTALL_DIR}/${BIN_NAME}"

INSTALL_PYTHON=false

for arg in "$@"; do
  case "$arg" in
    -p|--python|--py)
      INSTALL_PYTHON=true
      ;;
    -d|--default|--rust)
      INSTALL_PYTHON=false
      ;;
    -h|--help)
      echo "DOSH Browser Installer"
      echo ""
      echo "Usage:"
      echo "  install.sh [options]"
      echo ""
      echo "Options:"
      echo "  -d, --default, --rust   Install official standalone pure-Rust binary (default)"
      echo "  -p, --python, --py      Also install the optional Python companion SDK (webresearch)"
      echo "  -h, --help              Show this help message"
      exit 0
      ;;
    *)
      echo "Unknown option: $arg"
      echo "Run with -h for help."
      exit 1
      ;;
  esac
done

echo "┌────────────────────────────────────────────────────────┐"
echo "│  DOSH Browser — The Autonomous Browser for AI Agents   │"
echo "└────────────────────────────────────────────────────────┘"
echo ""

# 1. Detect OS and Architecture
OS="$(uname -s)"
ARCH="$(uname -m)"

case "$OS" in
  Darwin)
    case "$ARCH" in
      arm64|aarch64) TARGET="aarch64-apple-darwin" ;;
      x86_64)        TARGET="x86_64-apple-darwin" ;;
      *) echo "Error: Unsupported macOS architecture: $ARCH" && exit 1 ;;
    esac
    ;;
  Linux)
    case "$ARCH" in
      x86_64)        TARGET="x86_64-unknown-linux-gnu" ;;
      aarch64|arm64) TARGET="aarch64-unknown-linux-gnu" ;;
      *) echo "Error: Unsupported Linux architecture: $ARCH" && exit 1 ;;
    esac
    ;;
  *)
    echo "Error: Unsupported operating system: $OS"
    exit 1
    ;;
esac

echo "==> Platform detected: ${OS} (${ARCH}) -> ${TARGET}"
mkdir -p "$INSTALL_DIR"

# 2. Fetch Latest Release Asset from GitHub
LATEST_TAG=""
if command -v curl >/dev/null 2>&1; then
  LATEST_TAG=$(curl -fsSL -H "Accept: application/vnd.github.v3+json" "https://api.github.com/repos/${REPO}/releases/latest" 2>/dev/null | grep '"tag_name":' | sed -E 's/.*"([^"]+)".*/\1/' || echo "")
fi

INSTALLED=false

if [ -n "$LATEST_TAG" ]; then
  RELEASE_URL="https://github.com/${REPO}/releases/download/${LATEST_TAG}/dosh-${TARGET}.tar.gz"
  echo "==> Fetching DOSH ${LATEST_TAG} release binary..."
  if curl -fsSL -I "$RELEASE_URL" >/dev/null 2>&1; then
    TEMP_DIR=$(mktemp -d)
    ASSET="dosh-${TARGET}.tar.gz"
    if curl -fsSL "$RELEASE_URL" -o "${TEMP_DIR}/${ASSET}"; then
      if curl -fsSL "${RELEASE_URL}.sha256" -o "${TEMP_DIR}/${ASSET}.sha256" 2>/dev/null; then
        if (cd "$TEMP_DIR" && { shasum -a 256 -c "${ASSET}.sha256" >/dev/null 2>&1 || sha256sum -c "${ASSET}.sha256" >/dev/null 2>&1; }); then
          echo "==> SHA-256 checksum verified."
        else
          echo "Error: SHA-256 checksum verification FAILED for ${ASSET}. Refusing to install." >&2
          rm -rf "$TEMP_DIR"
          exit 1
        fi
      else
        echo "==> Warning: no .sha256 asset published for ${LATEST_TAG}; skipping verification."
      fi
      tar -xzf "${TEMP_DIR}/${ASSET}" -C "$TEMP_DIR"
      if [ -f "${TEMP_DIR}/dosh" ]; then
        mv "${TEMP_DIR}/dosh" "$TARGET_FILE"
        chmod +x "$TARGET_FILE"
        INSTALLED=true
      fi
    fi
    rm -rf "$TEMP_DIR"
  fi
fi

# Fallback: If no precompiled tarball exists for this target, build from source or copy locally
if [ "$INSTALLED" = false ]; then
  if [ -f "./target/release/dosh" ]; then
    echo "==> Found locally compiled release binary. Installing to ${TARGET_FILE}..."
    cp "./target/release/dosh" "$TARGET_FILE"
    chmod +x "$TARGET_FILE"
    INSTALLED=true
  elif command -v cargo >/dev/null 2>&1 && command -v git >/dev/null 2>&1; then
    echo "==> No precompiled binary available for ${TARGET}${LATEST_TAG:+ in ${LATEST_TAG}}."
    echo "==> Building dosh from source (git clone + cargo build; can take a few minutes)..."
    SRC_DIR="${HOME}/.dosh/src/dosh-browser"
    if [ -d "${SRC_DIR}/.git" ]; then
      git -C "$SRC_DIR" pull --ff-only 2>/dev/null || true
    else
      mkdir -p "$(dirname "$SRC_DIR")"
      git clone --depth 1 "https://github.com/${REPO}.git" "$SRC_DIR"
    fi
    (cd "$SRC_DIR" && cargo build --release --bin dosh)
    if [ -f "${SRC_DIR}/target/release/dosh" ]; then
      cp "${SRC_DIR}/target/release/dosh" "$TARGET_FILE"
      chmod +x "$TARGET_FILE"
      INSTALLED=true
    fi
  else
    echo "Error: Could not download precompiled release for ${TARGET} and cargo+git are not both available."
    echo "Please visit https://github.com/${REPO}/releases to download manually."
    exit 1
  fi
fi

# 3. Configure PATH
PROFILE_MODIFIED=false
SHELL_PROFILE=""

if [[ ":${PATH}:" != *":${INSTALL_DIR}:"* ]]; then
  if [ -n "${ZSH_VERSION:-}" ] || [ "${SHELL:-}" = "/bin/zsh" ] || [ "${SHELL:-}" = "/usr/bin/zsh" ]; then
    SHELL_PROFILE="${HOME}/.zshrc"
  elif [ -n "${BASH_VERSION:-}" ] || [ "${SHELL:-}" = "/bin/bash" ] || [ "${SHELL:-}" = "/usr/bin/bash" ]; then
    SHELL_PROFILE="${HOME}/.bashrc"
  else
    SHELL_PROFILE="${HOME}/.profile"
  fi

  if [ -n "$SHELL_PROFILE" ] && [ -f "$SHELL_PROFILE" ]; then
    if ! grep -q '\.dosh/bin' "$SHELL_PROFILE"; then
      echo "" >> "$SHELL_PROFILE"
      echo '# DOSH Browser' >> "$SHELL_PROFILE"
      echo 'export PATH="$HOME/.dosh/bin:$PATH"' >> "$SHELL_PROFILE"
      PROFILE_MODIFIED=true
    fi
  fi
fi

# 4. Optional: Python Companion SDK
if [ "$INSTALL_PYTHON" = true ]; then
  echo ""
  echo "==> Installing optional Python companion SDK (webresearch)..."
  SDK_SPEC="webresearch @ git+https://github.com/${REPO}.git"
  if command -v uv >/dev/null 2>&1; then
    uv pip install "$SDK_SPEC" 2>/dev/null || uv pip install -e . 2>/dev/null || true
  elif command -v pip3 >/dev/null 2>&1; then
    pip3 install "$SDK_SPEC" 2>/dev/null || pip3 install -e . 2>/dev/null || true
  elif command -v pip >/dev/null 2>&1; then
    pip install "$SDK_SPEC" 2>/dev/null || pip install -e . 2>/dev/null || true
  fi
  if command -v python3 >/dev/null 2>&1 && python3 -c "import webresearch" >/dev/null 2>&1; then
    echo "==> webresearch SDK import OK."
  else
    echo "==> Could not verify the webresearch import (pip may need a virtual environment)."
    echo "    Install manually with:"
    echo "      pip install \"$SDK_SPEC\""
  fi
fi

echo ""
echo "════════════════════════════════════════════════════════════"
echo "  DOSH Browser installed successfully to:"
echo "    ${TARGET_FILE}"
echo ""
if [ "$PROFILE_MODIFIED" = true ]; then
  echo "  To start using dosh now, reload your shell:"
  echo "    source ${SHELL_PROFILE}"
  echo ""
fi
echo "  Quick start:"
echo "    dosh read https://news.ycombinator.com"
echo "    dosh shell"
echo "    dosh mcp"
echo ""
echo "  Lanes:"
echo "    dosh <cmd>     -> Official standalone pure-Rust browser (default)"
echo "    dosh -p <cmd>  -> Optional Python companion SDK (webresearch)"
echo "════════════════════════════════════════════════════════════"

