"""Everything the bot says, in Spanish (Spain)."""

HELP = """Hola, soy <b>Mikiwame</b>. Te ayudo a detectar estafas en anuncios de alquiler \
<b>antes</b> de que pagues nada.

Mándame lo que tengas, mejor si empiezas por el enlace del anuncio:
• el enlace (o el texto) del anuncio,
• capturas del chat con el anunciante (WhatsApp, Telegram, el chat del portal),
• las fotos de la vivienda que te hayan mandado: las comparo con otros anuncios,
• el teléfono, el IBAN o el nombre que te hayan dado.

Cuando lo tengas todo, pulsa <b>Analizar</b>. Te devuelvo un semáforo 🟢 🟠 🔴 con el porqué y \
qué comprobar. ¿Aún no tienes un caso? Mira un ejemplo: /ejemplo

/nuevo empieza otro análisis · /privacidad qué guardo · /borrar borra tus datos · \
/invitar invita a alguien · /english in English"""

PRIVACY = """<b>Qué guardo y durante cuánto tiempo</b>
• Las capturas no se guardan: se leen para el análisis y se descartan.
• El texto del caso y el informe se guardan {days} días y luego se borran.
• Teléfonos, IBAN y nombres del anunciante se guardan solo como huella irreversible (HMAC), \
para reconocer anuncios que se repiten.
• El análisis lo hace un modelo de IA de OpenAI, a través de OpenCode. No entrena con estos \
datos, pero OpenAI guarda hasta 30 días un registro de las peticiones para detectar abusos.
• Para comprobar que la calle del anuncio existe, consulto el nombre de la calle y el municipio \
(sin número ni piso) en OpenStreetMap.
• Para aplicar los límites de uso guardo cuántos análisis pides y cuándo, durante 7 días, y \
tu idioma.
• De cada caso guardo, sin caducidad, una ficha anónima (señales, canal, país de la cuenta, \
importes) para entender cómo operan las estafas. La conversación solo se guarda más de {days} \
días si pulsas «Guardar anonimizado para aprender», y entonces sin tu usuario.
• Con /borrar eliminas todos tus casos.

<b>Responsable y contacto</b>: Patricio Llorens, info@patriciollorens.com, para ejercer tus \
derechos de protección de datos o para reclamar. Si eres propietario y crees que tus datos \
aparecen por error en nuestro registro, escríbenos: lo revisamos y dejamos de usarlos."""

