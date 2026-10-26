import os
import csv
import io
import requests
from fastapi import FastAPI, Request, Response

app = FastAPI(title="Chatbot ONAJUP - Justicia de Paz")

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


def transcribir_audio_groq(media_id: str) -> str:
    """Descarga la nota de voz de Meta y la convierte a texto con Groq (Whisper)."""
    try:
        # 1. Obtener URL del archivo desde Meta
        url_media = f"https://graph.facebook.com/v18.0/{media_id}"
        headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
        res_media = requests.get(url_media, headers=headers).json()
        download_url = res_media.get("url")

        # 2. Descargar bytes del audio
        audio_bytes = requests.get(download_url, headers=headers).content

        # 3. Transcribir con Whisper en Groq (Gratis)
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
                texto_consulta = message['text']['body'].strip().lower()

            # Caso B: El juez envió una nota de voz
            elif msg_type == 'audio':
                media_id = message['audio']['id']
                texto_consulta = transcribir_audio_groq(media_id).strip().lower()

            # Caso C: El juez seleccionó un botón/opción del menú interactivo
            elif msg_type == 'interactive':
                opcion_id = message['interactive']['list_reply']['id']
                for row in datos_sheets:
                    if row.get('ID_Opcion') == opcion_id:
                        enviar_respuesta_con_boton_volver(from_number, row.get('Respuesta_Texto', ''))
                        return {"status": "ok"}

            # Evaluar texto o voz transcrita
            if texto_consulta:
                # Buscar coincidencia semántica/palabra clave en la hoja
                respuesta_hallada = None
                for row in datos_sheets:
                    palabras_clave = [p.strip().lower() for p in row.get('Descripcion_Corta', '').split(',')]
                    if any(kw in texto_consulta for kw in palabras_clave if len(kw) > 2):
                        respuesta_hallada = row.get('Respuesta_Texto')
                        break

                if respuesta_hallada:
                    enviar_respuesta_con_boton_volver(from_number, respuesta_hallada)
                else:
                    enviar_menu_lista(from_number, datos_sheets)
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
            "body": {"text": "Marque una de las siguientes opciones o envíe una nota de voz con su consulta:"},
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