# Contratos de API

Versión 1 · Prefijo `/api/v1` · El contrato ejecutable es OpenAPI en `/docs` y `/openapi.json`.

## Convenciones

- **Autenticación:** tres credenciales en la cabecera `Authorization`:
  - `Bearer <JWT de Supabase Auth>`: la cuenta de la persona mayor. Alcance completo.
  - `Device <token>`: su dispositivo en casa. Vale en todo lo marcado *cuenta o
    dispositivo* (por defecto), no en lo marcado *solo cuenta*.
  - `Viewer <token>`: un contacto con acceso al panel. Solo `/family/*`.
- Los endpoints marcados como *públicos* se autorizan con un token de un solo propósito
  incluido en el enlace del correo.
- **Listas:** `{"items": [...], "total": n}`.
- **Errores:** `{"error": {"code": "…", "message": "…"}}`.
- Un recurso de otro usuario responde `404`, igual que uno inexistente.

| Código | Uso |
|---|---|
| `401 unauthenticated` | Falta el token o no es válido |
| `404 not_found` | No existe o no pertenece al propietario |
| `409 conflict` | Estado o versión incompatibles |
| `409 actions_paused` | Acción rutinaria durante una ruta de apoyo |
| `410 token_expired` | Token de enlace vencido |
| `413 payload_too_large` | Audio mayor que 10 MB |
| `422 validation_error` | Entrada inválida |
| `429 quota_exceeded` | Cuota diaria de mensajes normales agotada |

## Salud

| Método y ruta | Descripción |
|---|---|
| `GET /health` | Proceso vivo. Público |
| `GET /health/ready` | Base de datos, `pgvector` y tipo de adaptadores activos (real o simulado). Público |

## Perfil y preferencias

| Método y ruta | Descripción |
|---|---|
| `GET /profile` | Perfil del propietario |
| `PATCH /profile` | `preferred_name`, `language`, `timezone`, `country`, `voice_replies` |
| `GET /preferences` | Lista de preferencias |
| `POST /preferences` | `{category, value}` |
| `PATCH /preferences/{id}` | Edita `category` o `value` |
| `DELETE /preferences/{id}` | Borra |

## Contactos y aceptación

| Método y ruta | Descripción |
|---|---|
| `GET /contacts` | Lista de contactos |
| `POST /contacts` | `{name, email, relationship, support_opt_in}` → `pending` y correo de aceptación |
| `PATCH /contacts/{id}` | `name`, `relationship`, `support_opt_in` |
| `POST /contacts/{id}/revoke` | Revoca y cancela envíos pendientes |
| `GET /contact-consents/{token}` | *Público.* Solo lectura: quién invita y estado |
| `POST /contact-consents/{token}` | *Público.* `{decision: "accept" \| "decline"}` |

## Conversaciones y mensajes

| Método y ruta | Descripción |
|---|---|
| `POST /conversations` | Crea una conversación |
| `GET /conversations` | Lista |
| `GET /conversations/{id}/messages` | Historial |
| `POST /conversations/{id}/messages` | Envía un mensaje y devuelve la respuesta del agente |
| `DELETE /conversations/{id}` | Borra la conversación y sus mensajes |

### Respuesta conversacional

Petición: `{"content": "…", "client_message_id": "opcional, para reintentos idempotentes"}`

```json
{
  "message_id": "uuid",
  "reply": "texto para la persona",
  "mode": "normal | clarify | support | urgent | unavailable",
  "proposals": [
    {"id": "uuid", "kind": "activity", "title": "…", "body": "…", "status": "draft",
     "version": 1, "is_generic": false, "activity_id": "uuid", "contact_id": null,
     "scheduled_for": null}
  ],
  "memory_candidates": [
    {"id": "uuid", "category": "schedule", "content": "…", "status": "pending"}
  ],
  "source_refs": [
    {"type": "activity | knowledge | memory", "id": "uuid", "title": "…", "version": "…"}
  ],
  "support_options": {
    "resources": [{"id": "…", "name": "…", "phone": "…", "purpose": "…"}],
    "contacts": [{"id": "uuid", "name": "…"}],
    "can_resume": false
  }
}
```

Reglas del contrato:

- Si `mode` no es `normal`, `proposals` y `memory_candidates` están vacíos.
- `support_options` es `null` cuando `mode` es `normal` y el usuario no está en pausa.
- La respuesta **no** incluye puntuaciones ni categorías internas de seguridad.
- `support_options.contacts` solo lista contactos aceptados que la persona marcó con
  `support_opt_in`. El agente no elige ni recomienda a uno en particular.

## Audios y trabajos

| Método y ruta | Descripción |
|---|---|
| `POST /conversations/{id}/audio` | `multipart/form-data` con `file` y `speak_reply`. Responde `202 {audio_id, job_id, status}` |
| `GET /jobs/{id}` | Estado y resultado del trabajo del propietario |
| `GET /audio/{audio_id}/reply` | Audio sintetizado, si existe |

