# Arquitectura

Versión 1 · 1 de octubre de 2026

## 1. Alcance

Backend de un agente conversacional para una persona mayor autovalente (60–75 años).
Conversa, recuerda preferencias confirmadas, propone actividades y facilita invitaciones
aprobadas a contactos que aceptaron participar.

Fuera de alcance en esta versión: búsqueda web abierta, llamadas autónomas a servicios
de emergencia, avisos automáticos a familiares e interpretación de tono de voz.

La efectividad del producto sobre soledad o comprensión no está demostrada. Este
documento describe ingeniería, no resultados.

## 2. Procesos

Monolito modular con dos procesos que comparten código y base de datos:

| Proceso | Entrada | Responsabilidad |
|---|---|---|
| API | `uvicorn app.main:app` | HTTP `/api/v1`, autenticación, flujo conversacional |
| Worker | `python -m app.worker.main` | Cola persistente: correos, embeddings, audio, limpieza |

Ambos usan PostgreSQL (con `pgvector`) como única fuente de verdad. La cola vive en la
tabla `jobs`; no hay broker adicional.

## 3. Capas

```text
rutas (router.py)  →  servicios (service.py)  →  repositorios (repository.py)  →  PostgreSQL
                              │
                              └──→ integraciones (adaptadores) → OpenAI · Resend · Storage
```

- **Rutas:** validan entrada y salida con Pydantic. No contienen reglas de negocio.
- **Servicios:** reglas de negocio, permisos, estados y transacciones.
- **Repositorios:** consultas SQLAlchemy. Toda consulta de datos personales recibe
  `owner_id` como argumento obligatorio.
- **Integraciones:** interfaces (`Protocol`) con una implementación real y otra simulada.
  Las pruebas y el desarrollo sin credenciales usan la simulada.

## 4. Módulos

| Módulo | Contenido |
|---|---|
| `profiles` | Perfil y preferencias estructuradas |
| `memory` | Memorias candidatas y confirmadas, embeddings personales |
| `contacts` | Contactos, aceptación y revocación |
| `conversations` | Conversaciones, mensajes y orquestación del agente |
| `rag` | Ingesta administrativa y recuperación híbrida |
| `safety` | Reglas, moderación, clasificador, enrutador y validación de salida |
| `actions` | Propuestas, invitaciones, actividades y feedback |
| `voice` | Carga, validación, transcripción y síntesis |
| `followup` | Recordatorios, seguimiento, auditoría y retención |

## 5. Flujo de cada mensaje

```text
1. Identidad            JWT de Supabase → perfil del propietario
2. Seguridad            reglas → moderación → clasificador contextual → enrutador
3. ¿Ruta normal?
     no → protocolo fijo de apoyo, sin propuestas ni memorias candidatas
     sí → 4
4. Contexto             perfil y preferencias (exacto) + turnos recientes
                        + recuperación autorizada (RAG)
5. Generación           OpenAI Responses API con salida estructurada
6. Validación de salida moderación del texto, referencias y permisos
7. Respuesta            reply, mode, proposals, memory_candidates, source_refs, support_options
```

La evaluación de seguridad ocurre **antes** de recuperar contexto o generar
recomendaciones. Ninguna respuesta normal se entrega sin pasar la validación de salida.

## 6. Tres fuentes de contexto

| Fuente | Mecanismo | Regla |
|---|---|---|
| Perfil, preferencias, contactos y permisos | Consulta exacta por `owner_id` | Nunca por similitud semántica |
| Conversación reciente | Últimos turnos de la conversación | Privada; no se comparte con contactos |
| Conocimiento revisado y memorias confirmadas | Búsqueda textual + vectorial | Filtro de autorización dentro de la consulta |

### Memoria

El modelo solo **propone** memorias candidatas. Una candidata se convierte en memoria
confirmada cuando la persona la aprueba (puede editarla antes). Rechazarla la elimina.
En rutas distintas de `normal` no se generan candidatas: un mensaje de crisis no se
convierte en preferencia ni en etiqueta.

Cada memoria tiene `version`. El embedding se guarda en `memory_embeddings` con la
versión que lo originó y se elimina en cascada con la memoria. El trabajo de indexación
comprueba, dentro de una transacción con bloqueo de fila, que la memoria exista y que su
versión coincida; si no, descarta el resultado. Así un trabajo pendiente no recrea datos
borrados.

### RAG

**Ingesta** (`python -m app.cli ingest <manifiesto>`), sin endpoint público:

1. Cada documento declara fuente, responsable, fecha de revisión, versión, territorio,
   estado de aprobación y vigencia.
2. Se normaliza el texto y se divide en fragmentos de aproximadamente 400–700 tokens.
3. Se omiten fragmentos duplicados por hash de contenido.
4. Se generan embeddings y se publica una nueva versión del índice.

El conteo de tokens usa una estimación por caracteres (`len/3,5`). Es conservadora para
español y evita depender de descargas en tiempo de ejecución; es una aproximación.

**Recuperación:**

1. Los filtros se aplican en el `WHERE` de cada búsqueda, antes de ordenar:
   - memorias: `owner_id = :propietario`;
   - documentos y actividades: aprobados, vigentes hoy y del territorio del perfil.
