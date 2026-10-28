import os
import csv
import io
import requests
from fastapi import FastAPI, Request, Response
from openai import OpenAI

app = FastAPI(title="Chatbot ONAJUP - Justicia de Paz")

# Inicialización del cliente de OpenAI
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Variables de entorno cargadas desde el servidor (Render)
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "onajup_2026")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
SHEET_CSV_URL = os.getenv("SHEET_CSV_URL")


def obtener_datos_sheets() -> list:
    """Lee las opciones y respuestas desde Google Sheets en tiempo real."""
    try:
        res = requests.get(SHEET_CSV_URL, timeout=10)
        res.encoding = 'utf-8'
        reader = csv.DictReader(io.StringIO(res.text))
        return list(reader)
    except Exception as e:
        print(f"Error al leer Google Sheets: {e}")
        return []


def consultar_openai_estricto(pregunta_juez: str, datos_sheets: list) -> str:
    """
    Envía la consulta del Juez a OpenAI exigiendo estrictamente la norma legal
    basada únicamente en la base de conocimientos de Google Sheets.
    """
    # Construir la base de conocimiento en texto plano a partir de Sheets
    conocimiento = "BASE DE DATOS NORMATIVA OFICIAL DE ONAJUP:\n\n"
    for row in datos_sheets:
        conocimiento += f"Tema/Título: {row.get('Titulo_Boton', '')}\n"
        conocimiento += f"Palabras Clave: {row.get('Descripcion_Corta', '')}\n"
        conocimiento += f"Norma y Respuesta Oficial: {row.get('Respuesta_Texto', '')}\n"
        conocimiento += "-" * 40 + "\n"

    system_prompt = f"""
    Eres el Asistente Legal Oficial de la ONAJUP (Poder Judicial del Perú). 
    Tu función principal es ENTREGAR LA NORMA LEGAL EXACTA Y OFICIAL al Juez de Paz que realiza la consulta.

    REGLAS DE CUMPLIMIENTO OBLIGATORIO Y ESTRICTO:
    1. Toda respuesta DEBE fundamentarse e iniciar citando la BASE LEGAL O NORMA EXACTA (Ley N° 29824, Reglamento o Directivas) disponible en la base de datos.
    2. Utiliza ÚNICAMENTE la información oficial provista a continuación:
    {conocimiento}

    3. Si la consulta del Juez no tiene una norma o procedimiento correspondiente dentro de la base provista, responde EXACTAMENTE:
       "⚠️ No se encontró una norma o directiva específica en la base oficial para esta consulta. Por favor, remita su caso formalmente a la ONAJUP vía correo: onajup@pj.gob.pe."
    
    4. ESTÁ ESTRICTAMENTE PROHIBIDO:
       - Inventar o inferir artículos, leyes, plazos o procedimientos no expuestos en la base.
       - Emitir opiniones personales o jurídicas fuera del texto entregado.
       - Responder sin mencionar la norma legal o procedimiento oficial.
    """

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": pregunta_juez}
            ],
            temperature=0.0  # Temperatura 0 para cero creatividad y precisión estricta
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"Error consultando OpenAI: {e}")
        return "⚠️ Ocurrió un error al consultar la base normativa. Intente nuevamente en unos minutos."


def transcribir_audio_groq(media_id: str) -> str:
    """Descarga la nota de voz de Meta y la convierte a texto con Groq (Whisper)."""
    try:
        url_media = f"https://graph.facebook.com/v18.0/{media_id}"
        headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
        res_media = requests.get(url_media, headers=headers).json()
        download_url = res_media.get("url")

        audio_bytes = requests.get(download_url, headers=headers).content

        res_groq = requests.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            files={"file": ("audio.ogg", audio_bytes, "audio/ogg")},
            data={"model": "whisper-large-v3", "language": "es"},
            timeout=15
        )
        return res_groq.json().get("text", "")
    except Exception as e:
        print(f"Error en transcripción de audio: {e}")
        return ""


@app.get("/webhook")
async def verify_webhook(request: Request):
    """Verificación inicial requerida por Meta."""
    params = request.query_params
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == VERIFY_TOKEN:
        return Response(content=params.get("hub.challenge"), status_code=200)
    return Response(status_code=403)


@app.post("/webhook")
async def receive_whatsapp(request: Request):
    """Recepción de mensajes desde WhatsApp."""
    body = await request.json()
    try:
        entry = body['entry'][0]['changes'][0]['value']
        if 'messages' in entry:
            message = entry['messages'][0]
            from_number = message['from']
            msg_type = message['type']
            
            datos_sheets = obtener_datos_sheets()
            texto_consulta = ""

            # Caso A: El juez envió un mensaje de texto
            if msg_type == 'text':
                texto_consulta = message['text']['body'].strip()

            # Caso B: El juez envió una nota de voz
            elif msg_type == 'audio':
                media_id = message['audio']['id']
                texto_consulta = transcribir_audio_groq(media_id).strip()

            # Caso C: El juez seleccionó un botón/opción del menú interactivo
            elif msg_type == 'interactive':
                opcion_id = message['interactive']['list_reply']['id']
                for row in datos_sheets:
                    if row.get('ID_Opcion') == opcion_id:
                        enviar_respuesta_con_boton_volver(from_number, row.get('Respuesta_Texto', ''))
                        return {"status": "ok"}

            # Evaluar texto o nota de voz enviada por el juez
            if texto_consulta:
                # Si el usuario solo saluda o pide el menú
                if texto_consulta.lower() in ["hola", "menu", "menú", "inicio", "opciones"]:
                    enviar_menu_lista(from_number, datos_sheets)
                else:
                    # Procesar consulta con OpenAI exigiendo entrega estricta de la norma
                    respuesta_normativa = consultar_openai_estricto(texto_consulta, datos_sheets)
                    enviar_respuesta_con_boton_volver(from_number, respuesta_normativa)
            else:
                enviar_menu_lista(from_number, datos_sheets)

    except Exception as e:
        print(f"Error procesando webhook: {e}")

    return {"status": "ok"}


def enviar_menu_lista(to_number: str, datos_sheets: list):
    """Envía un menú de lista interactiva desplegable a WhatsApp."""
    rows = []
    for row in datos_sheets[:10]:  # Meta permite hasta 10 opciones por lista
        rows.append({
            "id": row.get("ID_Opcion", "opt_1"),
            "title": row.get("Titulo_Boton", "Consulta")[:24],
            "description": row.get("Descripcion_Corta", "")[:72]
        })

    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": "⚖️ ONAJUP - Justicia de Paz"},
            "body": {"text": "Marque una opción o envíe una nota de voz/texto con su consulta:"},
            "footer": {"text": "Poder Judicial del Perú"},
            "action": {
                "button": "Ver Opciones",
                "sections": [{"title": "Temas Disponibles", "rows": rows}]
            }
        }
    }
    requests.post(url, json=payload, headers=headers)


def enviar_respuesta_con_boton_volver(to_number: str, texto_respuesta: str):
    """Envía la respuesta seleccionada junto con el botón para regresar al menú."""
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": texto_respuesta},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": "volver_menu", "title": "🔄 Menú Principal"}}
                ]
            }
        }
    }
    requests.post(url, json=payload, headers=headers)