# Protocolo de seguridad conversacional

Versión de política: `2026-10-01.v1` · Archivo: `config/safety_policy.v1.yaml`

> **Estado: borrador de ingeniería, sin revisión profesional.**
> Antes de cualquier piloto con personas reales, una persona profesional competente en
> salud mental debe revisar reglas, plantillas, recursos y escenarios de prueba.
> La detección es probabilística: **no garantiza identificar todos los casos**.
> Este software no es un servicio de emergencia ni entrega atención clínica.

## 1. Qué significa «stop»

Detener recomendaciones, propuestas, memorias candidatas y envíos rutinarios. Mantener
una respuesta breve, comprensiva y orientada a ayuda humana. El chat no se cierra.

## 2. Evaluación en capas

Cada mensaje (escrito o transcrito) se evalúa antes de cualquier generación:

| Capa | Qué aporta | Límite |
|---|---|---|
| 1. Reglas | Expresiones explícitas, expresiones ambiguas e intentos de saltarse políticas | Palabras clave: sin contexto |
| 2. Moderación del proveedor | Categorías de autolesión marcadas | Sus puntuaciones **no** son probabilidades clínicas; solo se usa la marca por categoría |
| 3. Clasificador contextual | Salida estructurada sobre los turnos recientes: de quién se habla, cuándo, si hay negación e intención | Es un modelo: puede equivocarse |
| 4. Enrutador determinista | Combina las señales y decide la ruta | Código, sin modelo |

El clasificador devuelve: `subject` (`self`, `third_party`, `quote_or_fiction`),
`timeframe` (`present`, `past`, `hypothetical`), `negated`, `intent`
(`none`, `unclear`, `ideation`, `imminent`) y `bypass_attempt`.

## 3. Rutas

| Ruta | Situación | Comportamiento |
|---|---|---|
| `normal` | Sin indicios relevantes | Conversación, RAG y propuestas |
| `clarify` | Expresión ambigua o señales contradictorias | Pausa y pregunta directa y respetuosa |
| `support` | Pensamientos de autolesión sin urgencia establecida | Apoyo, recursos y contacto humano elegido |
| `urgent` | Acción en curso o peligro inmediato declarado | Emergencias y compañía presencial primero |
| `unavailable` | La evaluación falló | Respuesta fija segura, sin acciones normales |

Son rutas del software, **no diagnósticos**.

### Tabla de decisión del enrutador

Se evalúa en orden; gana la primera fila que coincide.

| # | Condición | Ruta |
|---|---|---|
| 1 | Falló el clasificador o la moderación, y las reglas detectan urgencia explícita | `urgent` |
| 2 | Falló el clasificador o la moderación, y las reglas detectan autolesión explícita | `support` |
| 3 | Falló el clasificador o la moderación | `unavailable` |
| 4 | Clasificador: `intent = imminent`, sobre sí misma, presente, sin negación | `urgent` |
| 5 | Clasificador: `intent = ideation`, sobre sí misma, presente, sin negación | `support` |
| 6 | Clasificador: `intent = unclear` | `clarify` |
| 7 | Reglas explícitas o moderación con intención marcada, y el clasificador **no** explica el contexto (no es tercero, cita, pasado, hipótesis ni negación) | `clarify` |
| 8 | Moderación marca intención de autolesión aunque el clasificador explique el contexto | `clarify` |
| 9 | Cualquier otro caso | `normal` |

Las filas 7 y 8 hacen que una discrepancia entre capas se resuelva preguntando, no
ignorando la señal. Tristeza, duelo, una cita, un relato del pasado o la situación de un
tercero no activan apoyo por sí solos.

Un intento de saltarse políticas (`bypass_attempt` o regla equivalente) nunca baja la
ruta ni reanuda el flujo normal.

## 4. Estado y reanudación

El estado es por usuario y solo escala: `clarify` → `support` → `urgent`.

Mientras el estado no sea `normal`:

- No se generan propuestas ni memorias candidatas.
- No se aprueban propuestas (`409 actions_paused`).
- El worker pospone invitaciones y recordatorios de ese usuario.
- Sí se permiten las solicitudes de apoyo aprobadas por la persona.
- Las respuestas de apoyo no se bloquean por cuotas.

La reanudación exige **dos** condiciones:

1. **Reevaluación contextual:** el último mensaje de la persona posterior al evento fue
   evaluado como `normal` por las capas anteriores, con los turnos recientes como contexto.
2. **Decisión explícita:** la persona llama a `POST /support/resume` con `confirm: true`.

Un mensaje que pida ignorar las políticas no cumple ninguna de las dos. Reanudar no
significa que el software declare que un riesgo desapareció.

## 5. Respuestas

Las respuestas de apoyo son **plantillas fijas** versionadas, no texto generado. Cada una:

- reconoce lo que la persona expresó, sin juicio ni culpa;
- pregunta de forma directa cuando hace falta aclarar[^oms];
- orienta hacia ayuda humana y muestra los recursos del país;
- no entrega instrucciones dañinas ni se presenta como terapeuta o servicio de emergencia.

[^oms]: La OMS indica que preguntar por pensamientos suicidas no induce a actuar sobre
    ellos: <https://www.who.int/news-room/questions-and-answers/item/suicide>.

## 6. Recursos de ayuda

Viven en `config/support_resources.v1.yaml`, fuera del RAG. Cada recurso registra
territorio, finalidad, fuente oficial y fecha de verificación. Deben revisarse antes del
piloto y luego mensualmente.

La aplicación puede ofrecer un botón para llamar. «Abrir una llamada» no equivale a
«llamada realizada»: el backend nunca asume que la persona recibió ayuda.

## 7. Contacto elegido

- No hay avisos automáticos a familiares.
- La persona elige un contacto que aceptó participar y que ella marcó para apoyo.
- El agente no propone a un contacto en particular: un familiar podría ser parte del problema.
- La persona revisa el texto y confirma el envío. Solo se comparte ese texto.
- El correo es complementario; **no es atención inmediata** y así se indica.

## 8. Validación de salida

Antes de mostrar una respuesta normal:

1. Moderación del texto generado; si falla o lo marca, se sustituye por un texto fijo.
2. Se descartan propuestas con actividades inexistentes, vencidas o no aprobadas.
3. Se descartan contactos que no sean del propietario o no estén aceptados.
4. Se descartan fuentes que no estuvieran en el contexto recuperado.
5. Se descartan memorias candidatas con expresiones de crisis.

## 9. Fallos

| Fallo | Comportamiento |
|---|---|
| Evaluación de seguridad | Ruta `unavailable` con respuesta fija y recursos |
| RAG | Conversación general sin fuentes |
| Generación | Mensaje fijo de disculpa; sin propuestas |
| Síntesis de voz | Se conserva el texto |
| Transcripción | El trabajo falla con un mensaje claro; nada se evalúa a ciegas |

## 10. Registro

Un evento de seguridad guarda ruta, instante, capas que aportaron señal y versiones de
política y clasificador. No guarda el texto ni puntuaciones. Retención: 30 días.

## 11. Evaluación

`evals/safety_scenarios.yaml` contiene escenarios sintéticos con ruta esperada y la marca
`professional_review: pending`. En CI se prueban las reglas y el enrutador con señales
simuladas; la evaluación con el modelo real es una verificación externa separada.
Aprobar el conjunto **no** garantiza detección universal. Falsos negativos y falsos
positivos se informan por separado.
