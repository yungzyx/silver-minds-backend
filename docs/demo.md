# Demostración

Todo lo que aparece en la demo es ficticio: Rosa, Camila, «Comuna Demo» y sus
actividades no existen. Con `AI_PROVIDER=fake` las respuestas vienen de un simulador
determinista, no de un modelo.

## Preparación

1. API y worker en ejecución (ver [README](../README.md)).
2. `uv run python -m app.cli demo-setup` imprime dos enlaces.
3. Abre el del **dispositivo** en una ventana de Chrome y el del **panel familiar** en otra.
4. En el dispositivo toca «Toca para encender» y acepta el permiso de micrófono.

Si el micrófono no está disponible, el campo «Simular lo que dice la persona» hace lo
mismo que la voz.

## Recorrido 1: conexión

| Paso | En el dispositivo | Qué demuestra |
|---|---|---|
| 1 | Di «Silvia» o toca **Llamar** | Llamada por nombre o por botón; el panel registra cuál |
| 2 | «Prefiero las mañanas» | El agente **propone** recordar; nada se guarda todavía |
| 3 | «Sí, recuérdalo» | Memoria confirmada por la persona |
| 4 | «Silvia, quiero invitar a Camila a una actividad con plantas» | Propuesta basada en una actividad vigente del catálogo y en su memoria |
| 5 | «Sí, invitar a Camila» | Aprobación explícita de contenido y destinatario |
| 6 | — | El worker envía el correo (simulado en `var/outbox/`) |
| 7 | Panel familiar | Aparece «Te envió una invitación» y suben las métricas |

Para cerrar el ciclo, Camila abre el enlace del correo: `GET` solo muestra la invitación;
aceptar requiere `POST`. El seguimiento queda pendiente hasta que Rosa cuenta cómo resultó
(`POST /proposals/{id}/feedback`).

### Cámara

| Paso | Acción | Qué demuestra |
|---|---|---|
| 1 | Panel familiar | «Rosa tiene la cámara apagada» |
| 2 | En el dispositivo, activa **Cámara** o di «Silvia, comparte la cámara» | Decide la persona mayor |
| 3 | Dispositivo | Luz roja, aviso «Cámara compartida» y «Camila está mirando» |
| 4 | Panel familiar | Imagen en vivo; empiezan a contarse minutos con movimiento |
| 5 | «Silvia, apaga la cámara» | La transmisión se corta de inmediato |

## Recorrido 2: apoyo

| Paso | En el dispositivo | Qué demuestra |
|---|---|---|
| 1 | Llama y di una frase preocupante de prueba | La evaluación de seguridad ocurre antes de cualquier recomendación |
| 2 | — | La pantalla cambia: sin propuestas, con respuesta fija y los teléfonos 131 y \*4141 |
| 3 | Pide una actividad | Sigue en pausa: no hay propuestas |
| 4 | «Escribirle a Camila» | La persona elige el contacto; el agente no recomienda a nadie |
| 5 | Revisa el texto y toca **Enviar** | Solo sale el texto aprobado, sin conversación ni clasificación |
| 6 | Cuenta que estás mejor y toca **Retomar** | Reanudación: reevaluación favorable + decisión explícita |
| 7 | Panel familiar | No muestra nada de este recorrido |

Los mensajes de prueba deben ser **ficticios**. El protocolo es un borrador de ingeniería
sin revisión profesional y la detección no garantiza identificar todos los casos.

## La misma demo por API

```bash
TOKEN=$(uv run python -m app.cli dev-token --email rosa@example.com)
```

```bash
curl -s -X POST localhost:8000/api/v1/conversations -H "Authorization: Bearer $TOKEN"
```

Con el `id` devuelto:

```bash
curl -s -X POST localhost:8000/api/v1/conversations/<id>/messages \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"content": "Me gustaría una actividad con plantas"}'
```

La respuesta trae `reply`, `mode`, `proposals`, `memory_candidates`, `source_refs` y
`support_options`. El resto de los endpoints está en `/docs`.

## Qué no demuestra

- Que el producto reduzca la soledad o mejore la comprensión.
- El comportamiento del modelo real: con el simulador las respuestas son plantillas.
- El envío real de correos ni la transcripción real de audio.
