# Mock auf dem Windows-PC

Zeigt genau das, was später auf dem Display steht, in einem Fenster (doppelt so groß).
Die Taster bedienst du über die Tastatur. Der Code ist derselbe wie auf dem Pi, nur die
Ausgabe ist ein Fenster statt des Displays.

## Einmalig einrichten (PowerShell)

Code holen: entweder `git clone https://github.com/K4inx/Mini-Haltestelle.git` oder auf
GitHub *Code → Download ZIP* und entpacken. Dann in den Ordner `client` wechseln:

```powershell
cd <Pfad>\Mini-Haltestelle\client

# Python über den vollen Pfad: „python" allein öffnet auf diesem PC den Microsoft Store
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-pc.txt
```

## Starten

```powershell
# mit deinem Server (gleiche Daten wie im Web-UI)
.\.venv\Scripts\python.exe -m abfahrt_client --mock --server http://<server-ip>:8080

# ohne Server, mit Beispieldaten
.\.venv\Scripts\python.exe -m abfahrt_client --mock --demo

# helles Theme
.\.venv\Scripts\python.exe -m abfahrt_client --mock --demo --theme light
```

Die Geräte-ID ist `pi`, wie im Web-UI. Taster im Fenster und in der Web-Vorschau wirken
deshalb auf dasselbe „Gerät". Mit `--device pc` bekommt der Mock einen eigenen Zustand.

## Tasten

| Taste | kurz | 3 s halten |
|---|---|---|
| **A** / **B** | vorherige / nächste Vorlage | — |
| **X** | Details zur nächsten Bahn | Nachtmodus an/aus |
| **Y** (oder **Z**) | Erinnere mich (nochmal = aus) | Info-Seite |
| **Esc** | Fenster schließen | |

Rechts neben dem Bild leuchtet die simulierte LED. Nachts wird das Bild dunkler, und die
erste Taste weckt es nur.

## Probleme

| Meldung | Abhilfe |
|---|---|
| `python.exe` nicht gefunden | Python 3.12+ von python.org installieren, Pfad anpassen |
| `No module named pygame` | `pip install` oben nicht gelaufen oder anderes venv |
| Fenster zeigt „verbinde …" | PC im Heimnetz? `http://<server-ip>:8080` im Browser öffnen |

`<server-ip>` ist die Adresse deines Servers. Wer sie nicht jedes Mal tippen will, legt
eine `client.yaml` mit `server_url: ...` an und startet mit `--config client.yaml`.
