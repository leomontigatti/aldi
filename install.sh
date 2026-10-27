#!/bin/sh
# Installs Aldi on macOS:
#   curl -LsSf https://raw.githubusercontent.com/leomontigatti/aldi/main/install.sh | sh
# It installs uv, then Aldi as a uv tool from the latest Release tag with uv.lock's pins, then `aldi setup` saves
# the reporting token and creates Aldi.app in Applications. A step that fails files an Error Report (ADR 4) that
# names only the step and the system. Running it again reinstalls the latest Release.
#
# ALDI_REPORT_TOKEN skips the question for the token; ALDI_SOURCE, a checkout, is installed instead of the
# latest Release (CI's test of this script).
set -eu

REPO=leomontigatti/aldi
UV_VERSION=0.13.0
token=${ALDI_REPORT_TOKEN:-}
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

report() {
    [ -n "$token" ] || return 1
    system="macOS $(sw_vers -productVersion 2>/dev/null || uname -sr), $(uname -m)"
    body="Filed by the install line on its own (ADR 4).\n\n- Problem: Install failed: $1\n- System: $system\n\nFingerprint: \`install-$1\`\n"
    printf '{"title":"Error Report: Install failed: %s","body":"%s","labels":["bug","needs-triage","source:app"]}' \
        "$1" "$body" >"$tmp/report.json"
    curl -sSf -o /dev/null -X POST "https://api.github.com/repos/$REPO/issues" \
        -H "Authorization: Bearer $token" -H "Accept: application/vnd.github+json" -H "User-Agent: aldi-install" \
        --data-binary "@$tmp/report.json" 2>/dev/null
}

fail() {
    printf 'No se pudo instalar Aldi: %s.\n' "$2" >&2
    if report "$1"; then
        echo "Ya se envió un informe para que lo arreglen." >&2
    else
        echo "No se pudo enviar el informe del error: avísale a quien te instaló Aldi." >&2
    fi
    exit 1
}

while [ -z "$token" ]; do
    printf 'Token para los informes de errores: ' >/dev/tty
    stty -echo </dev/tty
    IFS= read -r token </dev/tty || true
    stty echo </dev/tty
    printf '\n' >/dev/tty
done

uv=$(command -v uv || true)
if [ -z "$uv" ]; then
    echo "Instalando uv…"
    curl -LsSf -o "$tmp/uv-install.sh" "https://astral.sh/uv/$UV_VERSION/install.sh" ||
        fail uv "no se pudo descargar uv"
    UV_INSTALL_DIR="$HOME/.local/bin" sh "$tmp/uv-install.sh" || fail uv "no se pudo instalar uv"
    uv="$HOME/.local/bin/uv"
    [ -x "$uv" ] || fail uv "no se pudo instalar uv"
fi

if [ -n "${ALDI_SOURCE:-}" ]; then
    spec=$ALDI_SOURCE
    cp "$ALDI_SOURCE/install-constraints.txt" "$tmp/constraints.txt"
else
    tag=$(curl -sSfL "https://api.github.com/repos/$REPO/releases/latest" |
        sed -n 's/^ *"tag_name": *"\([^"]*\)".*/\1/p') || true
    [ -n "$tag" ] || fail release "no se encontró la última versión"
    curl -sSfL -o "$tmp/constraints.txt" "https://raw.githubusercontent.com/$REPO/$tag/install-constraints.txt" ||
        fail release "no se pudo descargar la versión $tag"
    spec="aldi @ https://github.com/$REPO/archive/refs/tags/$tag.tar.gz"
fi

echo "Instalando Aldi…"
"$uv" tool install --force --managed-python --python 3.12 --constraints "$tmp/constraints.txt" "$spec" ||
    fail tool-install "uv no pudo instalarlo"

# `aldi setup` files its own Error Report when it fails.
ALDI_REPORT_TOKEN=$token "$("$uv" tool dir --bin)/aldi" setup || exit 1
