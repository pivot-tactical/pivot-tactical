#!/usr/bin/env bash
# PIVOT Tactical — Linux uninstaller. Removes the service and the application,
# and (optionally) the data directory.
#
# Usage:
#   sudo ./uninstall.sh            # remove app + service, keep data
#   sudo ./uninstall.sh --purge    # also delete /var/lib/pivot-tactical
set -euo pipefail

APP_NAME="pivot-tactical"
INSTALL_DIR="/opt/${APP_NAME}"
DATA_DIR="/var/lib/${APP_NAME}"
SERVICE_NAME="${APP_NAME}.service"
RUN_USER="pivot"
PURGE="${1:-}"

if [[ $EUID -ne 0 ]]; then
  echo "Re-run with: sudo ./uninstall.sh [--purge]" >&2
  exit 1
fi

echo "==> Stopping and disabling ${SERVICE_NAME}"
systemctl disable --now "${SERVICE_NAME}" 2>/dev/null || true
rm -f "/etc/systemd/system/${SERVICE_NAME}"
systemctl daemon-reload

echo "==> Removing ${INSTALL_DIR}"
rm -rf "${INSTALL_DIR}"

if [[ "${PURGE}" == "--purge" ]]; then
  echo "==> Purging data directory ${DATA_DIR}"
  rm -rf "${DATA_DIR}"
  userdel "${RUN_USER}" 2>/dev/null || true
  echo "Removed PIVOT and all its data."
else
  echo "Removed PIVOT. Data kept at ${DATA_DIR} (use --purge to delete it)."
fi
