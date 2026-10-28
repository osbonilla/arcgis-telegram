# 📍 Telegram + ArcGIS — Reportes ciudadanos en vivo

Kit práctico para la charla **"¿ArcGIS + …?"**: un bot de Telegram que cualquier persona usa desde su celular. Lo que envía (ubicación, foto y descripción) aparece al instante en ArcGIS Online. Cuando un técnico lo atiende en Field Maps, el bot le avisa al ciudadano.

```
 Ciudadano                Middleware (Docker)                 ArcGIS Online
┌───────────┐  ubicación  ┌──────────────────────┐ addFeatures ┌─────────────────────┐
│ Telegram  │ ──foto────▶ │  bot-telegram-arcgis │ ──────────▶ │ Hosted Feature Layer│──▶ Dashboard
│ (celular) │  texto      │  Python · REST API   │ addAttachmt │  "Reportes"         │──▶ Field Maps
│           │ ◀────────── │  SQLite: chat_id     │ ◀────query─ │  estado: Nuevo →    │    (técnico)
└───────────┘ "¡Atendido!"└──────────────────────┘             │  En proceso→Atendido│
                                                               └─────────────────────┘
```

## Qué nivel de integración muestra cada pieza (diapositivas de la charla)

| Pieza | Nivel / patrón | Diapositiva |
|---|---|---|
| Telegram como interfaz del ciudadano: ArcGIS queda "invisible" | **Aplicación**: llevar ArcGIS a otros sistemas | 19–20, 24 |
| El bot llama a `addFeatures`, `addAttachment` y `query` | **Servicios**: ArcGIS REST APIs | 26 |
| Field Maps cambia el estado y el bot avisa por Telegram | **Flujo de trabajo**: integración en ambos sentidos | 21–22, 30 |
| El `chat_id` vive en el bot y el reporte vive en ArcGIS | *"Cada sistema sigue siendo dueño de su información"* | 17 |
| Dashboard y Field Maps sobre la misma capa | Todo en una misma pantalla, un mismo registro | 24–25 |

> **Toda la clase usa el mismo bot.** Atiende hasta 32 conversaciones en paralelo. Con el enlace `https://t.me/TU_USUARIO_bot?start=reportar` (o su QR) cada estudiante entra directo a reportar. Si configuras `GRUPO_CHAT_ID`, el bot publica cada reporte nuevo y cada reporte atendido en el grupo de Telegram de la clase. En el grupo, escribe `/chatid` para obtener ese id.

> **¿Por qué no hace falta una URL pública ni un túnel?** El bot usa *long polling*: es él quien pregunta a Telegram y quien escribe en ArcGIS. Funciona desde cualquier laptop con internet.

---

## 1. Preparación (una sola vez, unos 15 minutos)

### 1.1 Crear el bot en Telegram
1. En Telegram, abre **@BotFather** y envía `/newbot`.
2. Ponle un nombre (por ejemplo *Reportes Ciudad*) y un usuario que termine en `bot` (por ejemplo `ReportesClaseGIS_bot`).
3. Copia el **token** que te entrega.
4. Opcional: con `/setcommands` registra:
   ```
   reportar - Crear un nuevo reporte
   misreportes - Ver el estado de mis reportes
   cancelar - Cancelar el reporte en curso
   ```
5. Opcional: con `/setuserpic` ponle una foto al bot.

### 1.2 Configurar
```bash
cp .env.example .env
# edita .env: TELEGRAM_TOKEN, ARCGIS_USERNAME, ARCGIS_PASSWORD
```

### 1.3 Crear la capa en ArcGIS Online
```bash
docker compose run --rm bot python crear_capa.py
```
El script crea la Hosted Feature Layer **Reportes** con:
- los campos `categoria`, `descripcion`, `estado`, `fecha` y `fuente`;
- un dominio para `estado` con los valores Nuevo, En proceso y Atendido;
- adjuntos habilitados;
- simbología por estado: 🔴 Nuevo, 🟠 En proceso, 🟢 Atendido.

Al final imprime `ARCGIS_LAYER_URL=…`. Pega esa línea en `.env`.

### 1.4 Levantar el bot
```bash
docker compose up -d --build
docker compose logs -f     # para ver en vivo cada reporte que llega
```
Abre tu bot en Telegram, envía `/start` y haz un reporte de prueba.

