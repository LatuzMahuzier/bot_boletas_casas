# === BOT MEJORADO CON TIPO DE GASTO ===
import logging
import os
import requests
import openai
import tempfile
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import json
import base64
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, MessageHandler, CallbackQueryHandler, CommandHandler, ContextTypes, filters

# === Cargar variables desde .env ===
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
EMAIL_DESTINO = os.getenv("EMAIL_DESTINO")
EMAIL_ORIGEN = os.getenv("EMAIL_ORIGEN")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")

openai.api_key = OPENAI_API_KEY

# === Usuarios permitidos ===
USUARIOS_AUTORIZADOS = [7527703575, 222222222]  # Reemplaza con tus IDs de Telegram

# === Variables de estado ===
user_data = {}
user_image_paths = {}
user_temp_project = {}
user_waiting_gasto = set()

# === Proyectos disponibles ===
PROYECTOS = ["Casa Del Sante", "Casa Covarrubias", "Casa Shaccaluga", "Casa Vidal"]

# === Procesamiento de boleta con OpenAI ===
def analizar_boleta_con_openai(imagen_path):
    with open(imagen_path, "rb") as image_file:
        image_bytes = image_file.read()
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    response = openai.ChatCompletion.create(
        model="gpt-4o",
        messages=[
            {
                "role": "system",
                "content": "Eres un asistente que lee boletas y entrega un resumen en formato JSON. Por cada producto en la boleta, entrega un objeto JSON separado con los siguientes campos: Proveedor, NumeroBoletaFactura, Fecha, Producto, PrecioUnitario, Cantidad, TotalProducto, MontoTotalBoleta, IVABoleta. Devuelve una lista de objetos JSON."
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_b64}"
                        }
                    }
                ]
            }
        ]
    )

    return response.choices[0].message.content

# === Envío por correo ===
def enviar_resumen_por_correo(destinatario, lista_boletas):
    filas = []
    for b in lista_boletas:
        proyecto = b['proyecto']
        gasto = b['gasto']
        try:
            resumen_limpio = b['resumen'].strip()
            if resumen_limpio.startswith('```json'):
                resumen_limpio = resumen_limpio[7:]
            if resumen_limpio.startswith('```'):
                resumen_limpio = resumen_limpio[3:]
            if resumen_limpio.endswith('```'):
                resumen_limpio = resumen_limpio[:-3]
            items = json.loads(resumen_limpio)
            for item in items:
                item['Proyecto'] = proyecto
                item['TipoGasto'] = gasto
                filas.append(item)
        except:
            filas.append({"TipoGasto": gasto, "Proyecto": proyecto, "Error": "Resumen no válido", "Contenido": b['resumen']})

    if not filas:
        cuerpo_html = "<p>No se encontraron boletas.</p>"
    else:
        columnas = filas[0].keys()
        cuerpo_html = "<h2>Resumen de Boletas</h2><table border='1' cellpadding='5' cellspacing='0'><tr>"
        for col in columnas:
            cuerpo_html += f"<th>{col}</th>"
        cuerpo_html += "</tr>"

        for fila in filas:
            cuerpo_html += "<tr>"
            for col in columnas:
                valor = fila.get(col, "")
                cuerpo_html += f"<td>{valor}</td>"
            cuerpo_html += "</tr>"
        cuerpo_html += "</table>"

    msg = MIMEMultipart("alternative")
    msg['Subject'] = 'Resumen de boletas'
    msg['From'] = EMAIL_ORIGEN
    msg['To'] = destinatario
    msg.attach(MIMEText(cuerpo_html, 'html'))

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
        server.login(EMAIL_ORIGEN, EMAIL_PASSWORD)
        server.sendmail(EMAIL_ORIGEN, destinatario, msg.as_string())

# === Imagen recibida ===
async def manejar_imagen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id not in USUARIOS_AUTORIZADOS:
        await update.message.reply_text("No estás autorizado para usar este bot.")
        return

    await update.message.reply_text("📸 Foto recibida. Procesando...")
    photo_file = await update.message.photo[-1].get_file()
    with tempfile.NamedTemporaryFile(delete=False) as tf:
        await photo_file.download_to_drive(tf.name)
        user_image_paths[user_id] = tf.name

    keyboard = [[InlineKeyboardButton(proyecto, callback_data=proyecto)] for proyecto in PROYECTOS]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Selecciona el proyecto al que pertenece esta boleta:", reply_markup=reply_markup)

# === Selección de proyecto ===
async def manejar_seleccion_proyecto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    if user_id not in USUARIOS_AUTORIZADOS:
        await query.message.reply_text("No estás autorizado para usar este bot.")
        return

    await query.answer()
    proyecto = query.data
    user_temp_project[user_id] = proyecto
    user_waiting_gasto.add(user_id)

    await query.message.reply_text(f"Has seleccionado el proyecto: *{proyecto}*.\nPor favor escribe ahora el tipo de gasto (Ej: transporte, materiales, etc)", parse_mode='Markdown')

# === Mensaje texto: tipo de gasto ===
async def manejar_tipo_gasto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id not in user_waiting_gasto:
        return

    gasto = update.message.text
    proyecto = user_temp_project.get(user_id)
    imagen_path = user_image_paths.get(user_id)

    if imagen_path:
        resumen = analizar_boleta_con_openai(imagen_path)
        boletas_usuario = user_data.get(user_id, [])
        boletas_usuario.append({'proyecto': proyecto, 'gasto': gasto, 'resumen': resumen})
        user_data[user_id] = boletas_usuario

        os.remove(imagen_path)
        user_image_paths.pop(user_id, None)
        user_waiting_gasto.remove(user_id)
        user_temp_project.pop(user_id, None)

        await update.message.reply_text(f"✅ Boleta registrada bajo *{proyecto}* con gasto *{gasto}*. Si no tienes más boletas, usa /enviar", parse_mode='Markdown')
    else:
        await update.message.reply_text("❌ No se encontró imagen para procesar.")

# === Comando /enviar ===
async def comando_enviar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id not in USUARIOS_AUTORIZADOS:
        await update.message.reply_text("No estás autorizado para usar este bot.")
        return

    boletas = user_data.get(user_id, [])
    if not boletas:
        await update.message.reply_text("No tienes boletas pendientes para enviar.")
        return

    try:
        enviar_resumen_por_correo(EMAIL_DESTINO, boletas)
        await update.message.reply_text("Boletas enviadas por correo con éxito ✉️")
        user_data[user_id] = []
    except Exception as e:
        await update.message.reply_text("Error al enviar correo: " + str(e))

# === Iniciar bot (versión 20.7) ===
if __name__ == '__main__':
    import asyncio
    import nest_asyncio

    nest_asyncio.apply()

    async def main():
        logging.basicConfig(level=logging.INFO)
        application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
        application.add_handler(MessageHandler(filters.PHOTO, manejar_imagen))
        application.add_handler(CallbackQueryHandler(manejar_seleccion_proyecto))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, manejar_tipo_gasto))
        application.add_handler(CommandHandler("enviar", comando_enviar))
        await application.run_polling()

    asyncio.get_event_loop().run_until_complete(main())

