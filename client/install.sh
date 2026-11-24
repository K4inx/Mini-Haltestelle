#!/usr/bin/env bash
# Mini-Haltestelle — Client auf dem Pi einrichten (Raspberry Pi OS Lite 32-bit, Bookworm).
#   sudo /opt/mini-haltestelle/client/install.sh
# Idempotent: darf nach Updates erneut laufen.
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "Bitte mit sudo ausführen."; exit 1; }
CLIENT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$CLIENT_DIR")"
APP_USER=haltestelle
CONF_DIR=/etc/abfahrtsanzeige
VENV=/opt/mini-haltestelle-venv

echo "== SPI einschalten"
if command -v raspi-config >/dev/null; then
  raspi-config nonint do_spi 0
else
  echo "   raspi-config fehlt — SPI bitte selbst aktivieren (dtparam=spi=on)"
fi

echo "== Pakete aus apt (nichts muss auf dem Zero kompiliert werden)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git python3-venv python3-pil python3-numpy python3-spidev \
  python3-gpiozero python3-requests python3-yaml >/dev/null
# lgpio ist gpiozeros Standard-Backend auf aktuellem Raspberry Pi OS; fehlt es, nimmt gpiozero RPi.GPIO
apt-get install -y -qq python3-lgpio >/dev/null 2>&1 || echo "   python3-lgpio nicht verfügbar — gpiozero nutzt ein anderes Backend"

echo "== venv mit System-Paketen: $VENV"
[ -d "$VENV" ] || python3 -m venv --system-site-packages "$VENV"

echo "== Benutzer $APP_USER (Gruppen spi, gpio)"
id "$APP_USER" >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin "$APP_USER"
usermod -aG spi,gpio "$APP_USER"

echo "== Konfiguration $CONF_DIR/client.yaml"
install -d -m 755 "$CONF_DIR"
[ -f "$CONF_DIR/client.yaml" ] || install -m 644 "$CLIENT_DIR/client.example.yaml" "$CONF_DIR/client.yaml"

echo "== systemd-Dienst"
sed -e "s|@CLIENT_DIR@|$CLIENT_DIR|g" -e "s|@VENV@|$VENV|g" -e "s|@USER@|$APP_USER|g" \
  "$CLIENT_DIR/mini-haltestelle.service" > /etc/systemd/system/mini-haltestelle.service
systemctl daemon-reload
systemctl enable mini-haltestelle.service >/dev/null
systemctl restart mini-haltestelle.service

echo
echo "Fertig. Status:  systemctl status mini-haltestelle"
echo "Log:            journalctl -u mini-haltestelle -f"
echo "Config:         sudo nano $CONF_DIR/client.yaml   (danach: sudo systemctl restart mini-haltestelle)"
if ! grep -q "^dtparam=spi=on" /boot/firmware/config.txt 2>/dev/null; then
  echo "Hinweis: SPI wurde eben erst eingeschaltet — einmal neu starten: sudo reboot"
fi
git -C "$REPO_DIR" log -1 --format="Version: %h %s" 2>/dev/null || true