CLOSED = (
    "Mikiwame está en pruebas privadas: se entra con una invitación de alguien que ya lo usa. "
    "Gracias por tu interés. Contacto: info@patriciollorens.com"
)
PUBLIC_WELCOME = "Es gratis. Por seguridad, cada persona puede hacer hasta {daily} análisis al día."
PUBLIC_WELCOME_QUOTA = (
    "Puedes hacer {n} análisis gratis. Si alguien que ya usa Mikiwame te invita, tendrás más."
)
INVITE_CREATED = (
    "🎟 Invitación para una persona, válida {days} días. Mándale este enlace:\n{link}\n\n"
    "Invita solo a gente que conozcas: lo que analicen tus invitados cuenta junto con lo tuyo "
    "en el registro.{left}"
)
INVITE_LEFT = "\nTe quedan {n} invitación(es)."
# Open access, and no invite to give (public user, none left or beta full): the plain bot link
# lets anyone in with the public quota.
INVITE_SHARE = (
    "Para traer a alguien no hace falta invitación: pásale este enlace, es gratis (hasta "
    "{daily} análisis al día).\n{link}"
)
INVITE_SHARE_QUOTA = (
    "Para traer a alguien no hace falta invitación: pásale este enlace y tendrá {n} análisis "
    "gratis.\n{link}"
)
INVITE_NONE_LEFT = (
    "No te quedan invitaciones. Si necesitas más, escribe a info@patriciollorens.com."
)
BETA_FULL = (
    "La beta está completa por ahora: no se pueden crear más invitaciones. Gracias por querer "
    "traer a alguien."
)
INVITE_WELCOME = "🎟 Invitación aceptada. ¡Bienvenido/a a Mikiwame!"
INVITE_INVALID = (
    "Esa invitación no es válida: ya se usó, se acabaron sus plazas, ha caducado o no existe. "
    "Pide otra a quien te la mandó."
)
DRAFT_STATUS = "📥 Tengo {what}. Cuando lo tengas todo, pulsa <b>Analizar</b>."
ASK_LINK = (
    "🔗 Si tienes el <b>enlace del anuncio</b>, mándamelo también: lo registro para reconocer "
    "anuncios repetidos o copiados."
)
DRAFT_STATUS_CONTINUE = (
    "📥 Lo sumo al caso anterior: ahora tengo {what} en total. Pulsa <b>Analizar</b> para verlo "
    "todo junto, o <b>Empezar de nuevo</b> si es otro anuncio."
)
NOT_CHARGED = "Este intento no te ha gastado ningún análisis."
# Pressing Analizar on a bare unknown link: the status message just above already explains why
# (05-10-2026 in estimo: the whole notice twice in a row read as the bot repeating itself).
UNKNOWN_LINK_AGAIN = (
    "🔗 Con solo ese enlace sigo sin tener nada que valorar. Añade <b>capturas del anuncio</b> "
    "y <b>de la conversación con el anunciante</b>, y vuelve a pulsar <b>Analizar</b>. "
    + NOT_CHARGED
)
EMPTY_DRAFT = "Todavía no me has enviado nada. Mándame capturas del chat o el texto del anuncio."
ANALYZING = "🔎 Analizando… suele tardar menos de un minuto."
BUSY = "Ya estoy analizando tu caso anterior; espera a que termine."
TOO_MANY_IMAGES = (
    "Como máximo analizo {n} capturas por caso; las siguientes no las tendré en cuenta."
)
UNSUPPORTED = "Solo puedo analizar texto y capturas de pantalla (imágenes)."
RESET = "Vale, empezamos de cero. Mándame lo nuevo."
DELETE_CONFIRM = "¿Seguro que quieres borrar todos tus casos? No se puede deshacer."
DELETED = "Hecho: he borrado {n} caso(s) tuyos."
DELETE_CANCELLED = "No he borrado nada."
DELETE_BUSY = "Espera a que termine el análisis en curso y vuelve a pulsar /borrar."
ERROR = "Ha fallado algo por mi parte. Inténtalo de nuevo en un rato."
BUTTON_ANALYZE = "🔎 Analizar"
BUTTON_RESET = "🗑 Empezar de nuevo"
BUTTON_NEW_CASE = "🆕 Analizar otro anuncio"
BUTTON_KEEP = "📚 Guardar anonimizado para aprender"
KEPT = (
    "Gracias. He guardado este caso anonimizado (sin tu usuario ni los datos personales) para "
    "aprender a reconocer nuevas formas de estafa."
)
KEEP_FAILED = "Este caso ya no está disponible para guardarlo."
BUTTON_YES = "Sí, borrar"
BUTTON_NO = "No"
BUTTON_EXAMPLE = "👀 Ver un ejemplo"
# The invented example (mikiwame/example.py): what someone would send, then the real report.
EXAMPLE_INTRO = (
    "<b>Ejemplo inventado</b> (los datos no son de nadie). Imagina que alguien me manda esto:\n\n"
    "<i>{case}</i>"
)
EXAMPLE_OUTRO = (
    "Así es un informe. Cuando tengas un caso tuyo, mándame el anuncio y la conversación (texto "
    "o capturas) y pulsa <b>Analizar</b>. Ver el ejemplo no cuenta como análisis."
)
LANG_SET = "Vale, a partir de ahora te hablo en español. /english to switch to English."


def what(images: int, texts: int) -> str:
    parts = []
    if images:
        parts.append(f"{images} captura" + ("s" if images != 1 else ""))
    if texts:
        parts.append(f"{texts} texto" + ("s" if texts != 1 else ""))
    return " y ".join(parts) or "nada"


FOLLOWUP = (
    "🔁 Hace unos días analizaste un anuncio conmigo ({level}, el {day}). ¿Qué pasó al final? "
    "Tu respuesta es anónima y nos ayuda a acertar más."
)
FOLLOWUP_LEVEL = {
    "red": "🔴 riesgo alto",
    "amber": "🟠 precaución",
    "green": "🟢 sin señales claras",
}
BUTTON_OUT_SCAM = "Era un timo"
BUTTON_OUT_LEGIT = "Era de verdad"
BUTTON_OUT_UNKNOWN = "No seguí / no lo sé"
FOLLOWUP_THANKS = "Gracias, lo apunto. 🙏"
FOLLOWUP_THANKS_SORRY = (
    "Gracias, y perdona la falsa alarma: lo usamos para corregir el análisis. 🙏"
)
FOLLOWUP_THANKS_MISSED = (
    "Gracias por contárnoslo, y lo sentimos: lo usamos para que no se nos vuelva a escapar. "
    "Si has pagado, guarda el justificante y avisa a tu banco y a la Policía cuanto antes."
)
FOLLOWUP_DONE = "Ya tenía tu respuesta. ¡Gracias!"
