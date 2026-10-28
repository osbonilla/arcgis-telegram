"""Bot de Telegram -> ArcGIS: reportes ciudadanos con ubicación y foto.

Flujo:
  1. /reportar -> elige categoría -> comparte ubicación -> foto (opcional) -> descripción
  2. El bot crea el punto en la Hosted Feature Layer (addFeatures + addAttachment)
  3. Un técnico cambia el 'estado' en Field Maps / Map Viewer
  4. El bot detecta el cambio y avisa al ciudadano por Telegram
"""
import asyncio
import logging
import os
import time
import warnings

from telegram import (InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
                      ReplyKeyboardMarkup, ReplyKeyboardRemove, Update)
from telegram.ext import (AIORateLimiter, Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, ConversationHandler, MessageHandler, filters)

from arcgis_client import ArcGISClient
from store import Store

warnings.filterwarnings("ignore", message=".*per_message.*")
logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

# ------------------------------------------------------------------ config
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
LAYER_URL = os.environ["ARCGIS_LAYER_URL"]
MAPA_URL = os.getenv("MAPA_URL", "").strip()
INTERVALO = int(os.getenv("INTERVALO_REVISION_SEG", "15"))
# Grupo de Telegram de la clase donde el bot publica cada reporte (opcional)
GRUPO_CHAT_ID = os.getenv("GRUPO_CHAT_ID", "").strip()

arcgis = ArcGISClient(
    portal=os.getenv("ARCGIS_PORTAL", "https://www.arcgis.com"),
    layer_url=LAYER_URL,
    username=os.getenv("ARCGIS_USERNAME", ""),
    password=os.getenv("ARCGIS_PASSWORD", ""),
    api_key=os.getenv("ARCGIS_API_KEY", ""),
)
store = Store(os.getenv("DB_PATH", "/data/reportes.db"))

CATEGORIAS = {
    "Bache": "🕳️ Bache",
    "Alumbrado": "💡 Alumbrado",
    "Basura": "🗑️ Basura",
    "Agua": "🚰 Agua / alcantarilla",
    "Semaforo": "🚦 Semáforo",
    "Otro": "📌 Otro",
}
EMOJI_ESTADO = {"Nuevo": "🔴", "En proceso": "🟠", "Atendido": "🟢"}
CATEGORIA, UBICACION, FOTO, DESCRIPCION = range(4)
SALTAR = "Saltar ⏭️"
BOTON_REPORTAR = "📍 Nuevo reporte"

teclado_inicio = ReplyKeyboardMarkup([[BOTON_REPORTAR], ["📋 Mis reportes"]], resize_keyboard=True)


# --------------------------------------------------------------- comandos
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nombre = update.effective_user.first_name or ""
    await update.message.reply_text(
        f"¡Hola {nombre}! 👋\n\n"
        "Soy el bot de *reportes ciudadanos*. Lo que me envíes aparece al instante "
        "en un mapa de ArcGIS, y te aviso cuando un técnico lo atienda.\n\n"
        "Usa /reportar para empezar, /misreportes para ver tus reportes "
        "y /mapa para ver el mapa en vivo.",
        parse_mode="Markdown", reply_markup=teclado_inicio)


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
    teclado = ReplyKeyboardMarkup(
        [[KeyboardButton("📍 Enviar mi ubicación", request_location=True)]],
        resize_keyboard=True, one_time_keyboard=True)
    await q.message.reply_text(
        "Ahora comparte la *ubicación* del problema.\n"
        "Toca el botón, o usa 📎 → Ubicación para marcar otro punto en el mapa.",
        parse_mode="Markdown", reply_markup=teclado)
    return UBICACION


async def recibir_ubicacion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    loc = update.message.location
    context.user_data["lat"], context.user_data["lon"] = loc.latitude, loc.longitude
    await update.message.reply_text(
        "📸 Envía una *foto* del problema (o toca Saltar).", parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup([[SALTAR]], resize_keyboard=True, one_time_keyboard=True))
    return FOTO


