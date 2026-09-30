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
swiftc -O \
    -o "$CONTENTS/MacOS/OllamaBuddy" \
    "$ROOT/app/main.swift" \
    -framework Cocoa -framework WebKit

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
