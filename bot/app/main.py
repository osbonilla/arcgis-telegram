"""Bot de Telegram -> ArcGIS: reportes ciudadanos con ubicación, foto e IA.

Flujo con botones:
  /reportar -> categoría -> ubicación -> foto (opcional) -> descripción -> punto en ArcGIS
Flujo con IA (modelo local con Ollama, o Gemini en la nube):
  texto o audio libre -> Gemini decide si es REPORTE (categoría, urgencia, descripción)
  o PREGUNTA ("¿cuántos baches hay cerca?") -> consulta REST a ArcGIS -> respuesta natural
De vuelta:
  un técnico cambia 'estado' en Field Maps / Map Viewer -> el bot avisa al ciudadano
"""
import asyncio
import logging
import os
import time
import warnings
from datetime import datetime, timedelta, timezone

from telegram import (InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
                      ReplyKeyboardMarkup, ReplyKeyboardRemove, Update)
from telegram.constants import ChatAction
from telegram.ext import (AIORateLimiter, Application, CallbackQueryHandler,
                          CommandHandler, ContextTypes, ConversationHandler, MessageHandler,
                          TypeHandler, filters)

from arcgis_client import ArcGISClient
from ia import IAError, URGENCIAS, crear_ia
from store import Store

warnings.filterwarnings("ignore", message=".*per_message.*")
logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
for ruidoso in ("httpx", "apscheduler", "google_genai"):
    logging.getLogger(ruidoso).setLevel(logging.WARNING)
log = logging.getLogger("bot")

# ------------------------------------------------------------------ config
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
LAYER_URL = os.environ["ARCGIS_LAYER_URL"]
MAPA_URL = os.getenv("MAPA_URL", "").strip()
INTERVALO = int(os.getenv("INTERVALO_REVISION_SEG", "15"))
GRUPO_CHAT_ID = os.getenv("GRUPO_CHAT_ID", "").strip()
MOSTRAR_CONSULTA = os.getenv("MOSTRAR_CONSULTA", "true").lower() == "true"

arcgis = ArcGISClient(
    portal=os.getenv("ARCGIS_PORTAL", "https://www.arcgis.com"),
    layer_url=LAYER_URL,
    username=os.getenv("ARCGIS_USERNAME", ""),
    password=os.getenv("ARCGIS_PASSWORD", ""),
    api_key=os.getenv("ARCGIS_API_KEY", ""),
)
store = Store(os.getenv("DB_PATH", "/data/reportes.db"))
ia = crear_ia()  # None si no hay IA configurada

CATEGORIAS = {
    "Bache": "🕳️ Bache",
    "Alumbrado": "💡 Alumbrado",
    "Basura": "🗑️ Basura",
    "Agua": "🚰 Agua / alcantarilla",
    "Semaforo": "🚦 Semáforo",
    "Otro": "📌 Otro",
}
EMOJI_ESTADO = {"Nuevo": "🔴", "En proceso": "🟠", "Atendido": "🟢"}
EMOJI_URGENCIA = {"Alta": "🔥", "Media": "⚠️", "Baja": "🟢"}
CATEGORIA, UBICACION, FOTO, DESCRIPCION = range(4)
SALTAR = "Saltar ⏭️"
BOTON_REPORTAR = "📍 Nuevo reporte"
BOTON_MIS = "📋 Mis reportes"

teclado_inicio = ReplyKeyboardMarkup([[BOTON_REPORTAR], [BOTON_MIS]], resize_keyboard=True)
teclado_ubicacion = ReplyKeyboardMarkup(
    [[KeyboardButton("📍 Enviar mi ubicación", request_location=True)]],
    resize_keyboard=True, one_time_keyboard=True)


