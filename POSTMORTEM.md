# Post-mortem: ¿Es timo? (Mikiwame)

*Cerrado el 15-11-2026. Seis semanas de vida (bot desde el 01-10, web desde el 14-10). Un proyecto
de Sukima Labs en forma de concepto; el código queda en este repositorio con licencia MIT.*

## Qué se construyó

Un servicio casi pasivo: recibía un anuncio de alquiler y el chat con el «propietario» (texto o
capturas) y devolvía un informe de riesgo con semáforo verde / ámbar / rojo, el porqué de cada
señal y qué comprobar antes de pagar. Por dentro:

- Un modelo leía las capturas, otro el texto, y un tercer paso («adversarial») intentaba tumbar el
  veredicto preliminar antes de darlo. Votantes independientes (teléfono, IBAN, texto, gastos al
  inquilino, calle, precio frente al mercado, reincidencia) con reglas duras aparte de las
  puntuaciones.
- Un registro de reincidencia con huellas HMAC de teléfonos, IBAN, correos y nombres, y pHash de
  fotos, para decir «estos datos ya salieron en rojo» sin guardar datos en claro.
- Una política de modelos con la privacidad por delante (registro con lista de proveedores
  permitidos y topes de gasto por modelo) y un banco de evaluación con 37 casos inventados.
- Bot de Telegram (ES/EN), web, guías para buscadores, seguimiento a los 5-7 días («¿qué pasó al
  final?») y un radar de anuncios sospechosos sobre una copia del mercado (no incluido en este
  repositorio: dependía de datos de otro servicio).

Unas 10.000 líneas de Python, 244 commits entre el 30-09 y el 12-11.

## Qué medimos

| | Resultado |
|---|---|
| Coste por informe | ~0,0004 € de media (objetivo: < 0,05 €). Todo el gasto de producción y pruebas: ~0,04 $ en 82 llamadas; las evaluaciones, ~3 $ en 1.613 pasadas |
| Último banco (74 pasadas, 37 casos) | legítimos en rojo: 0 · precisión del rojo: 1,0 · recall del rojo: 0,825 · recall de señales: 0,963 · latencia media: 18,6 s |
| Uso real | 2 análisis en producción (web el día de la apertura; Telegram el 06-11); 4 personas escribieron al bot; ningún seguimiento respondido |
| Search Console (14-25 oct) | 33 impresiones, 1 clic, casi todo búsquedas en inglés sobre villas |

**Cautela sobre la precisión:** los 37 casos los escribimos nosotros. Con 0 rojos en *n* legítimos
confirmados, la tasa de falsos rojos queda por debajo de 3/*n* al 95 %: haría falta confirmar ~100
legítimos reales para bajar del 3 %. Llegamos a cero. La cifra de arriba no demuestra nada sobre
anuncios reales.

## Por qué no llegó a nadie

Es una herramienta de un momento muy concreto (justo antes de pagar) y los canales que probamos no
llegan a ese momento:

- Grupos de vivienda: en tres de cuatro la cuenta quedó silenciada o fuera; un antispam borró un
  aviso que el administrador había aprobado.
- Correos a quien atiende a quien busca piso (bolsa de vivienda y relaciones internacionales de la
  universidad, OMIC): una negativa, el resto sin respuesta.
- Un canal propio de avisos y siete guías por idioma: las guías son lo único que Search Console
  enseña.
- Los portales ya tienen su equipo antifraude y su botón de denuncia; no podíamos competir con
  eso, solo cubrir la conversación fuera del portal.

## Qué haríamos distinto

1. **Distribución antes que motor.** Construimos un motor cuidado (y barato) para un público que no
   teníamos. La pregunta «¿dónde está la persona justo antes de pagar?» se debió contestar con una
   página y un enlace, no con tres modelos.
2. **La métrica que manda necesita datos que no controlas.** Precisión del rojo sobre casos
   propios es fácil de mantener alta; el número que importa necesitaba legítimos confirmados.
3. **El coste nunca fue el problema.** Con modelos pequeños y un solo paso adversarial salía a una
   centésima del objetivo; el tope compartido de la casa no llegó a notarse.

## Qué sí funcionó (y se puede reutilizar)

- Separar reglas duras de puntuaciones, y señales informativas que no mueven el veredicto.
- El paso adversarial, medido aparte con veredictos preliminares conocidos.
- Redacción y HMAC desde el primer día: ningún dato de terceros en claro, retención de 30 días y
  una ficha anónima por caso.
- El banco de evaluación con señales esperadas y no deseadas, y un caso de inyección de
  instrucciones en el anuncio.
- Posible reutilización: el motor de semáforo como filtro para una búsqueda de piso propia.
