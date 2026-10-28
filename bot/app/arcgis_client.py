"""Cliente mínimo para la REST API de ArcGIS (Online o Enterprise).

Este es el "middleware": traduce lo que llega de Telegram (lat/lon, texto, foto)
a operaciones del Feature Service (addFeatures, addAttachment, query).
"""
import json
import math
import time

import requests


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
    def agregar_reporte(self, lon: float, lat: float, atributos: dict) -> int:
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