2. Cada colección se consulta por texto (`tsvector` en español) y por vector (coseno).
3. Los rankings se combinan con Reciprocal Rank Fusion (`k = 60`).
4. Se entregan como máximo 5 fragmentos y 2.500 tokens en total.
5. Se registra un `retrieval_run` con identificadores y versiones recuperadas.

Si la recuperación falla, la conversación continúa sin fuentes y sin inventarlas.

**Actividades:** una actividad concreta solo puede aparecer en una propuesta si existe
como registro aprobado y vigente; sus detalles salen del registro, no del texto
generado. Las ideas sin registro se marcan `is_generic = true` y se presentan como
propuestas.

### Contenido recuperado como datos

Los fragmentos se insertan en una sección delimitada del mensaje de entrada, nunca en
las instrucciones del sistema. La defensa no depende de que el modelo obedezca:

- El modelo no puede ejecutar acciones. Solo devuelve texto y borradores.
- Toda acción (aprobar, enviar, confirmar memoria) exige una llamada autenticada del
  propietario a un endpoint específico.
- La validación de salida descarta referencias a actividades, contactos o fuentes que no
  estén en el contexto autorizado de ese propietario.

## 7. Seguridad conversacional

Ver [safety-protocol.md](safety-protocol.md). Resumen:

- Cuatro capas: reglas, moderación del proveedor, clasificador contextual estructurado y
  enrutador determinista.
- Rutas: `normal`, `clarify`, `support`, `urgent`, `unavailable`. No son diagnósticos.
- Políticas, plantillas y recursos de ayuda viven en `config/`, versionados y fuera del RAG.
- Mientras el estado del usuario no sea `normal`: sin propuestas, sin candidatas y con
  envíos rutinarios en pausa.

## 8. Acciones e invitaciones

- Toda propuesta nace como `draft`. La persona aprueba contenido, destinatario y fecha.
- Aprobar exige indicar la `version` vista; editar una propuesta aprobada la devuelve a
  `draft` y cancela la invitación pendiente.
- Un contacto recibe invitaciones solo si aceptó participar (`accepted`).
- Revocar un contacto cancela sus envíos pendientes en la misma transacción.
- Los enlaces de correo llevan un token aleatorio; en la base se guarda solo su hash.
  `GET` es de solo lectura; aceptar o rechazar requiere `POST`.

### Envíos

El worker, antes de cada envío rutinario, vuelve a comprobar dentro de una transacción:
estado de la propuesta y su versión, estado del contacto y estado de seguridad del
propietario. Cada envío tiene una clave de idempotencia única que también se entrega al
proveedor.

| Resultado del proveedor | Tratamiento |
|---|---|
| Aceptado | `sent` |
| Rechazo definitivo (4xx de validación) | `failed`, sin reintento |
| Fallo transitorio antes de entregar (conexión rechazada, 429) | Reintento con espera creciente |
| Ambiguo (timeout tras enviar, 5xx) | `send_uncertain`, **sin reenvío automático** |

Si el worker se reinicia durante un envío, el registro queda en `sending`; al
recuperarlo se marca `send_uncertain` en lugar de reenviar.

## 9. Voz por turnos

`POST /conversations/{id}/audio` (autenticado) → validación de tamaño (≤ 10 MB), formato
y duración reales con `ffprobe` (≤ 120 s) → almacenamiento privado → trabajo
`process_audio` → transcripción → **mismo flujo** de seguridad y conversación →
síntesis opcional. Si la síntesis falla se conserva el texto. Los audios se eliminan
antes de 24 horas (por defecto, 12).

## 10. Cola persistente

Tabla `jobs` con `status`, `run_at`, `attempts`, `locked_at` e `idempotency_key` única.
El worker reclama trabajos con `SELECT … FOR UPDATE SKIP LOCKED`, por lo que varios
workers pueden convivir. Un trabajo `running` cuyo bloqueo expiró se recupera: los
trabajos reintentables vuelven a `queued`; los de envío siguen la regla de ambigüedad.

## 11. Datos y retención

| Dato | Retención |
|---|---|
| Conversaciones y mensajes | 30 días |
| Audios temporales | < 24 horas |
| Preferencias y memorias | Hasta que el propietario las borre |
| Eventos de seguridad (sin texto) | 30 días |

Los logs generales no contienen texto de mensajes. La auditoría registra acción,
entidad y actor, sin contenido.

## 12. Decisiones y motivos

| Decisión | Motivo |
|---|---|
| SQLAlchemy síncrono con `psycopg` 3 | Menos complejidad; FastAPI ejecuta rutas síncronas en un pool de hilos |
| Cola en PostgreSQL | Transacciones compartidas entre negocio y trabajos; sin infraestructura extra |
| Aislamiento por `owner_id` en repositorios | Verificable con pruebas; RLS queda como defensa adicional pendiente |
| Plantillas fijas en rutas de apoyo | El texto de apoyo no depende de un modelo generativo |
| Estimación de tokens por caracteres | Evita descargas en tiempo de ejecución; el límite es aproximado |
| `ffprobe` para validar audio | Validación real de contenedor y duración, no de extensión |
