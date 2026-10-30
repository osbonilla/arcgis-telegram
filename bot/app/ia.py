"""IA del bot: un servicio más que el bot orquesta junto con ArcGIS.

Dos proveedores intercambiables (se elige con IA_PROVEEDOR en el .env):
  - ollama: modelo local (p. ej. qwen2.5:7b) corriendo en tu computadora. Sin costo ni nube.
  - gemini: modelo de Google en la nube (entiende también audios).

Dos usos:
  1. interpretar(): convierte un mensaje libre en datos estructurados
     (¿es reporte o pregunta? categoría, urgencia, descripción, filtros de consulta).
  2. redactar_respuesta(): convierte el resultado de una consulta a ArcGIS en lenguaje natural.

La IA nunca toca ArcGIS directamente: propone valores de listas cerradas y el bot
arma la consulta REST, así no puede inventar SQL.
"""
import json
import logging
import os
import time

import requests

log = logging.getLogger("ia")

CATEGORIAS = ["Bache", "Alumbrado", "Basura", "Agua", "Semaforo", "Otro"]
URGENCIAS = ["Alta", "Media", "Baja"]

INSTRUCCIONES = """Eres el asistente de un bot de Telegram de reportes ciudadanos conectado a ArcGIS,
usado en una ciudad de Ecuador. Recibes un mensaje de un ciudadano y lo clasificas:

- "reporte": describe un problema urbano para registrar (un bache, una luminaria apagada,
  basura acumulada, una fuga de agua o alcantarilla, un semáforo dañado, etc.).
- "pregunta": pide información sobre los reportes existentes (cuántos hay, cuáles, dónde,
  cuáles están pendientes, qué tipo es más frecuente, cuáles están cerca, etc.).
- "otro": saludo, conversación o cualquier otra cosa.

Si es reporte: elige la categoría más adecuada, estima la urgencia (Alta si hay riesgo para
personas o vehículos, Media si afecta el uso normal, Baja si es menor) y escribe una descripción
clara en español de máximo 200 caracteres, en tercera persona, sin inventar datos.

Si es pregunta: traduce la pregunta a filtros. Usa "Todas"/"Todos" cuando no se filtra.
"Pendientes" = todo lo que no está Atendido. cerca_de_mi=true solo si pregunta por algo
cercano a él ("cerca", "por aquí", "a mi alrededor"); radio_m por defecto 500.
operacion: "contar" si pregunta cuántos; "listar" si pide ver cuáles o los últimos;
"agrupar" si pregunta qué tipo/estado/urgencia es más común o pide un desglose.
ultimas_horas: 0 si no menciona tiempo; 1 para "última hora"; 24 para "hoy".

Si es otro: escribe en "respuesta" algo breve y amable (máximo 2 frases) explicando que puede
contarte un problema o preguntarte por los reportes.

Siempre llena "transcripcion" con el texto del mensaje (transcrito si era audio).
Los campos que no apliquen al tipo de mensaje llénalos con un valor cualquiera válido."""

# Esquema en formato JSON Schema estándar (Ollama). Para Gemini se convierte abajo.
ESQUEMA = {
    "type": "object",
    "properties": {
        "tipo": {"type": "string", "enum": ["reporte", "pregunta", "otro"]},
        "transcripcion": {"type": "string"},
        "categoria": {"type": "string", "enum": CATEGORIAS},
        "urgencia": {"type": "string", "enum": URGENCIAS},
        "descripcion": {"type": "string"},
        "operacion": {"type": "string", "enum": ["contar", "listar", "agrupar"]},
        "filtro_categoria": {"type": "string", "enum": CATEGORIAS + ["Todas"]},
        "filtro_estado": {"type": "string", "enum": ["Nuevo", "En proceso", "Atendido", "Pendientes", "Todos"]},
        "filtro_urgencia": {"type": "string", "enum": URGENCIAS + ["Todas"]},
        "agrupar_por": {"type": "string", "enum": ["categoria", "estado", "urgencia", "ninguno"]},
        "cerca_de_mi": {"type": "boolean"},
        "radio_m": {"type": "integer"},
        "ultimas_horas": {"type": "integer"},
        "respuesta": {"type": "string"},
    },
}
ESQUEMA["required"] = list(ESQUEMA["properties"])  # todos: más estable con modelos pequeños


def esquema_gemini(s: dict) -> dict:
    """Convierte el JSON Schema al formato de Gemini (tipos en mayúscula, enums marcados)."""
    out = {}
    for k, v in s.items():
        if k == "type":
            out[k] = v.upper()
        elif k == "properties":
            out[k] = {n: esquema_gemini(p) for n, p in v.items()}
        else:
            out[k] = v
    if "enum" in s:
        out["format"] = "enum"
    return out


PROMPT_RESPUESTA = (
    "Responde la pregunta de un ciudadano usando SOLO estos datos reales de ArcGIS.\n"
    "Pregunta: {pregunta}\nConsulta ejecutada: {consulta}\nResultado (JSON): {resultado}\n\n"
    "Reglas: español, tono amable, máximo 3 frases, da números concretos, "
    "no inventes nada que no esté en el resultado, sin formato Markdown. "
    "Si el resultado está vacío, dilo con naturalidad."
)


class IAError(RuntimeError):
    pass


