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
    curl -fsSL "$RELEASE_URL" | tar -xz -C "$TEMP_DIR"
    if [ -f "${TEMP_DIR}/dosh" ]; then
      mv "${TEMP_DIR}/dosh" "$TARGET_FILE"
      chmod +x "$TARGET_FILE"
      INSTALLED=true
    fi
    rm -rf "$TEMP_DIR"
  fi
fi

# Fallback: If precompiled tarball not on GitHub Releases yet, build or copy from local repo/cargo
if [ "$INSTALLED" = false ]; then
  if [ -f "./target/release/dosh" ]; then
    echo "==> Found locally compiled release binary. Installing to ${TARGET_FILE}..."
    cp "./target/release/dosh" "$TARGET_FILE"
    chmod +x "$TARGET_FILE"
    INSTALLED=true
  elif command -v cargo >/dev/null 2>&1; then
    echo "==> No precompiled binary found on GitHub Releases yet."
    echo "==> Building standalone dosh binary from source via Cargo (this takes ~1-2 min)..."
    cargo install --git "https://github.com/${REPO}.git" dosh-browser-core --bin dosh --root "${HOME}/.dosh"
    INSTALLED=true
  else
    echo "Error: Could not download precompiled release for ${TARGET} and cargo is not available."
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
  if command -v uv >/dev/null 2>&1; then
    uv pip install dosh-browser 2>/dev/null || uv pip install -e . 2>/dev/null || true
  elif command -v pip3 >/dev/null 2>&1; then
    pip3 install dosh-browser 2>/dev/null || pip3 install -e . 2>/dev/null || true
  elif command -v pip >/dev/null 2>&1; then
    pip install dosh-browser 2>/dev/null || pip install -e . 2>/dev/null || true
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

