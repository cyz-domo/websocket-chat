#!/usr/bin/env bash

set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$PROJECT_DIR"

SERVICE_NAME="${SERVICE_NAME:-websocket-chat}"
UNIT_FILE="${UNIT_FILE:-/etc/systemd/system/${SERVICE_NAME}.service}"
ENV_FILE="${ENV_FILE:-/etc/default/${SERVICE_NAME}}"
VENV_PATH="${VENV_PATH:-.venv}"
PYTHON_BIN="$PROJECT_DIR/$VENV_PATH/bin/python"
DAPHNE_BIN="$PROJECT_DIR/$VENV_PATH/bin/daphne"
APP_USER="${APP_USER:-$(id -un)}"
APP_GROUP="${APP_GROUP:-$(id -gn)}"
BIND_HOST="${BIND_HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
APP_MODULE="${APP_MODULE:-websocket_project.asgi:application}"
MIGRATE_ON_START="${MIGRATE_ON_START:-1}"
INSTALL_DEPS_ON_START="${INSTALL_DEPS_ON_START:-1}"
PYPI_MIRROR_URL="${PYPI_MIRROR_URL:-https://mirrors.aliyun.com/pypi/simple/}"
PYPI_TRUSTED_HOST="${PYPI_TRUSTED_HOST:-mirrors.aliyun.com}"
GEOCODE_PROVIDER="${GEOCODE_PROVIDER:-auto}"
GEOCODE_TIMEOUT="${GEOCODE_TIMEOUT:-8}"
AMAP_WEB_API_KEY="${AMAP_WEB_API_KEY:-}"
REVERSE_GEOCODE_URL="${REVERSE_GEOCODE_URL:-https://nominatim.openstreetmap.org/reverse}"
BIGDATA_REVERSE_URL="${BIGDATA_REVERSE_URL:-https://api.bigdatacloud.net/data/reverse-geocode-client}"
GEOCODE_USER_AGENT="${GEOCODE_USER_AGENT:-websocket-chat/1.0 (location reverse geocoding)}"
REDIS_URL="${REDIS_URL:-}"
MOBILE_PUSH_NOTIFICATIONS_ENABLED="${MOBILE_PUSH_NOTIFICATIONS_ENABLED:-1}"
PUSH_NOTIFY_ONLINE_USERS="${PUSH_NOTIFY_ONLINE_USERS:-0}"
FIREBASE_CREDENTIALS_FILE="${FIREBASE_CREDENTIALS_FILE:-}"
FIREBASE_PROJECT_ID="${FIREBASE_PROJECT_ID:-}"
REQUIREMENTS_STAMP="$PROJECT_DIR/$VENV_PATH/.requirements.installed"
SELF_SCRIPT="$PROJECT_DIR/scripts/service.sh"
DB_RUNTIME_CONFIG="${DB_RUNTIME_CONFIG:-$PROJECT_DIR/.runtime-db.env}"
DB_SETUP_WIZARD="$PROJECT_DIR/scripts/db_setup_wizard.py"

DB_BACKEND="${DB_BACKEND:-}"
DB_NAME="${DB_NAME:-}"
DB_USER="${DB_USER:-}"
DB_PASSWORD="${DB_PASSWORD:-}"
DB_HOST="${DB_HOST:-}"
DB_PORT="${DB_PORT:-}"
DB_SSLMODE="${DB_SSLMODE:-}"
SQLITE_PATH="${SQLITE_PATH:-}"

if [ "$(id -u)" -eq 0 ]; then
  SUDO_CMD=""
else
  SUDO_CMD="sudo"
fi

run_as_root() {
  if [ -n "$SUDO_CMD" ]; then
    "$SUDO_CMD" "$@"
  else
    "$@"
  fi
}

ensure_command() {
  local command_name="$1"
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "$command_name is not installed."
    exit 1
  fi
}

ensure_python3() {
  ensure_command python3
  echo "python3"
}

ensure_systemd() {
  ensure_command systemctl
  ensure_command journalctl
}

ensure_venv() {
  local python3_bin
  python3_bin="$(ensure_python3)"

  if [ ! -d "$PROJECT_DIR/$VENV_PATH" ]; then
    echo "Creating virtual environment at $PROJECT_DIR/$VENV_PATH"
    "$python3_bin" -m venv "$PROJECT_DIR/$VENV_PATH"
  fi

  if [ ! -x "$PYTHON_BIN" ]; then
    echo "Python executable not found in $VENV_PATH"
    exit 1
  fi
}