# --------------------------------------------------------------- comandos
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nombre = update.effective_user.first_name or ""
    texto = (f"¡Hola {nombre}! 👋\n\n"
             "Soy el bot de *reportes ciudadanos*. Lo que me envíes aparece al instante "
             "en un mapa de ArcGIS, y te aviso cuando un técnico lo atienda.\n\n"
             "Usa /reportar para empezar, /misreportes para ver tus reportes "
             "y /mapa para ver el mapa en vivo.")
    if ia:
        texto += ("\n\n🤖 *También puedes hablarme normal*"
                  + (", por texto o con un audio" if ia.soporta_audio else "") + ":\n"
                  "• _\"Hay un hueco enorme frente a la biblioteca\"_ → creo el reporte\n"
                  "• _\"¿Cuántos baches hay cerca de mí?\"_ → consulto el mapa")
    await update.message.reply_text(texto, parse_mode="Markdown", reply_markup=teclado_inicio)


async def reportar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    botones = [[InlineKeyboardButton(v, callback_data=k)] for k, v in CATEGORIAS.items()]
    await update.message.reply_text("¿Qué quieres reportar?",
                                    reply_markup=InlineKeyboardMarkup(botones))
    return CATEGORIA


async def elegir_categoria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["categoria"] = q.data
    await q.edit_message_text(f"Categoría: {CATEGORIAS[q.data]}")
    await q.message.reply_text(
        "Ahora comparte la *ubicación* del problema.\n"
        "Toca el botón, o usa 📎 → Ubicación para marcar otro punto en el mapa.",
        parse_mode="Markdown", reply_markup=teclado_ubicacion)
    return UBICACION


