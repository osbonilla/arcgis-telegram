"""Cliente mínimo para la REST API de ArcGIS (Online o Enterprise).

Este es el "middleware": traduce lo que llega de Telegram (lat/lon, texto, foto)
a operaciones del Feature Service (addFeatures, addAttachment, query).
"""
import json
import math
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests


ZONA = ZoneInfo(os.getenv("ZONA_HORARIA", "America/Guayaquil"))


def lonlat_a_webmercator(lon: float, lat: float) -> dict:
    """Convierte WGS84 (lo que envía Telegram) a Web Mercator (wkid 102100)."""
    x = lon * 20037508.34 / 180.0
    y = math.log(math.tan((90.0 + lat) * math.pi / 360.0)) / (math.pi / 180.0)
    y = y * 20037508.34 / 180.0
    return {"x": x, "y": y, "spatialReference": {"wkid": 102100}}


class ArcGISError(RuntimeError):
    pass


class ArcGISClient:
    def __init__(self, portal: str, layer_url: str = "", username: str = "",
                 password: str = "", api_key: str = ""):
        self.portal = portal.rstrip("/")
        self.layer_url = layer_url.rstrip("/")
        self.username = username
        self.password = password
        self.api_key = api_key
        self._token = None
        self._expira = 0.0
        self.headers = {"Referer": self.portal}

    # ---------------------------------------------------------------- token
    def token(self) -> str:
        if self.api_key:
            return self.api_key
        if self._token and time.time() < self._expira - 120:
            return self._token
        r = requests.post(
            f"{self.portal}/sharing/rest/generateToken",
            data={
                "username": self.username,
                "password": self.password,
                "client": "referer",
                "referer": self.portal,
                "expiration": 120,  # minutos
                "f": "json",
            },
            headers=self.headers,
            timeout=30,
        )
        j = r.json()
        if "token" not in j:
            raise ArcGISError(f"No se pudo obtener token: {j.get('error', j)}")
        self._token = j["token"]
        self._expira = j["expires"] / 1000.0
        return self._token

    def post(self, url: str, data: dict, files=None) -> dict:
        """POST con reintento si el token expiró (códigos 498/499)."""
        for _ in range(2):
            payload = dict(data, f="json", token=self.token())
            r = requests.post(url, data=payload, files=files,
                              headers=self.headers, timeout=60)
            j = r.json()
            err = j.get("error")
            if err and err.get("code") in (498, 499) and not self.api_key:
                self._token = None
                continue
            if err:
                raise ArcGISError(f"{err.get('code')}: {err.get('message')} {err.get('details', '')}")
            return j
        raise ArcGISError("Token inválido tras reintento")

    # --------------------------------------------------------- operaciones
    def campos(self) -> set[str]:
        """Nombres de los campos de la capa (se consulta una sola vez)."""
        if not getattr(self, "_campos", None):
            info = self.post(self.layer_url, {})
            self._campos = {f["name"] for f in info.get("fields", [])}
        return self._campos

    def agregar_reporte(self, lon: float, lat: float, atributos: dict) -> int:
        # Solo se envían los atributos que existen en la capa (p. ej. 'urgencia' es opcional)
        existentes = self.campos()
        atributos = {k: v for k, v in atributos.items() if k in existentes and v is not None}
        feature = {"geometry": lonlat_a_webmercator(lon, lat), "attributes": atributos}
        j = self.post(f"{self.layer_url}/addFeatures",
                      {"features": json.dumps([feature]), "rollbackOnFailure": "true"})
        res = j["addResults"][0]
        if not res.get("success"):
            raise ArcGISError(f"addFeatures falló: {res.get('error')}")
        return res["objectId"]

    def agregar_foto(self, object_id: int, contenido: bytes, nombre: str = "foto.jpg") -> None:
        j = self.post(f"{self.layer_url}/{object_id}/addAttachment", {},
                      files={"attachment": (nombre, contenido, "image/jpeg")})
        res = j.get("addAttachmentResult", {})
        if not res.get("success"):
            raise ArcGISError(f"addAttachment falló: {res.get('error')}")

    def estados(self, object_ids: list[int]) -> dict[int, str]:
        if not object_ids:
            return {}
        j = self.post(f"{self.layer_url}/query", {
            "where": f"OBJECTID IN ({','.join(str(int(o)) for o in object_ids)})",
            "outFields": "OBJECTID,estado",
            "returnGeometry": "false",
        })
        return {f["attributes"]["OBJECTID"]: f["attributes"].get("estado")
                for f in j.get("features", [])}

    def consultar(self, where: str, operacion: str = "contar", agrupar_por: str | None = None,
                  cerca: tuple[float, float] | None = None, radio_m: int = 500,
                  limite: int = 5) -> dict:
        """Consulta la capa: contar, listar o agrupar, con filtro opcional por distancia.

        cerca = (lon, lat) en WGS84. Devuelve un dict pequeño, listo para dárselo a la IA.
        """
        params = {"where": where or "1=1", "returnGeometry": "false"}
        if cerca:
            params.update({
                "geometry": json.dumps({"x": cerca[0], "y": cerca[1]}),
                "geometryType": "esriGeometryPoint",
                "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
                "distance": str(int(radio_m)),
                "units": "esriSRUnit_Meter",
            })
        url = f"{self.layer_url}/query"

        if operacion == "agrupar" and agrupar_por in self.campos():
            params.update({
                "groupByFieldsForStatistics": agrupar_por,
                "outStatistics": json.dumps([{"statisticType": "count",
                                              "onStatisticField": "OBJECTID",
                                              "outStatisticFieldName": "total"}]),
                "orderByFields": "total DESC",
            })
            j = self.post(url, params)
            return {"grupos": {str(f["attributes"].get(agrupar_por)): f["attributes"]["total"]
                               for f in j.get("features", [])}}

        if operacion == "listar":
            campos = [c for c in ("OBJECTID", "categoria", "estado", "urgencia", "descripcion", "fecha")
                      if c in self.campos()]
            params.update({"outFields": ",".join(campos), "orderByFields": "fecha DESC",
                           "resultRecordCount": str(limite)})
            j = self.post(url, params)
            filas = []
            for f in j.get("features", []):
                a = dict(f["attributes"])
                if a.get("fecha"):
                    a["fecha"] = datetime.fromtimestamp(a["fecha"] / 1000, ZONA).strftime("%Y-%m-%d %H:%M")
                filas.append(a)
            conteo = {k: v for k, v in params.items()
                      if k not in ("outFields", "orderByFields", "resultRecordCount")}
            total = self.post(url, dict(conteo, returnCountOnly="true")).get("count", len(filas))
            return {"total": total, "mostrando": filas}

        params["returnCountOnly"] = "true"
        return {"total": self.post(url, params).get("count", 0)}