escape_env_value() {
  local value="${1:-}"
  printf "%q" "$value"
}

load_db_runtime_config() {
  if [ -f "$DB_RUNTIME_CONFIG" ]; then
    # shellcheck disable=SC1090
    source "$DB_RUNTIME_CONFIG"
    export DB_BACKEND DB_NAME DB_USER DB_PASSWORD DB_HOST DB_PORT DB_SSLMODE SQLITE_PATH
  fi
}

write_db_runtime_config() {
  mkdir -p "$(dirname "$DB_RUNTIME_CONFIG")"
  cat >"$DB_RUNTIME_CONFIG" <<EOF
DB_BACKEND=$(escape_env_value "$DB_BACKEND")
DB_NAME=$(escape_env_value "$DB_NAME")
DB_USER=$(escape_env_value "$DB_USER")
DB_PASSWORD=$(escape_env_value "$DB_PASSWORD")
DB_HOST=$(escape_env_value "$DB_HOST")
DB_PORT=$(escape_env_value "$DB_PORT")
DB_SSLMODE=$(escape_env_value "$DB_SSLMODE")
SQLITE_PATH=$(escape_env_value "$SQLITE_PATH")
EOF
}

launch_db_setup_wizard() {
  "$PYTHON_BIN" "$DB_SETUP_WIZARD" \
    --config-file "$DB_RUNTIME_CONFIG" \
    --project-dir "$PROJECT_DIR" \
    --sqlite-path "${SQLITE_PATH:-$PROJECT_DIR/db.sqlite3}" \
    --db-name "${DB_NAME:-websocket_chat}" \
    --db-user "${DB_USER:-postgres}" \
    --db-password "${DB_PASSWORD:-}" \
    --db-host "${DB_HOST:-127.0.0.1}" \
    --db-port "${DB_PORT:-5432}" \
    --db-sslmode "${DB_SSLMODE:-disable}"
  load_db_runtime_config
}

ensure_db_runtime_config() {
  load_db_runtime_config

  if [ -f "$DB_RUNTIME_CONFIG" ] && [ -n "$DB_BACKEND" ]; then
    return
  fi

  if [ -n "$DB_BACKEND" ]; then
    write_db_runtime_config
    return
  fi

  if [ -t 0 ] && [ -t 1 ]; then
    launch_db_setup_wizard
    return
  fi

  DB_BACKEND="sqlite"
  SQLITE_PATH="$PROJECT_DIR/db.sqlite3"
  write_db_runtime_config
  echo "未检测到交互终端，已默认使用 SQLite。"
}

requirements_changed() {
  if [ ! -f "$PROJECT_DIR/requirements.txt" ]; then
    echo "requirements.txt not found."
    exit 1
  fi

  if [ ! -f "$REQUIREMENTS_STAMP" ]; then
    return 0
  fi

  if ! cmp -s "$PROJECT_DIR/requirements.txt" "$REQUIREMENTS_STAMP"; then
    return 0
  fi

  return 1
}

install_dependencies() {
  ensure_venv

  if requirements_changed; then
    echo "Installing Python dependencies..."
    "$PYTHON_BIN" -m pip install --upgrade pip \
      -i "$PYPI_MIRROR_URL" \
      --trusted-host "$PYPI_TRUSTED_HOST"
    "$PYTHON_BIN" -m pip install -r "$PROJECT_DIR/requirements.txt" \
      -i "$PYPI_MIRROR_URL" \
      --trusted-host "$PYPI_TRUSTED_HOST"
    cp "$PROJECT_DIR/requirements.txt" "$REQUIREMENTS_STAMP"
  else
    echo "Python dependencies are up to date."
  fi
}

prepare_runtime() {
  ensure_venv
  ensure_db_runtime_config

  if [ "$INSTALL_DEPS_ON_START" = "1" ]; then
    install_dependencies
  fi

  if [ "$MIGRATE_ON_START" = "1" ]; then
    echo "Applying migrations..."
    "$PYTHON_BIN" "$PROJECT_DIR/manage.py" migrate --noinput
  fi
}

run_dev_server() {
  prepare_runtime
  echo "Starting Django ASGI development server at http://${BIND_HOST}:${PORT}"
  exec "$PYTHON_BIN" "$PROJECT_DIR/manage.py" runserver "${BIND_HOST}:${PORT}" --insecure
}