async def categoria_directa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Botón de categoría de un menú anterior: empieza un reporte nuevo con esa categoría."""
    context.user_data.clear()
    return await elegir_categoria(update, context)


async def recibir_ubicacion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    loc = update.message.location
    context.user_data["lat"], context.user_data["lon"] = loc.latitude, loc.longitude
    context.chat_data["ubicacion"] = (loc.longitude, loc.latitude)  # para preguntas "cerca de mí"
    await update.message.reply_text(
        "📸 Envía una *foto* del problema (o toca Saltar).", parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup([[SALTAR]], resize_keyboard=True, one_time_keyboard=True))
    return FOTO


def es_saltar(texto: str | None) -> bool:
    return (texto or "").strip().lower().startswith("saltar")


async def ubicacion_invalida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Necesito una ubicación 📍 Toca «Enviar mi ubicación» o usa 📎 → Ubicación.\n"
        "(Desde Telegram de escritorio no se puede: usa el celular.) /cancelar para salir.",
        reply_markup=teclado_ubicacion)
    return UBICACION


async def categoria_pendiente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Elige la categoría tocando uno de los botones de arriba 👆 (o /cancelar).")
    return CATEGORIA


async def foto_pendiente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Envía una foto 📸 o toca «Saltar» (o /cancelar).",
        reply_markup=ReplyKeyboardMarkup([[SALTAR]], resize_keyboard=True, one_time_keyboard=True))
    return FOTO


async def descripcion_pendiente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Escribe una descripción corta, o toca «Saltar» (o /cancelar).")
    return DESCRIPCION


async def recibir_foto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.photo:
        archivo = await update.message.photo[-1].get_file()  # la de mayor resolución
    else:  # foto enviada "como archivo"
        archivo = await update.message.document.get_file()
    context.user_data["foto"] = bytes(await archivo.download_as_bytearray())
    return await pedir_descripcion(update, context)


async def saltar_foto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await pedir_descripcion(update, context)


async def pedir_descripcion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("descripcion"):  # la IA ya la escribió
        return await guardar(update, context)
    extra = " o mándame un audio 🎙️" if ia and ia.soporta_audio else ""
    await update.message.reply_text(
        f"✍️ Describe brevemente el problema{extra} (o toca Saltar).",
        reply_markup=ReplyKeyboardMarkup([[SALTAR]], resize_keyboard=True, one_time_keyboard=True))
    return DESCRIPCION


async def recibir_descripcion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text
    context.user_data["descripcion"] = "" if es_saltar(texto) else texto[:500]
    return await guardar(update, context)


async def descripcion_por_voz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """En el paso de descripción también se acepta un audio: la IA lo transcribe y resume."""
    try:
        audio = await descargar_audio(update)
        datos = await pensando(context, update.effective_chat.id, ia.interpretar, None, audio)
        context.user_data["descripcion"] = datos["descripcion"]
        context.user_data.setdefault("urgencia", datos.get("urgencia"))
        await update.message.reply_text(f"🎧 Escuché: «{datos.get('transcripcion', '')[:300]}»")
    except IAError:
        await update.message.reply_text("No pude procesar el audio 😅 Escríbelo, por favor.")
        return DESCRIPCION
    return await guardar(update, context)


async def guardar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = context.user_data
    await update.message.reply_text("⏳ Enviando a ArcGIS…", reply_markup=ReplyKeyboardRemove())
    atributos = {
        "categoria": d["categoria"],
        "descripcion": d.get("descripcion", ""),
        "estado": "Nuevo",
        "urgencia": d.get("urgencia"),
        "fecha": int(time.time() * 1000),
        "fuente": "Telegram + IA" if d.get("via_ia") else "Telegram",
    }
    try:
        oid = await asyncio.to_thread(arcgis.agregar_reporte, d["lon"], d["lat"], atributos)
        if d.get("foto"):
            await asyncio.to_thread(arcgis.agregar_foto, oid, d["foto"], f"reporte_{oid}.jpg")
    except Exception as e:  # noqa: BLE001
        log.exception("Error guardando en ArcGIS")
        await update.message.reply_text(f"❌ No se pudo guardar el reporte: {e}",
                                        reply_markup=teclado_inicio)
        return ConversationHandler.END

    store.guardar(oid, update.effective_chat.id, d["categoria"])
    log.info("Reporte %s creado (%s, urgencia %s) por chat %s",
             oid, d["categoria"], d.get("urgencia"), update.effective_chat.id)
    await publicar_en_grupo(context, oid, d, update.effective_user.first_name or "Alguien")
    msg = f"✅ ¡Listo! Tu reporte *#{oid}* ya está en el mapa.\n{EMOJI_ESTADO['Nuevo']} Estado: Nuevo"
    if d.get("urgencia"):
        msg += f"\n{EMOJI_URGENCIA.get(d['urgencia'], '')} Urgencia: {d['urgencia']}"
    msg += "\n\nTe avisaré aquí cuando cambie su estado."
    if MAPA_URL:
        msg += f"\n\n🗺️ [Ver el mapa en vivo]({MAPA_URL})"
    await update.message.reply_text(msg, parse_mode="Markdown",
                                    reply_markup=teclado_inicio, disable_web_page_preview=True)
    context.user_data.clear()
    return ConversationHandler.END


async def publicar_en_grupo(context: ContextTypes.DEFAULT_TYPE, oid: int, d: dict, nombre: str):
    """Comparte el reporte en el grupo de la clase para que todos lo vean."""
    if not GRUPO_CHAT_ID:
        return
    texto = (f"📢 Nuevo reporte #{oid} · {CATEGORIAS.get(d['categoria'], d['categoria'])}\n"
             f"Enviado por {nombre}")
    if d.get("urgencia"):
        texto += f" · urgencia {d['urgencia']}"
    if d.get("via_ia"):
        texto += " · 🤖 clasificado por IA"
    if d.get("descripcion"):
        texto += f"\n“{d['descripcion'][:200]}”"
    try:
        if d.get("foto"):
            await context.bot.send_photo(GRUPO_CHAT_ID, d["foto"], caption=texto)
        else:
            await context.bot.send_message(GRUPO_CHAT_ID, texto)
    except Exception as e:  # noqa: BLE001
        log.warning("No se pudo publicar en el grupo: %s", e)


# ------------------------------------------------------------- IA (Gemini)
async def descargar_audio(update: Update) -> bytes:
    archivo = await update.message.voice.get_file()
    return bytes(await archivo.download_as_bytearray())


async def pensando(context: ContextTypes.DEFAULT_TYPE, chat_id: int, funcion, *args):
    """Ejecuta una llamada a la IA mostrando «escribiendo…» mientras espera (un modelo local tarda)."""
    tarea = asyncio.create_task(asyncio.to_thread(funcion, *args))
    while not tarea.done():
        try:
            await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
        except Exception:  # noqa: BLE001
            pass
        await asyncio.wait({tarea}, timeout=4)
    return tarea.result()


async def entrada_libre(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Texto o audio libre: la IA decide si es un reporte, una pregunta u otra cosa."""
    if not ia:
        await ayuda(update, context)
        return ConversationHandler.END
    if update.message.voice and not ia.soporta_audio:
        await update.message.reply_text(
            "🎙️ Con el modelo local todavía no entiendo audios. Escríbeme el mensaje, por favor 🙂")
        return ConversationHandler.END
    try:
        audio = await descargar_audio(update) if update.message.voice else None
        datos = await pensando(context, update.effective_chat.id, ia.interpretar, update.message.text, audio)
    except Exception as e:  # noqa: BLE001  (IAError, red, formato inesperado...)
        log.warning("IA no disponible: %s", e)
        await update.message.reply_text(
            "La IA no está disponible en este momento 😅 Usa /reportar para hacerlo con botones.")
        return ConversationHandler.END
    log.info("IA interpretó: %s", datos)  # útil para mostrar en clase qué extrajo la IA

    escuche = f"🎧 Escuché: «{datos['transcripcion'][:300]}»\n\n" if audio else ""

    if datos["tipo"] == "reporte":
        context.user_data.clear()
        context.user_data.update({"categoria": datos["categoria"], "urgencia": datos["urgencia"],
                                  "descripcion": datos["descripcion"], "via_ia": True})
        await update.message.reply_text(
            f"{escuche}🤖 Entendí este reporte:\n"
            f"{CATEGORIAS[datos['categoria']]} · {EMOJI_URGENCIA[datos['urgencia']]} Urgencia {datos['urgencia']}\n"
            f"«{datos['descripcion']}»\n\n"
            "Ahora comparte la ubicación 📍 (o /cancelar si no era eso).",
            reply_markup=teclado_ubicacion)
        return UBICACION

    if datos["tipo"] == "pregunta":
        if escuche:
            await update.message.reply_text(escuche.strip())
        await responder_pregunta(update, context, datos)
        return ConversationHandler.END

    await update.message.reply_text(
        datos.get("respuesta") or "Cuéntame un problema de tu ciudad o pregúntame por los reportes 🙂",
        reply_markup=teclado_inicio)
    return ConversationHandler.END


