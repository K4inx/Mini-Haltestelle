#!/usr/bin/env bash
# Client aktualisieren:  sudo /opt/mini-haltestelle/client/update.sh
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Bitte mit sudo ausführen."; exit 1; }
CLIENT_DIR="$(cd "$(dirname "$0")" && pwd)"
if grep -q "overlayroot\|boot=overlay" /proc/cmdline 2>/dev/null; then
  echo "Overlay-Dateisystem ist aktiv — Änderungen wären nach dem Neustart weg."
  echo "Erst ausschalten: sudo raspi-config nonint disable_overlayfs && sudo reboot"
  exit 1
fi
git -C "$CLIENT_DIR/.." pull --ff-only
systemctl restart mini-haltestelle
git -C "$CLIENT_DIR/.." log -1 --format="Jetzt: %h %s"