async def ubicacion_invalida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Necesito una ubicación 📍 — usa el botón o 📎 → Ubicación.")
    return UBICACION


async def recibir_foto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    archivo = await update.message.photo[-1].get_file()  # la de mayor resolución
    context.user_data["foto"] = bytes(await archivo.download_as_bytearray())
    return await pedir_descripcion(update)


async def saltar_foto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await pedir_descripcion(update)


async def pedir_descripcion(update: Update):
    await update.message.reply_text(
        "✍️ Describe brevemente el problema (o toca Saltar).",
        reply_markup=ReplyKeyboardMarkup([[SALTAR]], resize_keyboard=True, one_time_keyboard=True))
    return DESCRIPCION


async def recibir_descripcion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text
    context.user_data["descripcion"] = "" if texto == SALTAR else texto[:500]
    return await guardar(update, context)


async def guardar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = context.user_data
    await update.message.reply_text("⏳ Enviando a ArcGIS…", reply_markup=ReplyKeyboardRemove())
    atributos = {
        "categoria": d["categoria"],
        "descripcion": d.get("descripcion", ""),
        "estado": "Nuevo",
        "fecha": int(time.time() * 1000),
        "fuente": "Telegram",
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
    log.info("Reporte %s creado (%s) por chat %s", oid, d["categoria"], update.effective_chat.id)
    await publicar_en_grupo(context, oid, d, update.effective_user.first_name or "Alguien")
    msg = (f"✅ ¡Listo! Tu reporte *#{oid}* ya está en el mapa.\n"
           f"{EMOJI_ESTADO['Nuevo']} Estado: Nuevo\n\n"
           "Te avisaré aquí cuando cambie su estado.")
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
    if d.get("descripcion"):
        texto += f"\n“{d['descripcion'][:200]}”"
    try:
        if d.get("foto"):
            await context.bot.send_photo(GRUPO_CHAT_ID, d["foto"], caption=texto)
        else:
            await context.bot.send_message(GRUPO_CHAT_ID, texto)
    except Exception as e:  # noqa: BLE001
        log.warning("No se pudo publicar en el grupo: %s", e)


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
           .build())
    privado = filters.ChatType.PRIVATE
    conv = ConversationHandler(
        entry_points=[CommandHandler("reportar", reportar, filters=privado),
                      CommandHandler("start", reportar, filters=privado & filters.Regex("reportar")),
                      MessageHandler(privado & filters.Regex(f"^{BOTON_REPORTAR}$"), reportar)],
        states={
            CATEGORIA: [CallbackQueryHandler(elegir_categoria)],
            UBICACION: [MessageHandler(filters.LOCATION, recibir_ubicacion),
                        MessageHandler(filters.ALL & ~filters.COMMAND, ubicacion_invalida)],
            FOTO: [MessageHandler(filters.PHOTO, recibir_foto),
                   MessageHandler(filters.Regex(f"^{SALTAR}$"), saltar_foto)],
            DESCRIPCION: [MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_descripcion)],
        },
        fallbacks=[CommandHandler("cancelar", cancelar)],
    )
    app.add_handler(conv)
    app.add_handler(CommandHandler("start", start, filters=privado))
    app.add_handler(CommandHandler("reportar", solo_privado, filters=filters.ChatType.GROUPS))
    app.add_handler(CommandHandler("chatid", chatid))
    app.add_handler(CommandHandler("mapa", mapa))
    app.add_handler(CommandHandler("misreportes", mis_reportes))
    app.add_handler(MessageHandler(filters.Regex("^📋 Mis reportes$"), mis_reportes))
    app.job_queue.run_repeating(revisar_estados, interval=INTERVALO, first=5)
    return app


if __name__ == "__main__":
    log.info("Bot iniciado. Capa: %s", LAYER_URL)
    construir_app().run_polling(allowed_updates=Update.ALL_TYPES)
