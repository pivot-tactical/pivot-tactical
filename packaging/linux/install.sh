#!/usr/bin/env bash
# PIVOT Tactical — Linux installer (spec §3.7, §9.1).
#
# Installs the bundle under /opt/pivot-tactical, runs it as a dedicated `pivot`
# system user via systemd, and stores data under /var/lib/pivot-tactical. This is
# the Linux counterpart to the Windows installer + tray: the server runs headless
# in the background and self-updates out-of-band on restart (the service applies
# any staged update before starting — see --apply-staged).
#
# Usage (from inside the extracted tarball):
#   sudo ./install.sh
set -euo pipefail

APP_NAME="pivot-tactical"
INSTALL_DIR="/opt/${APP_NAME}"
DATA_DIR="/var/lib/${APP_NAME}"
SERVICE_NAME="${APP_NAME}.service"
RUN_USER="pivot"

# Resolve the directory this script lives in (the extracted bundle root).
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $EUID -ne 0 ]]; then
  echo "This installer needs root (it creates a system user and a service)." >&2
  echo "Re-run with: sudo ./install.sh" >&2
  exit 1
fi

if [[ ! -x "${SRC_DIR}/PIVOT-Tactical" ]]; then
  echo "PIVOT-Tactical binary not found next to install.sh — run this from the" >&2
  echo "extracted tarball directory." >&2
  exit 1
fi

echo "==> Creating service user '${RUN_USER}' (if missing)"
if ! id -u "${RUN_USER}" >/dev/null 2>&1; then
  useradd --system --home-dir "${DATA_DIR}" --shell /usr/sbin/nologin "${RUN_USER}"
fi

echo "==> Installing application to ${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}"
# Mirror the bundle into place; --delete keeps upgrades clean. Exclude the data
# dir in case someone extracted on top of it.
if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete --exclude 'install.sh' --exclude 'uninstall.sh' \
    "${SRC_DIR}/" "${INSTALL_DIR}/"
else
  cp -a "${SRC_DIR}/." "${INSTALL_DIR}/"
fi

echo "==> Preparing data directory ${DATA_DIR}"
mkdir -p "${DATA_DIR}/versions"
chown -R "${RUN_USER}:${RUN_USER}" "${DATA_DIR}"
# The install dir is owned by the service user so in-app self-update (the staged
# swap) works without root.
chown -R "${RUN_USER}:${RUN_USER}" "${INSTALL_DIR}"

echo "==> Installing systemd unit ${SERVICE_NAME}"
install -m 0644 "${SRC_DIR}/${SERVICE_NAME}" "/etc/systemd/system/${SERVICE_NAME}"
systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}"

# Best-effort LAN IP for the operator.
IP="$(ip route get 1.1.1.1 2>/dev/null | awk '{print $7; exit}')"
PORT="${PIVOT_PORT:-8080}"
echo
echo "PIVOT is installed and running."
echo "  Open  http://${IP:-<server-ip>}:${PORT}  in a browser on the LAN."
echo "  Logs: journalctl -u ${SERVICE_NAME} -f"
echo "  Stop: sudo systemctl stop ${SERVICE_NAME}"