run_daphne_server() {
  prepare_runtime

  if [ ! -x "$DAPHNE_BIN" ]; then
    echo "Daphne executable not found: $DAPHNE_BIN"
    echo "Please ensure dependencies are installed correctly."
    exit 1
  fi

  echo "Starting Daphne on ${BIND_HOST}:${PORT} with ${APP_MODULE}"
  exec "$DAPHNE_BIN" -b "$BIND_HOST" -p "$PORT" "$APP_MODULE"
}

write_env_file() {
  run_as_root mkdir -p "$(dirname "$ENV_FILE")"
  run_as_root tee "$ENV_FILE" >/dev/null <<EOF
PROJECT_DIR=$(escape_env_value "$PROJECT_DIR")
VENV_PATH=$(escape_env_value "$VENV_PATH")
BIND_HOST=$(escape_env_value "$BIND_HOST")
PORT=$(escape_env_value "$PORT")
APP_MODULE=$(escape_env_value "$APP_MODULE")
MIGRATE_ON_START=$(escape_env_value "$MIGRATE_ON_START")
INSTALL_DEPS_ON_START=$(escape_env_value "$INSTALL_DEPS_ON_START")
PYPI_MIRROR_URL=$(escape_env_value "$PYPI_MIRROR_URL")
PYPI_TRUSTED_HOST=$(escape_env_value "$PYPI_TRUSTED_HOST")
GEOCODE_PROVIDER=$(escape_env_value "$GEOCODE_PROVIDER")
GEOCODE_TIMEOUT=$(escape_env_value "$GEOCODE_TIMEOUT")
AMAP_WEB_API_KEY=$(escape_env_value "$AMAP_WEB_API_KEY")
REVERSE_GEOCODE_URL=$(escape_env_value "$REVERSE_GEOCODE_URL")
BIGDATA_REVERSE_URL=$(escape_env_value "$BIGDATA_REVERSE_URL")
GEOCODE_USER_AGENT=$(escape_env_value "$GEOCODE_USER_AGENT")
REDIS_URL=$(escape_env_value "$REDIS_URL")
MOBILE_PUSH_NOTIFICATIONS_ENABLED=$(escape_env_value "$MOBILE_PUSH_NOTIFICATIONS_ENABLED")
PUSH_NOTIFY_ONLINE_USERS=$(escape_env_value "$PUSH_NOTIFY_ONLINE_USERS")
FIREBASE_CREDENTIALS_FILE=$(escape_env_value "$FIREBASE_CREDENTIALS_FILE")
FIREBASE_PROJECT_ID=$(escape_env_value "$FIREBASE_PROJECT_ID")
DB_BACKEND=$(escape_env_value "$DB_BACKEND")
DB_NAME=$(escape_env_value "$DB_NAME")
DB_USER=$(escape_env_value "$DB_USER")
DB_PASSWORD=$(escape_env_value "$DB_PASSWORD")
DB_HOST=$(escape_env_value "$DB_HOST")
DB_PORT=$(escape_env_value "$DB_PORT")
DB_SSLMODE=$(escape_env_value "$DB_SSLMODE")
SQLITE_PATH=$(escape_env_value "$SQLITE_PATH")
EOF
}

write_unit_file() {
  run_as_root tee "$UNIT_FILE" >/dev/null <<EOF
[Unit]
Description=WebSocket Chat Django Service
After=network.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_GROUP
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$SELF_SCRIPT serve
Restart=always
RestartSec=5
KillSignal=SIGINT
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF
}

reload_systemd() {
  run_as_root systemctl daemon-reload
}

service_action() {
  local action="$1"
  ensure_systemd
  run_as_root systemctl "$action" "$SERVICE_NAME"
}

install_service() {
  ensure_systemd
  chmod +x "$SELF_SCRIPT" "$PROJECT_DIR/start.sh"
  prepare_runtime
  write_env_file
  write_unit_file
  reload_systemd
  service_action enable
  service_action restart
  echo "Installed and started ${SERVICE_NAME}.service"
}

uninstall_service() {
  ensure_systemd
  set +e
  run_as_root systemctl stop "$SERVICE_NAME" >/dev/null 2>&1
  run_as_root systemctl disable "$SERVICE_NAME" >/dev/null 2>&1
  set -e
  run_as_root rm -f "$UNIT_FILE" "$ENV_FILE"
  reload_systemd
  echo "Removed ${SERVICE_NAME}.service"
}

logs_service() {
  ensure_systemd
  run_as_root journalctl -u "$SERVICE_NAME" -n 200 -f
}