Límites: 10 MB y 120 segundos, verificados sobre el contenido real del archivo.
El `result` de un trabajo `process_audio` terminado es una respuesta conversacional
más `transcript` y `reply_audio_available`.

## Memoria

| Método y ruta | Descripción |
|---|---|
| `GET /memory-candidates` | Candidatas pendientes |
| `PATCH /memory-candidates/{id}` | Edita antes de confirmar |
| `POST /memory-candidates/{id}/confirm` | Crea la memoria confirmada |
| `POST /memory-candidates/{id}/reject` | Elimina la candidata. `204` |
| `GET /memories` | Memorias confirmadas |
| `PATCH /memories/{id}` | Edita; incrementa versión y reindexa |
| `DELETE /memories/{id}` | Borra memoria y embedding. `204` |

## Propuestas, invitaciones y actividades

| Método y ruta | Descripción |
|---|---|
| `GET /proposals` | Filtro opcional `status` |
| `GET /proposals/{id}` | Detalle |
| `PATCH /proposals/{id}` | `title`, `body`, `contact_id`, `scheduled_for`. Si estaba aprobada vuelve a `draft` |
| `POST /proposals/{id}/approve` | `{version}`. Crea la invitación si hay contacto y programa recordatorios |
| `POST /proposals/{id}/reject` | Rechaza un borrador |
| `POST /proposals/{id}/cancel` | Cancela una aprobada y sus envíos pendientes |
| `POST /proposals/{id}/feedback` | `{happened, rating, comment}` |
| `GET /invitations` | Invitaciones del propietario |
| `GET /invitation-responses/{token}` | *Público.* Solo lectura |
| `POST /invitation-responses/{token}` | *Público.* `{response: "accept" \| "decline"}` |
| `GET /activities` | Actividades aprobadas y vigentes |
| `GET /activities/{id}` | Detalle |
| `GET /reminders` | Recordatorios y seguimientos del propietario |

## Apoyo

| Método y ruta | Descripción |
|---|---|
| `GET /support-resources` | *Público.* Recursos verificados por país (`?country=CL`) |
| `GET /support/state` | `{mode, can_resume}` |
| `POST /support/resume` | `{confirm: true}`. Reanuda si la última evaluación fue `normal` |
| `GET /support-requests` | Solicitudes del propietario |
| `POST /support-requests` | `{contact_id, message}` → `draft` |
| `PATCH /support-requests/{id}` | Edita el mensaje del borrador |
| `POST /support-requests/{id}/approve` | Aprueba y encola el envío |
| `POST /support-requests/{id}/cancel` | Cancela un borrador |

Una solicitud de apoyo comparte **únicamente** el texto aprobado. No adjunta
conversación, historial ni clasificación interna.

## Dispositivo

| Método y ruta | Descripción |
|---|---|
| `POST /devices` | *Solo cuenta.* `{name}` → dispositivo y su token, que se muestra una vez |
| `GET /devices` | *Solo cuenta.* Lista |
| `POST /devices/{id}/revoke` | *Solo cuenta.* Revoca y corta la transmisión |
| `GET /device/session` | *Dispositivo.* Nombre, cámara, quién mira, modo y pendientes. Sirve de latido |
| `POST /device/events` | *Dispositivo.* `{kind: wake_button \| wake_name \| presence, value?}`. La presencia solo se acepta con la cámara encendida |
| `POST /device/speech` | *Dispositivo.* `{text}` → audio. `503 voice_unavailable` o `429 voice_quota_exceeded` hacen que el dispositivo use la voz del navegador |
| `WS /device/stream` | *Dispositivo.* Primer mensaje `{type: "auth", token}`. Luego `{type: "camera", enabled}` y cuadros JPEG |

## Panel familiar

| Método y ruta | Descripción |
|---|---|
| `POST /family-access` | *Solo cuenta.* `{contact_id, can_view_camera}`. El contacto debe estar aceptado; recibe un enlace personal |
| `GET /family-access` | *Solo cuenta.* Lista |
| `PATCH /family-access/{id}` | *Solo cuenta.* `{can_view_camera}` |
| `POST /family-access/{id}/revoke` | *Solo cuenta.* Quita el acceso y corta la transmisión en curso |
| `GET /family/overview` | *Acceso familiar.* Señales de actividad |
| `WS /family/stream` | *Acceso familiar con permiso de cámara.* Recibe estado y cuadros mientras la cámara está encendida |

`GET /family/overview` devuelve `device`, `camera`, `today`, `week`, `series` (siete días)
y `timeline`. No incluye conversaciones, memorias ni el estado de seguridad.

Códigos de cierre de WebSocket: `4401` credencial inválida, `4403` sin permiso o revocado.

## Páginas

| Ruta | Descripción |
|---|---|
| `/device/` | Dispositivo simulado. Lee el token de `#token=` |
| `/family/` | Panel familiar. Lee el token de `#token=` |