def construir_where(d: dict) -> str:
    """Arma el filtro SQL con valores de listas cerradas (la IA no escribe SQL)."""
    c = []
    if d.get("filtro_categoria") in CATEGORIAS:
        c.append(f"categoria = '{d['filtro_categoria']}'")
    est = d.get("filtro_estado")
    if est == "Pendientes":
        c.append("estado <> 'Atendido'")
    elif est in EMOJI_ESTADO:
        c.append(f"estado = '{est}'")
    if d.get("filtro_urgencia") in URGENCIAS and "urgencia" in arcgis.campos():
        c.append(f"urgencia = '{d['filtro_urgencia']}'")
    horas = int(d.get("ultimas_horas") or 0)
    if 0 < horas <= 24 * 30:
        desde = datetime.now(timezone.utc) - timedelta(hours=horas)
        c.append(f"fecha >= TIMESTAMP '{desde:%Y-%m-%d %H:%M:%S}'")
    return " AND ".join(c) or "1=1"


async def responder_pregunta(update: Update, context: ContextTypes.DEFAULT_TYPE, datos: dict):
    cerca = None
    radio = max(50, min(int(datos.get("radio_m") or 500), 5000))
    if datos.get("cerca_de_mi"):
        cerca = context.chat_data.get("ubicacion")
        if not cerca:
            context.chat_data["pregunta_pendiente"] = datos
            await update.message.reply_text(
                "Para responder eso necesito saber dónde estás 📍 Comparte tu ubicación.",
                reply_markup=teclado_ubicacion)
            return

    operacion = datos.get("operacion") or "contar"
    agrupar = datos.get("agrupar_por") if datos.get("agrupar_por") != "ninguno" else None
    if operacion == "agrupar" and not agrupar:
        agrupar = "categoria"
    try:
        where = await asyncio.to_thread(construir_where, datos)
        resultado = await asyncio.to_thread(arcgis.consultar, where, operacion, agrupar, cerca, radio)
    except Exception as e:  # noqa: BLE001
        log.exception("Error consultando ArcGIS")
        await update.message.reply_text(f"❌ No pude consultar el mapa: {e}", reply_markup=teclado_inicio)
        return

    consulta = f"{operacion} · where: {where}"
    if agrupar and operacion == "agrupar":
        consulta += f" · agrupado por {agrupar}"
    if cerca:
        consulta += f" · a {radio} m de tu ubicación"
    log.info("Pregunta → ArcGIS: %s → %s", consulta, resultado)

    try:
        respuesta = await pensando(context, update.effective_chat.id,
                                   ia.redactar_respuesta, datos["transcripcion"], consulta, resultado)
    except Exception:  # noqa: BLE001  sin IA para redactar, se responde con los datos crudos
        respuesta = f"Resultado: {resultado}"
    if MOSTRAR_CONSULTA:
        respuesta += f"\n\n🔎 Consulta a ArcGIS: {consulta}"
    await update.message.reply_text(respuesta, reply_markup=teclado_inicio)


