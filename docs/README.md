# Documentación

Por dónde empezar según lo que buscas.

## Quiero entender el producto

1. [README](../README.md): qué es, cómo se usa y qué está verificado.
2. [Demostración](demo.md): guion de los dos recorridos, conexión y apoyo.
3. [ADR 0001](adr/0001-dispositivo-y-panel-familiar.md): por qué la cámara depende del
   consentimiento de la persona mayor y qué hipótesis siguen sin validar.

## Quiero entender cómo está construido

1. [Arquitectura](architecture.md): procesos, capas, memoria, RAG, credenciales,
   transmisión en vivo y decisiones con sus motivos.
2. [Entidades y estados](entities-and-states.md): tablas y máquinas de estado.
3. [Contratos de API](api-contracts.md): endpoints, credenciales y códigos de error. El
   contrato ejecutable es OpenAPI en `/docs` con la API en marcha.

## Quiero revisar la seguridad conversacional

1. [Protocolo de seguridad](safety-protocol.md): capas, tabla de decisión, reanudación,
   recursos de ayuda y fallos.
2. [config/safety_policy.v1.yaml](../config/safety_policy.v1.yaml): reglas y plantillas.
3. [config/support_resources.v1.yaml](../config/support_resources.v1.yaml): teléfonos,
   con su fuente oficial y fecha de verificación.
4. [evals/safety_scenarios.yaml](../evals/safety_scenarios.yaml): 52 escenarios sintéticos.

El protocolo es un borrador de ingeniería. Necesita revisión profesional antes de usarse
con personas reales.

## Quiero ejecutarlo o desplegarlo

1. [README, «Abrirlo en tu computador»](../README.md#abrirlo-en-tu-computador).
2. [Despliegue](deployment.md): Supabase, Render, Resend y modelos. Nada está desplegado.

## Quiero saber qué falta

[Plan y estado real](../Silver_Minds_Backend_Plan.md): qué está hecho, qué está solo
simulado, qué no se pudo verificar y qué depende de terceros.

## Convenciones

- Los documentos distinguen evidencia, interpretación e hipótesis. Lo no confirmado se
  marca `[POR VERIFICAR]` y los supuestos de diseño, `HIPÓTESIS`.
- Una integración solo se declara verificada si se ejecutó contra el servicio real.
- Todo dato de demostración es ficticio y está rotulado como tal.
- Las capturas de `images/` se tomaron del MVP en ejecución, con datos ficticios.
