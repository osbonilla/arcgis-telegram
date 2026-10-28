# 📱 Práctica: Telegram + ArcGIS

**Objetivo:** ver cómo dos sistemas que no se conocen (Telegram y ArcGIS) quedan conectados por una integración, sin modificar ninguno de los dos.

## Parte 1: Sé un ciudadano (5 min)
1. Escanea el QR o abre el link del bot que está en pantalla y toca **Iniciar**.
2. Toca **📍 Nuevo reporte** (o escribe `/reportar`).
3. Elige una categoría.
4. Toca **📍 Enviar mi ubicación**. También puedes marcar otro punto con 📎 → Ubicación.
5. Envía una foto (o toca *Saltar*).
6. Escribe una descripción corta.
7. 👀 Mira el Dashboard proyectado: **tu punto rojo ya está ahí**.
8. Espera… cuando el "técnico" atienda tu reporte, te llegará un mensaje al celular.

## Parte 2: Piensa como arquitecto (2 min)
Responde con tu compañero:
- ¿Qué sistema guarda **tu identidad de Telegram**? ¿Y cuál guarda **el reporte**?
- ¿Qué operaciones de la **REST API de ArcGIS** tuvo que llamar el bot? *Pista: crear el punto, subir la foto, consultar el estado.*
- ¿En qué **nivel de integración** (aplicación, servicios o datos) ubicarías esta solución? ¿Por qué?
- ¿Qué otro sistema conectarías con ArcGIS de la misma forma? WhatsApp, Discord, un ERP…

## Parte 3 (opcional): Tu propio bot
1. En Telegram: @BotFather → `/newbot` → copia el token.
2. Clona el repo (o ábrelo en GitHub Codespaces).
3. Ejecuta `cp .env.example .env` y pega tu token, más la URL de la capa y la API key de edición (`ARCGIS_API_KEY`) que te da el profe.
4. Ejecuta `docker compose up --build`.
5. Reto: agrega la categoría **🌳 Árbol caído** en `bot/app/main.py`.
