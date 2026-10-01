# Silver Minds — backend, dispositivo y panel familiar

MVP de Silver Minds para Hack4Seniors UDD. Un agente conversa con una persona mayor
autovalente desde una pantalla en su casa, recuerda preferencias **confirmadas**, propone
actividades y facilita invitaciones **aprobadas** a familiares o amigos. La familia puede
ver señales de actividad y, cuando la persona mayor la enciende, la cámara en vivo.

> La persona mayor conserva el control. La efectividad sobre soledad o comprensión **no
> está demostrada**. Este software no es un servicio de emergencia ni entrega atención
> clínica, y su protocolo de apoyo necesita revisión profesional antes de un piloto.

## Qué incluye

| Parte | Dónde |
|---|---|
| API `/api/v1` con OpenAPI | `app/` · documentación en `/docs` |
| Worker con cola persistente en PostgreSQL | `app/worker/` |
| Dispositivo simulado: personaje, botón y llamada por nombre | `web/device/` · `/device/` |
| Panel familiar: cámara en vivo y métricas | `web/family/` · `/family/` |
| Política de seguridad y recursos de ayuda versionados | `config/` |
| Conocimiento y actividades de demostración (ficticios) | `data/knowledge/` |
| Evaluaciones de RAG y de seguridad | `evals/` |

## Requisitos

- Python 3.12 y [uv](https://docs.astral.sh/uv/)
- PostgreSQL 15 o superior con las extensiones `vector` y `unaccent`
- `ffmpeg` (aporta `ffprobe`, que valida los audios)

Sin credenciales externas todo funciona con adaptadores simulados.

## Instalación local

```bash
uv sync
```

```bash
createdb silver_minds
```

```bash
cp .env.example .env
```

Edita `.env` y reemplaza `SUPABASE_JWT_SECRET` por un valor aleatorio de al menos 32
caracteres. Luego aplica las migraciones:

```bash
uv run alembic upgrade head
```

## Ejecutar

API (sirve también el dispositivo y el panel):

```bash
uv run uvicorn app.main:app --port 8000 --ws-max-size 400000
```

Worker, en otra terminal:

```bash
uv run python -m app.worker.main
```

### Con Docker Compose

```bash
docker compose up --build
```

Levanta PostgreSQL con pgvector, aplica las migraciones e inicia la API y el worker.
**No se verificó en la máquina de desarrollo**, que no tiene Docker instalado.

## Demostración

Prepara datos ficticios (Rosa, su hija Camila, un dispositivo y un acceso familiar):

```bash
uv run python -m app.cli demo-setup
```

El comando imprime dos enlaces con tokens: el dispositivo de Rosa y el panel de Camila.
Ábrelos en Chrome; cámara y micrófono requieren `localhost` o HTTPS. El recorrido completo
está en [docs/demo.md](docs/demo.md).

Los correos simulados quedan en `var/outbox/`.

## Comandos administrativos

| Comando | Qué hace |
|---|---|
| `uv run python -m app.cli demo-setup` | Datos ficticios y enlaces de la demostración |
| `uv run python -m app.cli ingest data/knowledge/manifest.yaml` | Ingesta de conocimiento revisado |
| `uv run python -m app.cli eval-rag` | 30 consultas con fuentes esperadas |
| `uv run python -m app.cli eval-safety` | 52 escenarios sintéticos con señales simuladas |
| `uv run python -m app.cli eval-safety --live` | Los mismos escenarios contra el proveedor real |
| `uv run python -m app.cli check-ai` | Una llamada mínima por capacidad al proveedor de IA |
| `uv run python -m app.cli reindex` | Regenera los embeddings con el proveedor configurado |
| `uv run python -m app.cli dev-token` | JWT local para probar la API como cuenta |

## Pruebas

Usan PostgreSQL real y proveedores simulados:

```bash
createdb silver_minds_test
```

```bash
uv run pytest
```

```bash
uv run ruff check . && uv run ruff format --check .
```

CI (GitHub Actions) ejecuta lo mismo con un contenedor `pgvector/pgvector:pg17`.

## Proveedores reales

Cada integración se activa con variables de entorno; ver [.env.example](.env.example).

| Integración | Variable | Estado |
|---|---|---|
| OpenAI (Responses, embeddings, moderación, voz) | `AI_PROVIDER=openai` | Implementada, **sin ejecutar contra la API real** |
| Resend | `EMAIL_PROVIDER=resend` | Implementada, **sin ejecutar contra la API real** |
| Supabase Storage | `STORAGE_PROVIDER=supabase` | Implementada, **sin ejecutar contra un proyecto real** |
| Supabase Auth | `SUPABASE_JWKS_URL` | Verificación probada con claves ES256 locales |

`GET /api/v1/health/ready` indica qué adaptadores están activos.

### Conectar OpenAI

1. Agrega tu clave a `.env` (nunca al repositorio ni a un chat): `OPENAI_API_KEY=...`
2. En el mismo archivo cambia `AI_PROVIDER=fake` por `AI_PROVIDER=openai`.
3. Comprueba credenciales y modelos con una llamada mínima por capacidad:

```bash
uv run python -m app.cli check-ai
```

4. Reconstruye el índice: los vectores del simulador no son comparables con los de OpenAI.
   Hasta hacerlo, la recuperación usa solo búsqueda por texto.

```bash
uv run python -m app.cli reindex
```

5. Repite las evaluaciones con el modelo real y reinicia la API y el worker:

```bash
uv run python -m app.cli eval-rag && uv run python -m app.cli eval-safety --live
```

## Documentación

- [Arquitectura](docs/architecture.md)
- [Entidades y estados](docs/entities-and-states.md)
- [Contratos de API](docs/api-contracts.md)
- [Protocolo de seguridad](docs/safety-protocol.md)
- [ADR 0001: dispositivo y panel familiar](docs/adr/0001-dispositivo-y-panel-familiar.md)
- [Demostración](docs/demo.md)
- [Despliegue](docs/deployment.md)
- [Plan y estado real](Silver_Minds_Backend_Plan.md)
