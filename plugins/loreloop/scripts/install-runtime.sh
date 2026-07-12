#!/bin/sh
set -eu

REPOSITORY="zhangguiping-xydt/loreloop"
VERSION="${LORELOOP_VERSION:-latest}"
WITH_WEB=0
INITIALIZE=0

usage() {
  cat <<'EOF'
Install the LoreLoop Runtime from a checksummed GitHub Release wheel.

Usage: install-runtime.sh [--version vX.Y.Z] [--with-web] [--init]

  --version VERSION  Install one tagged release instead of latest.
  --with-web         Include Playwright's Python package (browser download is separate).
  --init             Initialize LoreLoop and install project companion skills in cwd.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --version)
      [ "$#" -ge 2 ] || { echo "error: --version requires a value" >&2; exit 2; }
      VERSION="$2"
      shift 2
      ;;
    --with-web)
      WITH_WEB=1
      shift
      ;;
    --init)
      INITIALIZE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unsupported argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [ -n "${LORELOOP_RELEASE_BASE_URL:-}" ]; then
  RELEASE_BASE="$LORELOOP_RELEASE_BASE_URL"
elif [ "$VERSION" = "latest" ]; then
  RELEASE_BASE="https://github.com/$REPOSITORY/releases/latest/download"
else
  RELEASE_BASE="https://github.com/$REPOSITORY/releases/download/$VERSION"
fi

TMP_DIR="$(mktemp -d 2>/dev/null || mktemp -d -t loreloop-install)"
trap 'rm -rf "$TMP_DIR"' EXIT HUP INT TERM
SUMS="$TMP_DIR/SHA256SUMS"

download() {
  url="$1"
  output="$2"
  if command -v curl >/dev/null 2>&1; then
    curl --fail --location --silent --show-error "$url" --output "$output"
  elif command -v wget >/dev/null 2>&1; then
    wget -q "$url" -O "$output"
  else
    echo "error: curl or wget is required to download LoreLoop" >&2
    exit 1
  fi
}

echo "Downloading LoreLoop Runtime from $RELEASE_BASE"
download "$RELEASE_BASE/SHA256SUMS" "$SUMS"

WHEEL_NAME="$(awk '$2 ~ /^loreloop-[A-Za-z0-9_.+!-]+-py3-none-any\.whl$/ { print $2; exit }' "$SUMS")"
if [ -z "$WHEEL_NAME" ]; then
  echo "error: SHA256SUMS does not contain a valid LoreLoop wheel filename" >&2
  exit 1
fi
WHEEL="$TMP_DIR/$WHEEL_NAME"
download "$RELEASE_BASE/$WHEEL_NAME" "$WHEEL"
EXPECTED="$(awk -v wheel="$WHEEL_NAME" '$2 == wheel { print $1; exit }' "$SUMS")"
if command -v sha256sum >/dev/null 2>&1; then
  ACTUAL="$(sha256sum "$WHEEL" | awk '{print $1}')"
elif command -v shasum >/dev/null 2>&1; then
  ACTUAL="$(shasum -a 256 "$WHEEL" | awk '{print $1}')"
else
  echo "error: sha256sum or shasum is required to verify the release" >&2
  exit 1
fi
if [ "$ACTUAL" != "$EXPECTED" ]; then
  echo "error: LoreLoop wheel checksum mismatch" >&2
  exit 1
fi
echo "Verified SHA-256: $ACTUAL"

SPEC="$WHEEL"
if [ "$WITH_WEB" -eq 1 ]; then
  SPEC="$WHEEL[web]"
fi

if command -v uv >/dev/null 2>&1; then
  uv tool install --force "$SPEC"
  RUNTIME="$(command -v loreloop 2>/dev/null || true)"
elif command -v pipx >/dev/null 2>&1; then
  pipx install --force "$SPEC"
  RUNTIME="$(command -v loreloop 2>/dev/null || true)"
else
  PYTHON=""
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(not ((3, 11) <= sys.version_info[:2] < (3, 15)))'; then
      PYTHON="$candidate"
      break
    fi
  done
  if [ -z "$PYTHON" ]; then
    echo "error: install uv, pipx, or Python 3.11-3.14, then retry" >&2
    exit 1
  fi
  VENV="${LORELOOP_INSTALL_ROOT:-$HOME/.local/share/loreloop}/venv"
  "$PYTHON" -m venv "$VENV"
  "$VENV/bin/python" -m pip install --force-reinstall "$SPEC"
  mkdir -p "$HOME/.local/bin"
  ln -sf "$VENV/bin/loreloop" "$HOME/.local/bin/loreloop"
  RUNTIME="$HOME/.local/bin/loreloop"
fi

if [ -z "$RUNTIME" ] || [ ! -x "$RUNTIME" ]; then
  if [ -x "$HOME/.local/bin/loreloop" ]; then
    RUNTIME="$HOME/.local/bin/loreloop"
  else
    echo "error: installation completed but loreloop is not discoverable on PATH" >&2
    exit 1
  fi
fi

"$RUNTIME" --help >/dev/null
echo "Installed LoreLoop Runtime: $RUNTIME"

if [ "$INITIALIZE" -eq 1 ]; then
  "$RUNTIME" init --skill
fi

echo "Next: restart Codex, then invoke \$loreloop in your project."