async def ubicacion_suelta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ubicación enviada fuera de un reporte: se guarda para preguntas 'cerca de mí'."""
    loc = update.message.location
    context.chat_data["ubicacion"] = (loc.longitude, loc.latitude)
    pendiente = context.chat_data.pop("pregunta_pendiente", None)
    if pendiente:
        await responder_pregunta(update, context, pendiente)
    else:
        await update.message.reply_text(
            "📍 Guardé tu ubicación. Pregúntame, por ejemplo: «¿qué reportes hay cerca de mí?», "
            "o usa /reportar." if ia else
            "📍 Recibí una ubicación, pero no hay un reporte en curso. Empieza con /reportar 🙂",
            reply_markup=teclado_inicio)


# ------------------------------------------------------------- utilidades
async def ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Usa /reportar para crear un reporte o /misreportes para ver los tuyos.",
                                    reply_markup=teclado_inicio)


async def registrar_entrada(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Registra en los logs cada mensaje que llega (para ver en vivo qué hace cada usuario)."""
    m = update.effective_message
    quien = update.effective_user.first_name if update.effective_user else "?"
    if update.callback_query:
        tipo = f"botón «{update.callback_query.data}»"
    elif m is None:
        return
    elif m.location:
        tipo = f"ubicación ({m.location.latitude:.5f}, {m.location.longitude:.5f})"
    elif m.photo or (m.document and (m.document.mime_type or "").startswith("image/")):
        tipo = "foto"
    elif m.voice:
        tipo = f"audio de {m.voice.duration}s"
    elif m.text:
        tipo = f"texto «{m.text[:60]}»"
    else:
        tipo = "otro tipo de mensaje"
    log.info("← %s (%s): %s", quien, update.effective_chat.type if update.effective_chat else "?", tipo)


async def error_global(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Cualquier error inesperado: se registra y el usuario recibe una respuesta (nunca silencio)."""
    log.error("Error no controlado", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "Ups, algo falló de mi lado 😅 Intenta de nuevo o usa /reportar.", reply_markup=teclado_inicio)
        except Exception:  # noqa: BLE001
            pass


async def al_iniciar(app: Application):
    yo = await app.bot.get_me()
    log.info("Conectado a Telegram como @%s → https://t.me/%s", yo.username, yo.username)
    if ia:  # carga el modelo en segundo plano para que el primer mensaje no espere
        asyncio.get_running_loop().run_in_executor(None, ia.precargar)


async def reiniciar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start en medio de un reporte: se descarta y se vuelve al inicio."""
    context.user_data.clear()
    await start(update, context)
    return ConversationHandler.END


async def foto_suelta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📸 Linda foto, pero no hay un reporte en curso. Empieza con /reportar y te la pido en el paso 3.",
        reply_markup=teclado_inicio)


