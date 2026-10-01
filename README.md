# Silver Minds — backend

Backend del MVP de Silver Minds (Hack4Seniors UDD): un agente que conversa con una
persona mayor autovalente, recuerda preferencias confirmadas, propone actividades
significativas y facilita invitaciones aprobadas a familiares o amigos.

La persona mayor conserva el control. La efectividad sobre soledad o comprensión
**no está demostrada**.

## Árbol del proyecto

```text
app/
  api/            Ensamblado de rutas /api/v1
  core/           Configuración, base de datos, autenticación y errores
  models/         Modelos SQLAlchemy
  modules/        Módulos de negocio (rutas, servicios y repositorios)
    profiles/     Perfil y preferencias
    memory/       Memorias candidatas y confirmadas
    contacts/     Contactos y aceptación
    conversations/ Conversaciones y agente contextual
    rag/          Ingesta y recuperación
    safety/       Seguridad conversacional
    actions/      Propuestas, invitaciones y actividades
    voice/        Audios por turnos
    followup/     Recordatorios, seguimiento y auditoría
  integrations/   Adaptadores: IA, correo y almacenamiento
  worker/         Cola persistente y proceso worker
config/           Políticas, recursos de ayuda y prompts versionados
migrations/       Migraciones Alembic
data/             Conocimiento y datos ficticios de demostración
evals/            Evaluaciones de RAG y de seguridad
tests/            Pruebas unitarias y de integración
docs/             Arquitectura, contratos y protocolo
```

La documentación de instalación se completa en la fase 1.
