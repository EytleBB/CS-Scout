#!/usr/bin/env bash
set -Eeuo pipefail

# Read-only release validation. Run this as the cs-scout service user with the
# three CS_SCOUT_*_DIR variables exported, or accept the /var/lib defaults.
APP_ROOT="${1:-/opt/cs-scout/current}"
PYTHON_BIN="${APP_ROOT}/.venv/bin/python"
DEMO_DIR="${CS_SCOUT_DEMO_DIR:-/var/lib/cs-scout/demos}"
OUTPUT_DIR="${CS_SCOUT_OUTPUT_DIR:-/var/lib/cs-scout/output}"
MAPS_DIR="${CS_SCOUT_MAPS_DIR:-/var/lib/cs-scout/maps}"

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

[[ -d "${APP_ROOT}/server" ]] || fail "missing server directory: ${APP_ROOT}/server"
[[ -x "${PYTHON_BIN}" ]] || fail "missing Python environment: ${PYTHON_BIN}"
[[ -d "${DEMO_DIR}" && -w "${DEMO_DIR}" ]] || fail "demo directory is not writable: ${DEMO_DIR}"
[[ -d "${OUTPUT_DIR}" && -w "${OUTPUT_DIR}" ]] || fail "output directory is not writable: ${OUTPUT_DIR}"
[[ -d "${MAPS_DIR}" && -r "${MAPS_DIR}" ]] || fail "map directory is not readable: ${MAPS_DIR}"

required_maps=(
    de_ancient de_anubis de_dust2 de_inferno
    de_mirage de_nuke de_overpass de_train
)
for map_name in "${required_maps[@]}"; do
    [[ -r "${MAPS_DIR}/${map_name}/meta.json" ]] || fail "missing ${map_name}/meta.json"
    [[ -r "${MAPS_DIR}/${map_name}/radar.png" ]] || fail "missing ${map_name}/radar.png"
done

export PYTHONDONTWRITEBYTECODE=1
export CS_SCOUT_DEMO_DIR="${DEMO_DIR}"
export CS_SCOUT_OUTPUT_DIR="${OUTPUT_DIR}"
export CS_SCOUT_MAPS_DIR="${MAPS_DIR}"

"${PYTHON_BIN}" -m pip check
(
    cd "${APP_ROOT}/server"
    "${PYTHON_BIN}" -c \
        'import config, web_server; required={"de_ancient","de_anubis","de_dust2","de_inferno","de_mirage","de_nuke","de_overpass","de_train"}; assert required <= set(web_server.maps.available_maps())'
)

available_kib="$(df -Pk "${DEMO_DIR}" | awk 'NR == 2 {print $4}')"
printf 'Release validation passed; demo filesystem has %s MiB available.\n' \
    "$((available_kib / 1024))"
