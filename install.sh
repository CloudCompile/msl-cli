#!/usr/bin/env bash
# Install msl: dedicated venv + ~/.local/bin symlinks (msl and mystudylife).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${MSL_VENV:-$HOME/.venv-msl}"
BIN="${MSL_BIN:-$HOME/.local/bin}"

python3 -m venv "$VENV"
"$VENV/bin/pip" -q install --upgrade pip
"$VENV/bin/pip" -q install -e "$HERE"

mkdir -p "$BIN"
ln -sf "$VENV/bin/msl" "$BIN/msl"
ln -sf "$VENV/bin/msl" "$BIN/mystudylife"

mkdir -p "$HOME/.config/msl"
if [ ! -e "$HOME/.config/msl/config" ]; then
    umask 077
    cat > "$HOME/.config/msl/config" <<'EOF'
# msl credentials — chmod 600 this file
# email=
# password=
# provider=email
EOF
    chmod 600 "$HOME/.config/msl/config"
    echo "wrote template $HOME/.config/msl/config"
fi

echo "installed: $BIN/msl, $BIN/mystudylife  ->  $VENV"
"$BIN/msl" --help >/dev/null && echo "ok"