class BaseIA:
    nombre = "?"
    soporta_audio = False

    def _json(self, texto: str | None, audio: bytes | None, mime_audio: str) -> str:
        raise NotImplementedError

    def _texto(self, prompt: str) -> str:
        raise NotImplementedError

    def interpretar(self, texto: str | None = None, audio: bytes | None = None,
                    mime_audio: str = "audio/ogg") -> dict:
        if audio and not self.soporta_audio:
            raise IAError("Este modelo no entiende audios")
        try:
            datos = json.loads(self._json(texto, audio, mime_audio))
        except json.JSONDecodeError as e:
            raise IAError(f"La IA no devolvió JSON válido: {e}") from e
        # Saneamiento: solo valores de las listas cerradas
        if datos.get("tipo") not in ("reporte", "pregunta", "otro"):
            datos["tipo"] = "otro"
        if datos.get("categoria") not in CATEGORIAS:
            datos["categoria"] = "Otro"
        if datos.get("urgencia") not in URGENCIAS:
            datos["urgencia"] = "Media"
        datos["transcripcion"] = datos.get("transcripcion") or texto or ""
        datos["descripcion"] = (datos.get("descripcion") or datos["transcripcion"])[:500]
        return datos

    def redactar_respuesta(self, pregunta: str, consulta: str, resultado: dict) -> str:
        return self._texto(PROMPT_RESPUESTA.format(
            pregunta=pregunta, consulta=consulta,
            resultado=json.dumps(resultado, ensure_ascii=False))).strip()

    def precargar(self) -> None:
        """Opcional: deja el modelo listo en memoria antes del primer mensaje."""


# ------------------------------------------------------------------ Ollama
class OllamaIA(BaseIA):
    """Modelo local servido por Ollama (por defecto en la computadora anfitriona)."""

    def __init__(self, url: str, modelo: str):
        self.url = url.rstrip("/")
        self.modelo = modelo
        self.nombre = f"{modelo} (local, Ollama)"

    def _chat(self, mensajes: list, formato=None, temperatura=0.1) -> str:
        cuerpo = {"model": self.modelo, "messages": mensajes, "stream": False,
                  "keep_alive": "60m",  # mantiene el modelo cargado durante la clase
                  "options": {"temperature": temperatura}}
        if formato:
            cuerpo["format"] = formato
        try:
            r = requests.post(f"{self.url}/api/chat", json=cuerpo, timeout=180)
        except requests.RequestException as e:
            raise IAError(f"No se pudo conectar con Ollama en {self.url}: {e}") from e
        if r.status_code != 200:
            raise IAError(f"Ollama respondió {r.status_code}: {r.text[:300]}")
        return r.json()["message"]["content"]

    def _json(self, texto, audio, mime_audio):
        return self._chat([{"role": "system", "content": INSTRUCCIONES},
                           {"role": "user", "content": texto or ""}],
                          formato=ESQUEMA)

    def _texto(self, prompt):
        return self._chat([{"role": "user", "content": prompt}], temperatura=0.3)

    def precargar(self):
        inicio = time.time()
        try:
            r = requests.post(f"{self.url}/api/generate",
                              json={"model": self.modelo, "prompt": "", "keep_alive": "60m"}, timeout=300)
            r.raise_for_status()
            log.info("Modelo local %s listo en %.0f s", self.modelo, time.time() - inicio)
        except requests.RequestException as e:
            log.warning("No se pudo precargar %s en %s: %s", self.modelo, self.url, e)


# ------------------------------------------------------------------ Gemini
class GeminiIA(BaseIA):
    soporta_audio = True

    def __init__(self, api_key: str, modelo: str):
        from google import genai  # solo se importa si se usa Gemini
        from google.genai import types
        self.types = types
        self.modelo = modelo
        self.nombre = f"{modelo} (Gemini)"
        self.cliente = genai.Client(api_key=api_key)

    def _generar(self, partes, config) -> str:
        for intento in range(3):
            try:
                r = self.cliente.models.generate_content(model=self.modelo, contents=partes, config=config)
                break
            except Exception as e:  # noqa: BLE001
                # 429 = límite por minuto, 503 = servicio ocupado (402 = sin saldo: no se reintenta)
                if str(e).startswith(("429", "503")) and intento < 2:
                    time.sleep(3 * (intento + 1))
                    continue
                log.warning("Gemini falló: %s", e)
                raise IAError(str(e)) from e
        if not r.text:
            raise IAError("Gemini no devolvió texto")
        return r.text

    def _json(self, texto, audio, mime_audio):
        partes = []
        if audio:
            partes.append(self.types.Part.from_bytes(data=audio, mime_type=mime_audio))
            partes.append("Mensaje de voz del ciudadano (adjunto). Interprétalo.")
        if texto:
            partes.append(f"Mensaje del ciudadano: {texto}")
        cfg = self.types.GenerateContentConfig(
            system_instruction=INSTRUCCIONES, response_mime_type="application/json",
            response_schema=esquema_gemini(ESQUEMA), temperature=0.1)
        return self._generar(partes, cfg)

    def _texto(self, prompt):
        return self._generar([prompt], self.types.GenerateContentConfig(temperature=0.3))


# ------------------------------------------------------------------ fábrica
def crear_ia() -> BaseIA | None:
    """Elige el proveedor según el .env. Sin configuración, el bot funciona sin IA."""
    proveedor = os.getenv("IA_PROVEEDOR", "").strip().lower()
    if not proveedor:  # compatibilidad: si hay key de Gemini, se usa Gemini
        proveedor = "gemini" if os.getenv("GEMINI_API_KEY", "").strip() else "ninguna"
    if proveedor == "ollama":
        return OllamaIA(os.getenv("OLLAMA_URL", "http://host.docker.internal:11434"),
                        os.getenv("OLLAMA_MODEL", "qwen2.5:7b"))
    if proveedor == "gemini" and os.getenv("GEMINI_API_KEY", "").strip():
        return GeminiIA(os.getenv("GEMINI_API_KEY").strip(),
                        os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite").strip())
    return None
