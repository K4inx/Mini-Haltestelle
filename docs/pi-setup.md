# Pi einrichten: von der leeren microSD bis zum laufenden Gerät

Hardware: Raspberry Pi Zero W (v1) + Pimoroni Display HAT Mini, microSD ab 8 GB,
Micro-USB-Netzteil (5 V, ≥ 1,2 A). Der Server muss schon laufen (siehe README).

## 1. Löten

Der Zero W hat keine Stiftleiste. Das Display HAT Mini hat die Buchsenleiste schon drauf.

- **2×20-Stiftleiste** (2,54 mm) von **oben** in den Zero stecken. Die kurzen Enden ragen
  unten heraus und werden **unten** gelötet.
- Tipp: Zum Ausrichten das HAT aufstecken, dann zuerst zwei Ecken-Pins löten und prüfen,
  ob die Leiste gerade sitzt. Danach den Rest löten.
- Lötstellen sollen glänzend und kegelförmig sein, ohne Brücken zwischen Nachbarpins.
- Ohne Löten geht es mit einem **GPIO-Hammer-Header**, der eingepresst wird.
- Das HAT kommt **über** den Zero, Display nach außen. Die beiliegenden 10-mm-Abstandshalter
  verschrauben beide Platinen.

## 2. microSD beschreiben (Raspberry Pi Imager)

Formatieren ist **nicht** nötig. Der Imager überschreibt die Karte komplett.

1. [Raspberry Pi Imager](https://www.raspberrypi.com/software/) auf dem PC starten.
2. **Gerät:** Raspberry Pi Zero.
3. **Betriebssystem:** *Raspberry Pi OS (other)* → **Raspberry Pi OS (Legacy, 32-bit) Lite**
   (Bookworm). Diese Ausgabe nennt den Zero W ausdrücklich als unterstützt. Die neuere
   Trixie-Ausgabe *könnte* auch gehen, ist für den Zero v1 aber nicht bestätigt.
4. **Speicher:** die microSD.
5. **Einstellungen bearbeiten** (OS-Anpassung):
   - Hostname: `mini-haltestelle`
   - Benutzername und Passwort festlegen
   - **WLAN:** SSID und Passwort, Land `DE`. Der Zero W kann **nur 2,4 GHz**. Das 2,4-GHz-Band
     der Fritzbox muss an sein.
   - Zeitzone `Europe/Berlin`, Tastatur `de`
   - Reiter *Dienste*: **SSH aktivieren**, *nur Authentifizierung per Public Key*. Den
     eigenen öffentlichen Schlüssel einfügen. Weitere Schlüssel, etwa von einem
     Admin-Rechner, als zusätzliche Zeilen.
6. Schreiben, Karte in den Zero, Strom an. Der erste Start dauert auf dem Zero **2–5 Minuten**.

In der Fritzbox (*Heimnetz → Netzwerk*) den Pi suchen, „Diesem Gerät immer die gleiche
IPv4-Adresse zuweisen" anhaken.

## 3. Installieren

```bash
ssh <benutzer>@mini-haltestelle.local      # oder die IP aus der Fritzbox
sudo apt-get update && sudo apt-get install -y git
sudo git clone https://github.com/K4inx/Mini-Haltestelle.git /opt/mini-haltestelle
sudo /opt/mini-haltestelle/client/install.sh http://<server-ip>:8080
sudo reboot                                 # nur beim ersten Mal (SPI)
```

`install.sh` macht Folgendes:

- schaltet SPI ein
- installiert alle Pakete per **apt**: `python3-pil`, `-numpy`, `-spidev`, `-gpiozero`,
  `-lgpio`, `-requests`, `-yaml`. Auf dem Zero wird nichts kompiliert.
- legt ein venv mit `--system-site-packages` und den Benutzer `haltestelle` an
- legt die Config `/etc/abfahrtsanzeige/client.yaml` an
- richtet den systemd-Dienst `mini-haltestelle` mit `Restart=always` ein

Config anpassen, falls nötig (Server-URL, Geräte-ID, Theme):

```bash
sudo nano /etc/abfahrtsanzeige/client.yaml
sudo systemctl restart mini-haltestelle
```

Nach dem Start erscheint erst „verbinde …", nach wenigen Sekunden die Abfahrten.

## 4. Prüfen

| Was | Erwartung |
|---|---|
| `systemctl status mini-haltestelle` | `active (running)` |
| `journalctl -u mini-haltestelle -f` | keine Fehler, keine Tracebacks |
| Y 3 s halten | Info-Seite mit IP, WLAN-Stärke, Server-Status |
| A / B | Vorlage wechselt (auch im Web-UI sichtbar) |
| Server stoppen | nach einem Abruf „offline seit X min", nach 10 min Fehler-Screen |

## 5. SD-Karte schonen (Overlay-Dateisystem)

Erst einschalten, wenn alles läuft. Danach landen Schreibzugriffe nur im RAM, und
Stromausfälle machen der Karte nichts mehr aus.

```bash
sudo raspi-config nonint enable_overlayfs
sudo raspi-config nonint enable_bootro
sudo reboot
```

Der Client schreibt selbst nichts auf die Karte. Logs gehen ins Journal und sind mit Overlay
nach einem Neustart weg.

## 6. Updates

```bash
sudo /opt/mini-haltestelle/client/update.sh     # git pull + Dienst neu starten
```

Mit aktivem Overlay bricht das Skript ab, weil die Änderungen sonst nach dem Neustart
verloren wären. Dann so vorgehen:

```bash
sudo raspi-config nonint disable_overlayfs && sudo reboot
sudo /opt/mini-haltestelle/client/update.sh
sudo raspi-config nonint enable_overlayfs && sudo reboot
```

Das meiste ändert sich aber auf dem **Server**. Profile, Filter und Layout-Daten brauchen
kein Update am Pi.

## Fehlersuche

| Symptom | Ursache / Abhilfe |
|---|---|
| Display bleibt weiß oder schwarz, Log zeigt `spidev`-Fehler | SPI aus → `sudo raspi-config nonint do_spi 0`, Neustart |
| `Permission denied: /dev/spidev0.1` oder `gpiochip` | Benutzer nicht in `spi`/`gpio` → `install.sh` erneut laufen lassen |
| Bild steht auf dem Kopf | in `client.yaml` `rotation: 180` |
| Dauerhaft „verbinde …" | Darunter steht der Grund. Server-URL in `client.yaml` prüfen, dann `curl http://<server>:8080/healthz` vom Pi aus |
| Log: `PinPWMUnsupported: PWM is not supported on pin GPIO13` | `lgpio` kann sein Arbeitsverzeichnis nicht beschreiben → `install.sh` erneut laufen lassen (setzt `LG_WD`) |
| Taster reagieren nicht, Log meldet `lgpio`/`pin factory` | `sudo apt-get install python3-lgpio`, Dienst neu starten |
| Sehr träge | `htop`: Der Client sollte unter 15 % CPU liegen. Sonst Log prüfen. |
