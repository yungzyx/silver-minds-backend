#!/bin/zsh
# Lanza el MVP de Silver Minds en este computador y lo abre en Google Chrome.
#
# Uso: ./scripts/start-demo.sh        (Ctrl+C lo detiene)
#      NO_OPEN=1 ./scripts/start-demo.sh   (no abre el navegador)
#
# Todo corre en local con datos ficticios y proveedores simulados.

set -u
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

ROOT="${0:A:h:h}"
cd "$ROOT" || exit 1

PORT=8000
BASE="http://localhost:$PORT"
LINKS="var/demo-links.txt"
mkdir -p var/logs

say() { print -P "%F{208}▸%f $1"; }
fail() { print -P "%F{red}✗ $1%f"; exit 1; }

# --- Requisitos ---------------------------------------------------------------------
command -v uv >/dev/null || fail "Falta uv. Instálalo con: brew install uv"
command -v psql >/dev/null || fail "Falta PostgreSQL. Instálalo con: brew install postgresql@17 pgvector"
command -v ffprobe >/dev/null || say "Aviso: falta ffmpeg; los audios subidos al backend no se podrán validar."

if ! pg_isready -q; then
  say "Iniciando PostgreSQL…"
  brew services start postgresql@17 >/dev/null 2>&1
  for _ in {1..20}; do pg_isready -q && break; sleep 0.5; done
  pg_isready -q || fail "PostgreSQL no responde."
fi

[[ -f .env ]] || fail "Falta .env. Cópialo desde .env.example y cambia SUPABASE_JWT_SECRET."

say "Preparando dependencias y base de datos…"
uv sync --frozen --quiet || fail "No se pudieron instalar las dependencias."
psql -d postgres -Atc "SELECT 1 FROM pg_database WHERE datname='silver_minds'" | grep -q 1 \
  || createdb silver_minds
uv run alembic upgrade head >var/logs/migrations.log 2>&1 || fail "Fallaron las migraciones (var/logs/migrations.log)."

# --- Procesos -----------------------------------------------------------------------
PIDS=()
stop_all() {
  print
  say "Deteniendo Silver Minds…"
  for pid in $PIDS; do kill "$pid" 2>/dev/null; done
  wait 2>/dev/null
}
trap stop_all EXIT
trap 'exit 0' INT TERM HUP

API_PID=""
start_api() {
  uv run uvicorn app.main:app --port $PORT --ws-max-size 400000 >>var/logs/api.log 2>&1 &
  API_PID=$!
  PIDS+=($API_PID)
}
api_up() { curl -fsS "$BASE/api/v1/health" >/dev/null 2>&1; }

if api_up; then
  say "Ya había una API en el puerto $PORT; se usa esa y, si se cierra, se levanta una propia."
else
  start_api
fi

# Siempre un worker propio: varios workers conviven sin duplicar trabajos.
uv run python -m app.worker.main >var/logs/worker.log 2>&1 &
PIDS+=($!)

for _ in {1..40}; do
  curl -fsS "$BASE/api/v1/health/ready" >/dev/null 2>&1 && break
  sleep 0.5
done
curl -fsS "$BASE/api/v1/health/ready" >/dev/null 2>&1 || fail "La API no respondió (var/logs/api.log)."

# --- Enlaces de la demostración -----------------------------------------------------
# Se conservan entre ejecuciones para que el navegador siga emparejado.
link() { grep -o "$BASE/$1/#token=[A-Za-z0-9_-]*" "$LINKS" 2>/dev/null | head -1; }
valid() {
  [[ -n "$2" ]] && curl -fsS -o /dev/null -H "Authorization: $1 ${2##*=}" "$BASE/api/v1/$3"
}

if ! valid Device "$(link device)" device/session || ! valid Viewer "$(link family)" family/overview; then
  say "Creando los datos ficticios de la demostración…"
  uv run python -m app.cli demo-setup >"$LINKS" 2>var/logs/demo-setup.log \
    || fail "No se pudo preparar la demostración (var/logs/demo-setup.log)."
  chmod 600 "$LINKS"
fi
DEVICE_URL="$(link device)"
FAMILY_URL="$(link family)"

if [[ -z "${NO_OPEN:-}" ]]; then
  if [[ -d "/Applications/Google Chrome.app" ]]; then
    open -a "Google Chrome" "$DEVICE_URL"
    sleep 1
    open -na "Google Chrome" --args --new-window "$FAMILY_URL"
  else
    open "$DEVICE_URL"
    open "$FAMILY_URL"
  fi
fi

print
print -P "%F{green}✓ Silver Minds está corriendo%f  (datos ficticios, proveedores simulados)"
print
print "  Dispositivo de la persona mayor   $BASE/device/"
print "  Panel familiar                    $BASE/family/"
print "  API y documentación               $BASE/docs"
print
print "  En el dispositivo: «Toca para encender», acepta el micrófono y di «Silvia»."
print "  Para compartir la cámara: activa el interruptor Cámara."
print "  Correos simulados: var/outbox/   ·   Registros: var/logs/"
print
print "  Si Chrome pide volver a vincular la pantalla, los enlaces están en $LINKS"
print
print -P "%F{208}Deja esta ventana abierta. Ctrl+C (o cerrarla) detiene todo.%f"

# Vigila la API: si la que estaba en uso se cierra, levanta una propia.
while true; do
  sleep 5
  if ! api_up && { [[ -z "$API_PID" ]] || ! kill -0 "$API_PID" 2>/dev/null; }; then
    say "La API no responde; iniciándola…"
    start_api
  fi
done
