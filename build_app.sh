#!/bin/bash
# Assemble « Ollama Buddy.app » : un bundle macOS autonome.
#
#   ./build_app.sh              construit l'app dans le dossier du projet
#   ./build_app.sh --install    la construit puis la copie dans /Applications
#
# L'app embarque le serveur Python et le tableau de bord : elle n'a besoin de
# rien d'autre que macOS (le python3 du systeme lui suffit).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$ROOT/Ollama Buddy.app"
CONTENTS="$APP/Contents"

# ---------------------------------------------------------------------------
# Prerequis. Sans ce garde-fou, l'echec surviendrait plus loin sous la forme
# d'une trace Python ou d'une erreur de compilateur peu lisible.
# ---------------------------------------------------------------------------

if ! command -v swiftc > /dev/null 2>&1; then
    echo "swiftc est introuvable. Installe les outils Xcode :" >&2
    echo "    xcode-select --install" >&2
    exit 1
fi

if ! python3 -c "import PIL" > /dev/null 2>&1; then
    echo "Pillow est requis pour generer l'icone :" >&2
    echo "    pip3 install pillow" >&2
    exit 1
fi

echo "==> Nettoyage"
rm -rf "$APP"
mkdir -p "$CONTENTS/MacOS" "$CONTENTS/Resources/app"

echo "==> Info.plist"
cp "$ROOT/app/Info.plist" "$CONTENTS/Info.plist"

echo "==> Serveur et tableau de bord embarques"
cp "$ROOT/ollama_buddy.py" "$CONTENTS/Resources/app/"
cp -R "$ROOT/web" "$CONTENTS/Resources/app/web"

echo "==> Icone"
python3 "$ROOT/app/make_icon.py" "$CONTENTS/Resources/AppIcon.icns" > /dev/null

echo "==> Compilation de l'enveloppe native"
# Universel : une tranche par architecture, puis fusion. macOS 26 est la derniere
# version a supporter Intel, mais l'app doit encore y tourner. La cible 12.0 suit
# LSMinimumSystemVersion de l'Info.plist.
SLICES=()
for arch in arm64 x86_64; do
    slice="$CONTENTS/MacOS/.OllamaBuddy-$arch"
    swiftc -O -target "$arch-apple-macosx12.0" \
        -o "$slice" \
        "$ROOT/app/main.swift" \
        -framework Cocoa -framework WebKit
    SLICES+=("$slice")
done
lipo -create "${SLICES[@]}" -output "$CONTENTS/MacOS/OllamaBuddy"
rm -f "${SLICES[@]}"

echo "==> Signature ad-hoc"
codesign --force --sign - --timestamp=none "$APP" 2>/dev/null || \
    echo "    (signature ignoree — macOS peut demander une confirmation au premier lancement)"

echo "==> Verification"
codesign --verify --verbose=1 "$APP" 2>&1 | sed 's/^/    /' || true
du -sh "$APP" | sed 's/^/    taille : /'

if [[ "${1:-}" == "--install" ]]; then
    echo "==> Installation dans /Applications"
    rm -rf "/Applications/Ollama Buddy.app"
    cp -R "$APP" "/Applications/Ollama Buddy.app"
    echo "    installee."
fi

echo
echo "Pret : $APP"
echo "Glisse-la dans le Dock, ou lance :  open \"$APP\""
