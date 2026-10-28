"""Registro local del bot (SQLite dentro del contenedor).

Idea clave de integración: cada sistema es dueño de su información.
El chat_id de Telegram NO viaja a ArcGIS; vive aquí. ArcGIS solo guarda el reporte.
Así la capa puede mostrarse públicamente sin exponer datos de los usuarios.
"""
import sqlite3
import threading
from datetime import datetime


class Store:
    def __init__(self, ruta: str):
        self._lock = threading.Lock()
        self.db = sqlite3.connect(ruta, check_same_thread=False)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS reportes (
                oid INTEGER PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                categoria TEXT,
                estado TEXT,
                creado TEXT
            )""")
        self.db.commit()

    def guardar(self, oid: int, chat_id: int, categoria: str, estado: str = "Nuevo"):
        with self._lock:
            self.db.execute("INSERT OR REPLACE INTO reportes VALUES (?,?,?,?,?)",
                            (oid, chat_id, categoria, estado,
                             datetime.now().isoformat(timespec="seconds")))
            self.db.commit()

    def de_usuario(self, chat_id: int) -> list[tuple]:
        with self._lock:
            return self.db.execute(
                "SELECT oid, categoria, estado FROM reportes WHERE chat_id=? ORDER BY oid DESC LIMIT 10",
                (chat_id,)).fetchall()

    def pendientes(self) -> list[tuple]:
        """Reportes que aún no se han cerrado (para vigilar cambios de estado)."""
        with self._lock:
            return self.db.execute(
                "SELECT oid, chat_id, categoria, estado FROM reportes WHERE estado != 'Atendido'"
            ).fetchall()

    def actualizar_estado(self, oid: int, estado: str):
        with self._lock:
            self.db.execute("UPDATE reportes SET estado=? WHERE oid=?", (estado, oid))
            self.db.commit()
