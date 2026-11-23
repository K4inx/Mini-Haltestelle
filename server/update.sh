#!/usr/bin/env bash
# Server aktualisieren: neuesten Stand holen, Image neu bauen, alte Images wegräumen.
# Aufruf auf dem Server (als root):  /opt/abfahrtsanzeige/server/update.sh
# config/, data/ und .env sind nicht im Repo und bleiben unangetastet.
set -euo pipefail
cd "$(dirname "$0")"
git -C .. pull --ff-only
chown -R 1000:1000 config data   # App-User im Image; git/tar setzen sonst root
docker compose up -d --build
docker image prune -f >/dev/null
sleep 5
curl -fsS http://127.0.0.1:8080/healthz && echo