show_config() {
  ensure_db_runtime_config
  cat <<EOF
Service name:          $SERVICE_NAME
Project dir:           $PROJECT_DIR
Unit file:             $UNIT_FILE
Environment file:      $ENV_FILE
Launcher script:       $SELF_SCRIPT
App user/group:        $APP_USER:$APP_GROUP
Bind host/port:        $BIND_HOST:$PORT
ASGI module:           $APP_MODULE
Migrate on start:      $MIGRATE_ON_START
Install deps on start: $INSTALL_DEPS_ON_START
Virtual env path:      $VENV_PATH
Geocode provider:      $GEOCODE_PROVIDER
Geocode timeout:       $GEOCODE_TIMEOUT
AMap key:              ${AMAP_WEB_API_KEY:+configured}
Reverse geocode URL:   $REVERSE_GEOCODE_URL
Secondary geocode URL: $BIGDATA_REVERSE_URL
Redis URL:             ${REDIS_URL:+configured}
Push enabled:          $MOBILE_PUSH_NOTIFICATIONS_ENABLED
Push online users:     $PUSH_NOTIFY_ONLINE_USERS
Firebase credentials:  ${FIREBASE_CREDENTIALS_FILE:-not configured}
Firebase project id:   ${FIREBASE_PROJECT_ID:-not configured}
Database backend:      ${DB_BACKEND:-sqlite}
Database host/name:    ${DB_HOST:-local}/${DB_NAME:-${SQLITE_PATH:-db.sqlite3}}
Runtime DB config:     $DB_RUNTIME_CONFIG
EOF
}

usage() {
  cat <<EOF
Usage: ./scripts/service.sh <command>

Commands:
  dev         Prepare venv/deps/migrations and start Django dev server
  serve       Prepare venv/deps/migrations and start Daphne
  install     Install/update the systemd service and start it
  start       Start the systemd service
  stop        Stop the systemd service
  restart     Restart the systemd service
  status      Show service status
  enable      Enable the service at boot
  disable     Disable the service at boot
  logs        Tail service logs
  config      Show the resolved runtime configuration
  db-reset    Delete the saved database choice so next start asks again
  uninstall   Stop, disable and remove the service

Optional environment variables:
  SERVICE_NAME           Default: websocket-chat
  APP_USER               Default: current user
  APP_GROUP              Default: current user's primary group
  VENV_PATH              Default: .venv
  BIND_HOST              Default: 0.0.0.0
  PORT                   Default: 8000
  APP_MODULE             Default: websocket_project.asgi:application
  MIGRATE_ON_START       Default: 1
  INSTALL_DEPS_ON_START  Default: 1
  PYPI_MIRROR_URL        Default: https://mirrors.aliyun.com/pypi/simple/
  PYPI_TRUSTED_HOST      Default: mirrors.aliyun.com
  GEOCODE_PROVIDER       Default: auto
  GEOCODE_TIMEOUT        Default: 8
  AMAP_WEB_API_KEY       Optional: 高德 Web 服务 Key，国内服务器建议配置
  REVERSE_GEOCODE_URL    Default: https://nominatim.openstreetmap.org/reverse
  BIGDATA_REVERSE_URL    Default: https://api.bigdatacloud.net/data/reverse-geocode-client
  REDIS_URL              Optional: Redis 连接串
  MOBILE_PUSH_NOTIFICATIONS_ENABLED  Default: 1
  PUSH_NOTIFY_ONLINE_USERS           Default: 0
  FIREBASE_CREDENTIALS_FILE          Optional: Firebase service account JSON 路径
  FIREBASE_PROJECT_ID                Optional: Firebase project id
  DB_RUNTIME_CONFIG      Default: .runtime-db.env in project root
EOF
}

reset_db_runtime_config() {
  rm -f "$DB_RUNTIME_CONFIG"
  echo "已清除数据库选择。下次启动会重新询问。"
}

main() {
  local command="${1:-dev}"

  case "$command" in
    dev)
      run_dev_server
      ;;
    serve)
      run_daphne_server
      ;;
    install)
      install_service
      ;;
    start|stop|restart|status|enable|disable)
      service_action "$command"
      ;;
    logs)
      logs_service
      ;;
    config)
      show_config
      ;;
    db-reset)
      reset_db_runtime_config
      ;;
    uninstall)
      uninstall_service
      ;;
    *)
      usage
      exit 1
      ;;
  esac
}

main "$@"
