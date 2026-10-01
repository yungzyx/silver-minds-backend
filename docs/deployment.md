# Despliegue

**Estado: preparado, no desplegado.** No se creó infraestructura ni se contrató nada.
`render.yaml` y el `Dockerfile` no se han ejecutado en Render ni en Docker.

## Piezas

| Pieza | Dónde | Notas |
|---|---|---|
| Base de datos | Supabase (PostgreSQL) | Extensiones `vector` y `unaccent` |
| Autenticación | Supabase Auth | Claves asimétricas mediante JWKS |
| Audios temporales | Supabase Storage | Bucket privado `audio-temp` |
| API y páginas | Render, servicio web Docker | Una instancia |
| Worker | Render, background worker Docker | Sin plan gratuito |
| Correo | Resend | Dominio remitente verificado |
| IA | OpenAI | Modelos configurables por entorno |

## Pasos pendientes

Cada uno requiere una cuenta o una decisión de gasto del equipo.

1. **Supabase.** Crear el proyecto. En el panel SQL habilitar `vector` y `unaccent`.
   Crear el bucket privado `audio-temp`. Copiar la URL del JWKS, la clave de servicio y
   la cadena de conexión en modo sesión (puerto 5432).
2. **Migraciones.** `DATABASE_URL=… uv run alembic upgrade head`. La cadena debe empezar
   con `postgresql+psycopg://`. Las migraciones no se han probado contra Supabase.
3. **OpenAI.** Crear la clave y ejecutar `uv run python -m app.cli eval-safety --live` y
   `eval-rag` antes de habilitar a nadie.
4. **Resend.** Verificar el dominio remitente y crear la clave.
5. **Render.** Crear el Blueprint desde `render.yaml` y completar las variables marcadas
   `sync: false`. Esto genera cobros.
6. **Conocimiento.** `python -m app.cli ingest <manifiesto>` con material revisado. El
   corpus de `data/knowledge/` es de demostración.

## Modelos

Verificados en la documentación de OpenAI el 1 de octubre de 2026:

| Uso | Modelo | Estado |
|---|---|---|
| Texto | `gpt-4.1-mini` | Disponible |
| Embeddings | `text-embedding-3-small` (1536 dimensiones) | Disponible |
| Moderación | `omni-moderation-latest` | Disponible |
| Voz | `gpt-4o-mini-tts` | Disponible |
| Transcripción | `gpt-4o-mini-transcribe` | **En retiro: se elimina el 26 de febrero de 2027** |

Se mantiene `gpt-4o-mini-transcribe` porque es el modelo acordado y sigue disponible. El
reemplazo que indica OpenAI es `gpt-transcribe`; basta cambiar
`OPENAI_TRANSCRIPTION_MODEL`. No se probó ninguno contra la API real.

Cambiar de modelo, de prompt, de política o de índice obliga a repetir las evaluaciones.

## Antes de un piloto con personas reales

- Revisión profesional del protocolo de apoyo, sus plantillas y sus escenarios.
- Volver a verificar los recursos de ayuda en sus fuentes oficiales (y luego cada mes).
- Límite de peticiones por cliente; hoy no hay.
- Políticas RLS en Supabase como defensa adicional al aislamiento de la aplicación.
- Un canal compartido para la transmisión si se necesita más de una instancia.
- Reemplazar el reconocimiento de voz del navegador por uno que no envíe el audio a
  terceros sin acuerdo de tratamiento de datos.
- Entrevistas con personas de 60 a 75 años sobre la cámara y el panel
  ([ADR 0001](adr/0001-dispositivo-y-panel-familiar.md)).
