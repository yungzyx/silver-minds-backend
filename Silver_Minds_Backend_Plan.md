# Silver Minds — plan del backend y estado real

Actualizado: 1 de octubre de 2026

Este archivo registra qué está implementado y verificado, qué está solo simulado y qué
depende de terceros. No describe intenciones como si fueran hechos.

## Estado por fase

| Fase | Entregable | Rama | Estado |
|---|---|---|---|
| 0 | Arquitectura, entidades, estados, contratos y protocolo | `codex/architecture` | Hecho |
| 1 | Base FastAPI, Docker, dependencias, pruebas y CI | `codex/backend-foundation` | Hecho. CI en verde en GitHub. Docker **sin verificar** |
| 2 | Autenticación, migraciones y aislamiento | `codex/auth-and-data` | Hecho |
| 3 | Memoria, preferencias y contactos; cola y worker | `codex/confirmed-memory` | Hecho |
| 4 | Seguridad conversacional | `codex/conversation-safety` | Hecho. **Sin revisión profesional** |
| 5 | RAG: ingesta y recuperación | `codex/rag-retrieval` | Hecho con embeddings simulados |
| 6 | Agente contextual | `codex/contextual-agent` | Hecho con generación simulada |
| 7 | Invitaciones y recordatorios | `codex/approved-actions` | Hecho con correo simulado |
| — | Dispositivo en casa y panel familiar (pivote) | `codex/device-and-family` | Hecho. Cámara real **sin verificar por el autor** |
| 8 | Voz por turnos | `codex/voice-messages` | Hecho con transcripción y síntesis simuladas |
| 9 | Evaluaciones | `codex/evaluations` | Hecho con señales simuladas |
| 10 | Despliegue | `codex/deployment` | Configuración preparada. **No desplegado** |

Cada fase se integró a `main` con un merge propio. No hubo pull requests: las ramas se
integraron en local antes de crear el repositorio remoto.

## Verificado localmente

- Pruebas con PostgreSQL 17 real y pgvector 0.8.6; la suite corre también en CI.
- Migraciones aplicadas desde cero en cada ejecución de pruebas.
- Dispositivo y panel familiar en el navegador: llamada por nombre mediante el simulador
  de texto, propuesta, aprobación, invitación y métricas del panel.
- Transmisión en vivo de extremo a extremo con cuadros sintéticos.
- Evaluación de RAG: 30 de 30 consultas con una fuente esperada entre las cinco primeras,
  sobre el corpus de demostración y con embeddings simulados.
- 52 escenarios sintéticos de seguridad contra las reglas y el enrutador, con señales
  simuladas.

## Simulado: no cuenta como integración completa

| Integración | Qué existe | Qué falta |
|---|---|---|
| OpenAI Responses, embeddings, moderación, transcripción y voz | Adaptador probado con un cliente sustituto | Ejecutarlo con una clave real |
| Clasificador contextual de seguridad | Prompt versionado y esquema estricto | Evaluarlo en vivo: `eval-safety --live` |
| Resend | Adaptador probado con transporte simulado | Clave y dominio verificado |
| Supabase Auth | Verificación ES256 mediante JWKS probada con claves locales | Proyecto real |
| Supabase Storage | Adaptador probado con transporte simulado | Proyecto y bucket |
| Supabase PostgreSQL | Migraciones probadas en PostgreSQL local | Aplicarlas en Supabase |
| Render | `render.yaml` | Cuenta y autorización de gasto |
| Docker y Docker Compose | Archivos escritos | Construir y ejecutar: no hay Docker en la máquina |

## Sin verificar por el autor

- Cámara y micrófono reales en el dispositivo: el navegador de desarrollo los bloquea.
  El código usa `getUserMedia` y la Web Speech API; hay que probarlo en Chrome.
- El comportamiento del modelo real ante los escenarios de seguridad.
- La calidad de recuperación con embeddings reales.

## Dependencias externas pendientes

1. Revisión profesional del protocolo de apoyo, plantillas y escenarios.
2. Credenciales de OpenAI, Resend y Supabase.
3. Cuenta de Render y decisión de gasto.
4. Conocimiento revisado que reemplace al corpus de demostración.
5. Entrevistas con personas de 60 a 75 años sobre la cámara y el panel.

## Límites conocidos del MVP

- Sin límite de peticiones por cliente.
- Aislamiento entre usuarios en la capa de aplicación; sin RLS.
- La transmisión de cámara exige una sola instancia de la API.
- El conteo de tokens es una estimación por caracteres.
- La transcripción no entrega una medida de certeza: un audio mal transcrito sigue el
  mismo flujo que el texto, sin un tratamiento especial.
- Si el clasificador atribuye la expresión a un tercero, la conversación sigue normal y
  no se ofrecen recursos para ese tercero.
- Una invitación ya enviada no se puede retirar al editar o cancelar la propuesta.
- El reconocimiento de voz del navegador envía el audio al proveedor del navegador.

## Documentos

- [README](README.md)
- [docs/architecture.md](docs/architecture.md)
- [docs/entities-and-states.md](docs/entities-and-states.md)
- [docs/api-contracts.md](docs/api-contracts.md)
- [docs/safety-protocol.md](docs/safety-protocol.md)
- [docs/adr/0001-dispositivo-y-panel-familiar.md](docs/adr/0001-dispositivo-y-panel-familiar.md)
- [docs/demo.md](docs/demo.md)
- [docs/deployment.md](docs/deployment.md)