### 1.5 Armar las vistas en ArcGIS Online
1. **Mapa web:** abre la capa en Map Viewer, ajusta el popup para que muestre la categoría, la descripción, el estado y la foto, y guárdala como *Mapa Reportes Ciudadanos*.
2. **Dashboard:** crea uno con:
   - un mapa,
   - un indicador con el total de reportes,
   - un gráfico por categoría,
   - un gráfico o indicador por estado,
   - una lista de los últimos reportes.

   En cada elemento configura la **actualización automática cada 0,1 min** (Configuración de la capa → Actualizar datos).
3. **Field Maps:** el mismo mapa web ya funciona en Field Maps. Asegúrate de que la capa permita editar (Configuración → Editar → Actualizar). Ese es el rol del "técnico".
4. **Opcional, para que los estudiantes vean el mapa en sus celulares:**
   - En la página del ítem de la capa, usa **Crear capa de vista**.
   - En la vista, desactiva la edición y compártela como **Público**.
   - Arma el Dashboard público sobre esa vista y pon su link en `MAPA_URL` del `.env`. El bot lo enviará después de cada reporte.

   La capa original sigue privada y editable. Como el `chat_id` nunca se guarda en ArcGIS, no se expone ningún dato personal.

---

## 2. Guion de la demo en clase (8 a 10 minutos)

1. **(1 min)** Proyecta el Dashboard vacío y muestra el QR del bot. Para generarlo, el link es `https://t.me/TU_USUARIO_bot`.
2. **(3 min)** Cada estudiante abre el bot y envía `/reportar`: elige una categoría, comparte su ubicación y manda una foto. Los puntos 🔴 empiezan a aparecer en el Dashboard frente a todos.
3. **(1 min)** Muestra `docker compose logs -f` y di: *"esto es el middleware: recibe Telegram y habla REST con ArcGIS"*.
4. **(2 min)** Abre Field Maps (o la tabla del Map Viewer) y cambia el estado de un reporte a **En proceso**, y luego a **Atendido**. En unos 15 segundos:
   - el punto cambia de color en el Dashboard;
   - al dueño del reporte le llega a su celular "🟢 ¡Tu reporte fue atendido!".

   Es el momento "wow".
5. **(1 min)** Cierre: *"Telegram no sabe nada de ArcGIS y ArcGIS no sabe nada de Telegram. No modificamos ninguno de los dos: los conectamos. Esto mismo, con Odoo, es nuestra demo de solicitudes ciudadanas."*

---

## 3. Retos para estudiantes avanzados

Cada uno crea su propio bot con @BotFather y corre este repo con Docker o en **GitHub Codespaces**, donde no hay nada que instalar.

| Reto | Qué tocar |
|---|---|
| Agregar la categoría "🌳 Árbol caído" | El diccionario `CATEGORIAS` en `main.py` |
| Preguntar la urgencia (Baja, Media o Alta) | Un nuevo estado en el `ConversationHandler` y un campo `urgencia` en la capa |
| Comando `/cerca`: reportes a menos de 500 m de mi ubicación | `query` con `geometry`, `distance=500` y `units=esriSRUnit_Meter` |
| Geocodificar: devolver la dirección del reporte | Llamar al servicio *reverseGeocode* de ArcGIS Location Services |
| IA: clasificar automáticamente el texto libre | Un LLM decide la categoría antes de llamar a `addFeatures` |

---

## 4. Estructura del proyecto

```
telegram-arcgis/
├── docker-compose.yml
├── .env.example
└── bot/
    ├── Dockerfile
    ├── requirements.txt
    └── app/
        ├── main.py            # conversación de Telegram + aviso de cambios de estado
        ├── arcgis_client.py   # middleware: token, addFeatures, addAttachment, query
        ├── store.py           # registro local reporte ↔ chat_id (SQLite)
        └── crear_capa.py      # crea la Hosted Feature Layer (createService + addToDefinition)
```

## 5. Problemas comunes

- **`No se pudo obtener token`:** si tu cuenta entra con SSO o MFA (inicio de sesión de la organización), `generateToken` no funciona. Tienes dos opciones:
  - usar una cuenta integrada de ArcGIS;
  - crear una **API key** (Developer credentials) con privilegios para crear y editar la capa, y ponerla en `ARCGIS_API_KEY`. En ese caso crea la capa a mano o con una cuenta integrada.
- **El botón "Enviar mi ubicación" no aparece:** solo existe en la app de celular. En la versión de escritorio, usa 📎 → Ubicación.
- **El Dashboard no se actualiza solo:** revisa el intervalo de actualización de la capa en cada elemento del Dashboard.
- **ArcGIS Enterprise:** cambia `ARCGIS_PORTAL` a `https://tu-servidor/portal`. El resto funciona igual.
