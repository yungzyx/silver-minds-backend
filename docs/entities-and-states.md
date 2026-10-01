# Entidades y estados

Versión 1 · 1 de octubre de 2026

Todas las tablas usan `uuid` como clave primaria y marcas de tiempo con zona horaria.
`owner_id` siempre referencia `profiles.id`, que coincide con el `sub` del JWT de Supabase.

## Entidades

### Identidad y preferencias

| Tabla | Campos principales | Notas |
|---|---|---|
| `profiles` | `id`, `email`, `preferred_name`, `language`, `timezone`, `country`, `voice_replies` | Se crea en la primera petición autenticada |
| `preferences` | `owner_id`, `category`, `value` | Declaradas por la persona. Consulta exacta |
| `contacts` | `owner_id`, `name`, `email`, `relationship`, `status`, `support_opt_in`, `consent_token_hash`, `consent_expires_at` | Único por (`owner_id`, `email`) entre contactos no revocados |

Categorías de preferencia y memoria: `interest`, `activity`, `schedule`, `limit`,
`support_style`, `communication`.

### Conversación y memoria

| Tabla | Campos principales | Notas |
|---|---|---|
| `conversations` | `owner_id`, `title` | |
| `messages` | `conversation_id`, `owner_id`, `role`, `content`, `mode`, `source`, `client_message_id`, `payload` | `payload` guarda la respuesta estructurada para reintentos idempotentes |
| `memory_candidates` | `owner_id`, `conversation_id`, `category`, `content`, `status` | Rechazar elimina la fila |
| `confirmed_memories` | `owner_id`, `category`, `content`, `version` | Incluye `tsvector` en español |
| `memory_embeddings` | `memory_id` (PK, FK en cascada), `owner_id`, `embedding`, `memory_version` | Se borra junto con la memoria |

### Conocimiento

| Tabla | Campos principales | Notas |
|---|---|---|
| `knowledge_documents` | `collection`, `title`, `source`, `responsible`, `reviewed_at`, `version`, `territory`, `approval_status`, `valid_from`, `valid_until`, `content_hash` | |
| `knowledge_chunks` | `document_id`, `chunk_index`, `content`, `token_count`, `content_hash`, `embedding`, `tsv` | |
| `activities` | `title`, `description`, `location`, `schedule_text`, `cost`, `requirements`, `accessibility`, `territory`, `source`, `approval_status`, `valid_from`, `valid_until`, `embedding`, `tsv` | Registro estructurado |
| `index_state` | `version` | Versión publicada del índice |
| `retrieval_runs` | `owner_id`, `conversation_id`, `index_version`, `items`, `token_total` | `items`: tipo, id y versión de cada fragmento |

### Seguridad

| Tabla | Campos principales | Notas |
|---|---|---|
| `safety_states` | `owner_id` (PK), `mode`, `since`, `last_route`, `policy_version` | Estado por usuario |
| `safety_events` | `owner_id`, `conversation_id`, `route`, `layers`, `classifier_version`, `policy_version` | **Sin texto del mensaje ni puntuaciones** |
| `support_requests` | `owner_id`, `contact_id`, `message`, `status`, `idempotency_key` | Mensaje aprobado por la persona |

Los recursos de ayuda y las plantillas del protocolo **no** son tablas: están en
`config/`, versionados con el código.

### Acciones

| Tabla | Campos principales | Notas |
|---|---|---|
| `proposals` | `owner_id`, `conversation_id`, `kind`, `title`, `body`, `activity_id`, `is_generic`, `contact_id`, `scheduled_for`, `status`, `version`, `approved_version` | |
| `invitations` | `owner_id`, `proposal_id`, `proposal_version`, `contact_id`, `status`, `token_hash`, `token_expires_at`, `idempotency_key`, `provider_message_id` | |
| `reminders` | `owner_id`, `proposal_id`, `kind`, `due_at`, `status` | `kind`: `reminder`, `followup` |
| `activity_feedback` | `owner_id`, `proposal_id`, `happened`, `rating`, `comment` | |

### Operación

| Tabla | Campos principales | Notas |
|---|---|---|
| `jobs` | `kind`, `payload`, `status`, `run_at`, `attempts`, `max_attempts`, `locked_at`, `idempotency_key`, `owner_id`, `result`, `last_error` | Cola persistente |
| `audio_uploads` | `owner_id`, `conversation_id`, `storage_path`, `content_type`, `duration_seconds`, `size_bytes`, `status`, `reply_audio_path`, `expires_at` | |
| `audit_events` | `owner_id`, `actor`, `action`, `entity_type`, `entity_id` | Sin contenido |

## Máquinas de estado

### Contacto

```text
pending ──aceptar (POST con token)──▶ accepted ──revocar──▶ revoked
   │──rechazar (POST con token)────▶ declined
   │──revocar──────────────────────▶ revoked
   └──token vencido: no cambia de estado; el POST responde 410
```

Solo `accepted` puede recibir invitaciones. `revoked` y `declined` son terminales.

### Memoria candidata

```text
pending ──confirmar (con edición opcional)──▶ confirmed  (crea memoria confirmada)
   └─────rechazar──────────────────────────▶ fila eliminada
```

### Memoria confirmada

```text
activa (version n) ──editar──▶ activa (version n+1, se reindexa)
        └──────────borrar──▶ eliminada (junto con su embedding)
```

### Propuesta

```text
draft ──aprobar(version)──▶ approved ──feedback──▶ completed
  │                            │──editar──▶ draft (version+1, cancela invitación)
  │                            └──cancelar──▶ cancelled
  └──rechazar──▶ rejected
```

Aprobar responde `409` si la `version` no coincide o si las acciones están en pausa.

### Invitación

```text
queued ──worker──▶ sending ──proveedor acepta──▶ sent ──POST token──▶ accepted | declined
   │                  │──ambiguo──▶ send_uncertain      └─token vencido: 410
   │                  └──rechazo definitivo──▶ failed
   └──revocar contacto / editar o cancelar propuesta──▶ cancelled
```

Mientras el estado de seguridad no sea `normal`, el trabajo se pospone y la invitación
permanece en `queued`.

### Estado de seguridad (por usuario)

```text
normal ──mensaje evaluado──▶ clarify ─▶ support ─▶ urgent   (solo escala)
   ▲                                                  │
   └──── reanudar: última evaluación normal + decisión explícita ◀──┘
```

`unavailable` es la ruta de un mensaje cuya evaluación falló; no es un estado persistente.

### Trabajo

```text
queued ──▶ running ──▶ succeeded
              │──error reintentable──▶ queued (run_at posterior)
              │──sin reintentos──▶ failed
              └──bloqueo vencido──▶ queued | regla de ambigüedad si es un envío
queued ──▶ cancelled
```

### Audio

```text
uploaded ──▶ processing ──▶ done ──limpieza──▶ deleted
                 └────────▶ failed ──limpieza──▶ deleted
```

### Solicitud de apoyo

```text
draft ──aprobar──▶ approved ──worker──▶ sent | send_uncertain | failed
  └──cancelar──▶ cancelled
```

Las solicitudes de apoyo no se pausan por el estado de seguridad ni por cuotas.