async def solo_privado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """En el grupo no se reporta (mezclaría conversaciones): se invita al chat privado."""
    yo = await context.bot.get_me()
    await update.message.reply_text(
        f"Para reportar, escríbeme por privado 👉 https://t.me/{yo.username}?start=reportar")


async def chatid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra el id del chat actual (sirve para configurar GRUPO_CHAT_ID)."""
    await update.message.reply_text(f"El id de este chat es: {update.effective_chat.id}")


async def mapa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if MAPA_URL:
        await update.message.reply_text(f"🗺️ Mapa en vivo: {MAPA_URL}")
    else:
        await update.message.reply_text("El mapa en vivo se está proyectando en clase 🙂")


async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Reporte cancelado.", reply_markup=teclado_inicio)
    return ConversationHandler.END


async def mis_reportes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    filas = store.de_usuario(update.effective_chat.id)
    if not filas:
        await update.message.reply_text("Aún no tienes reportes. Usa /reportar 🙂")
        return
    try:  # estado fresco desde ArcGIS
        vivos = await asyncio.to_thread(arcgis.estados, [f[0] for f in filas])
    except Exception:  # noqa: BLE001
        vivos = {}
    lineas = []
    for oid, cat, estado in filas:
        estado = vivos.get(oid) or estado
        lineas.append(f"{EMOJI_ESTADO.get(estado, '⚪')} #{oid} · {CATEGORIAS.get(cat, cat)} · {estado}")
    await update.message.reply_text("Tus últimos reportes:\n\n" + "\n".join(lineas))


async def mis_reportes_en_curso(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await mis_reportes(update, context)
    await update.message.reply_text("(Sigues con un reporte a medias: continúa o usa /cancelar.)")
    return None  # None = se mantiene el estado actual


# ---------------------------------------- ArcGIS -> Telegram (de vuelta)
async def revisar_estados(context: ContextTypes.DEFAULT_TYPE):
    pendientes = store.pendientes()
    if not pendientes:
        return
    try:
        vivos = await asyncio.to_thread(arcgis.estados, [p[0] for p in pendientes])
    except Exception as e:  # noqa: BLE001
        log.warning("No se pudo consultar estados: %s", e)
        return
    for oid, chat_id, cat, estado_local in pendientes:
        nuevo = vivos.get(oid)
        if nuevo and nuevo != estado_local:
            store.actualizar_estado(oid, nuevo)
            if GRUPO_CHAT_ID and nuevo == "Atendido":
                try:
                    await context.bot.send_message(
                        GRUPO_CHAT_ID, f"🟢 El reporte #{oid} ({CATEGORIAS.get(cat, cat)}) fue atendido.")
                except Exception as e:  # noqa: BLE001
                    log.warning("No se pudo avisar al grupo: %s", e)
            texto = {
                "En proceso": f"🟠 Tu reporte #{oid} ({CATEGORIAS.get(cat, cat)}) está *en proceso*. Un técnico ya va en camino.",
                "Atendido": f"🟢 ¡Tu reporte #{oid} ({CATEGORIAS.get(cat, cat)}) fue *atendido*! Gracias por ayudar a tu ciudad. 🙌",
            }.get(nuevo, f"Tu reporte #{oid} cambió a: {nuevo}")
            try:
                await context.bot.send_message(chat_id, texto, parse_mode="Markdown")
                log.info("Notificado %s -> %s", oid, nuevo)
            except Exception as e:  # noqa: BLE001
                log.warning("No se pudo notificar a %s: %s", chat_id, e)


# -------------------------------------------------------------------- app
def construir_app() -> Application:
    # concurrent_updates: atiende a toda la clase en paralelo (cada usuario tiene su propia conversación)
    app = (Application.builder().token(TELEGRAM_TOKEN)
           .concurrent_updates(32)
           # respeta los límites de Telegram (~20 mensajes/min por grupo): encola en vez de perder mensajes
           .rate_limiter(AIORateLimiter(max_retries=5))
           .post_init(al_iniciar)
           .build())
    app.add_handler(TypeHandler(Update, registrar_entrada), group=-1)  # solo registra, no responde
    app.add_error_handler(error_global)
    privado = filters.ChatType.PRIVATE
    botones = filters.Regex(f"^({BOTON_REPORTAR}|{BOTON_MIS})$")
    texto_libre = privado & ((filters.TEXT & ~filters.COMMAND & ~botones) | filters.VOICE)

    entradas = [CommandHandler("reportar", reportar, filters=privado),
                CommandHandler("start", reportar, filters=privado & filters.Regex("reportar")),
                MessageHandler(privado & filters.Regex(f"^{BOTON_REPORTAR}$"), reportar),
                CallbackQueryHandler(categoria_directa, pattern=f"^({'|'.join(CATEGORIAS)})$")]
    if ia:  # mensajes libres (texto o audio) entran por la IA
        entradas.append(MessageHandler(texto_libre, entrada_libre))

    no_boton = ~filters.COMMAND & ~botones
    descripcion = [MessageHandler(filters.TEXT & no_boton, recibir_descripcion)]
    if ia and ia.soporta_audio:
        descripcion.append(MessageHandler(filters.VOICE, descripcion_por_voz))
    descripcion.append(MessageHandler(filters.ALL & no_boton, descripcion_pendiente))

    conv = ConversationHandler(
        entry_points=entradas,
        states={
            CATEGORIA: [CallbackQueryHandler(elegir_categoria),
                        MessageHandler(filters.ALL & no_boton, categoria_pendiente)],
            UBICACION: [MessageHandler(filters.LOCATION, recibir_ubicacion),
                        MessageHandler(filters.ALL & no_boton, ubicacion_invalida)],
            FOTO: [MessageHandler(filters.PHOTO | filters.Document.IMAGE, recibir_foto),
                   MessageHandler(filters.Regex(r"(?i)^\s*saltar"), saltar_foto),
                   MessageHandler(filters.ALL & no_boton, foto_pendiente)],
            DESCRIPCION: descripcion,
        },
        fallbacks=[CommandHandler("cancelar", cancelar),
                   # /reportar o el botón en medio de un reporte: empieza uno nuevo
                   CommandHandler("reportar", reportar),
                   MessageHandler(filters.Regex(f"^{BOTON_REPORTAR}$"), reportar),
                   CommandHandler("start", reiniciar),
                   MessageHandler(filters.Regex(f"^{BOTON_MIS}$"), mis_reportes_en_curso)],
        conversation_timeout=15 * 60,  # un reporte abandonado se descarta a los 15 min
    )
    app.add_handler(conv)
    app.add_handler(CommandHandler("start", start, filters=privado))
    app.add_handler(CommandHandler("reportar", solo_privado, filters=filters.ChatType.GROUPS))
    app.add_handler(CommandHandler("chatid", chatid))
    app.add_handler(CommandHandler("mapa", mapa))
    app.add_handler(CommandHandler("misreportes", mis_reportes))
    app.add_handler(MessageHandler(privado & filters.Regex(f"^{BOTON_MIS}$"), mis_reportes))
    app.add_handler(MessageHandler(privado & filters.LOCATION, ubicacion_suelta))
    app.add_handler(MessageHandler(privado & (filters.PHOTO | filters.Document.IMAGE), foto_suelta))
    app.add_handler(MessageHandler(privado & ~filters.COMMAND, ayuda))
    app.job_queue.run_repeating(revisar_estados, interval=INTERVALO, first=5)
    return app


if __name__ == "__main__":
    log.info("Bot iniciado. Capa: %s · IA: %s", LAYER_URL, ia.nombre if ia else "desactivada")
    construir_app().run_polling(allowed_updates=Update.ALL_TYPES)
