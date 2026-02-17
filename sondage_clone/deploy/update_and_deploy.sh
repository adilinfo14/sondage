#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
COMPOSE_REL="sondage_clone/docker-compose.yml"
COMPOSE_PATH="${REPO_ROOT}/${COMPOSE_REL}"
BACKUP_PATH="${HOME}/docker-compose.server.backup.yml"
TEMP_PATH="$(mktemp -u "${HOME}/docker-compose.server.backup.XXXXXX.yml")"

cd "${REPO_ROOT}"

echo "[1/6] Vérification du repo: ${REPO_ROOT}"
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Erreur: dossier courant hors dépôt git."
  exit 1
fi

echo "[2/6] Sauvegarde du compose serveur"
HAD_COMPOSE=0
if [[ -f "${COMPOSE_PATH}" ]]; then
  HAD_COMPOSE=1
  cp "${COMPOSE_PATH}" "${BACKUP_PATH}"
  mv "${COMPOSE_PATH}" "${TEMP_PATH}"
fi

echo "[3/6] Pull fast-forward"
if ! git pull --ff-only origin main; then
  echo "Le pull a échoué, restauration du compose serveur..."
  if [[ "${HAD_COMPOSE}" -eq 1 && -f "${TEMP_PATH}" ]]; then
    mv "${TEMP_PATH}" "${COMPOSE_PATH}"
  fi
  exit 1
fi

echo "[4/6] Restauration du compose serveur"
if [[ "${HAD_COMPOSE}" -eq 1 && -f "${TEMP_PATH}" ]]; then
  mv "${TEMP_PATH}" "${COMPOSE_PATH}"
elif [[ -f "${BACKUP_PATH}" ]]; then
  cp "${BACKUP_PATH}" "${COMPOSE_PATH}"
fi

echo "[5/6] Rebuild + restart"
cd "${REPO_ROOT}/sondage_clone"
docker compose up -d --build --force-recreate
docker compose ps

echo "[6/6] Vérification HEAD vs origin/main"
cd "${REPO_ROOT}"
LOCAL_HEAD="$(git rev-parse --short HEAD)"
REMOTE_HEAD="$(git rev-parse --short origin/main)"
echo "HEAD local : ${LOCAL_HEAD}"
echo "HEAD remote: ${REMOTE_HEAD}"

if [[ "${LOCAL_HEAD}" != "${REMOTE_HEAD}" ]]; then
  echo "Attention: HEAD local différent de origin/main"
  exit 1
fi

echo "Déploiement terminé avec succès."
