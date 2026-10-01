# ADR 0001 — Dispositivo en casa y panel familiar

Fecha: 1 de octubre de 2026 · Estado: aceptado para el MVP del hackathon

## Contexto

El equipo decidió que la persona mayor use el agente desde una pantalla física con un
personaje y un botón, a la que también se llama por su nombre. La familia tendría un
segundo frente para ver en tiempo real, por cámara, cómo está la persona y disponer de
métricas medibles.

Esto entra en tensión con decisiones vigentes del proyecto:

- la persona mayor controla su participación y lo que comparte;
- la conversación es privada y no se comparte con familiares;
- no hay avisos automáticos a la familia;
- el arquetipo interno quiere recibir ayuda **sin perder control**.

Una cámara que la familia puede abrir cuando quiera sería vigilancia, y contradice al
usuario para el que se diseña.

## Decisión

1. **Dispositivo simulado**: una página web que imita la pantalla, con personaje, botón
   virtual y llamada por nombre. Se autentica con un token propio que actúa por la persona.
2. **Panel familiar con consentimiento**:
   - El acceso lo otorga la persona mayor a un contacto que aceptó participar.
   - La cámara se comparte solo mientras ella la mantiene encendida desde el dispositivo.
   - La pantalla muestra una luz, un aviso y el nombre de quien está mirando.
   - La imagen se transmite en vivo y no se guarda.
3. **Métricas de actividad, no de intimidad**: conteos y marcas de tiempo. Sin
   conversaciones, sin memorias, sin estado de seguridad.
4. **Sin inferencia de emociones ni de salud** a partir de la cámara.

## Consecuencias

- La familia no puede «encender» la cámara a distancia. Si se quisiera lo contrario,
  habría que revisar esta decisión con personas del segmento 60–75.
- Las métricas son más modestas que un «estado emocional», pero cada una es verificable.
- La presencia solo se mide con la cámara encendida: con la cámara apagada el panel
  muestra menos.
- El relevo de video vive en memoria: una sola instancia de la API.

## Hipótesis que esta decisión no valida

- `HIPÓTESIS` Que una persona mayor autovalente quiera una cámara en su casa.
- `HIPÓTESIS` Que la familia pague por un panel de actividad.
- `HIPÓTESIS` Que ver actividad acerque a la familia en vez de reemplazar el contacto.

Ninguna tiene evidencia propia todavía `[POR VERIFICAR]`. La demo muestra que el
mecanismo funciona, no que resuelva la falta de conexiones significativas.

## Pendiente de decisión del equipo

- ¿Debe la familia recibir alguna señal cuando la persona entra en una ruta de apoyo?
  Hoy no: el contacto solo se entera si ella aprueba un mensaje.
- Nombre definitivo del dispositivo. «Silvia» es un valor de demostración.
