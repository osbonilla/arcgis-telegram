"""Agrega el campo 'urgencia' a una capa Reportes ya creada (para usar la IA).

Uso (una sola vez):
    docker compose run --rm bot python actualizar_capa.py

Si el campo ya existe, no hace nada.
"""
import json
import os
import sys

from arcgis_client import ArcGISClient

layer_url = os.getenv("ARCGIS_LAYER_URL", "").rstrip("/")
if not layer_url:
    sys.exit("Define ARCGIS_LAYER_URL en .env")

cli = ArcGISClient(os.getenv("ARCGIS_PORTAL", "https://www.arcgis.com"), layer_url,
                   os.getenv("ARCGIS_USERNAME", ""), os.getenv("ARCGIS_PASSWORD", ""),
                   os.getenv("ARCGIS_API_KEY", ""))

if "urgencia" in cli.campos():
    print("✔ La capa ya tiene el campo 'urgencia'. Nada que hacer.")
    sys.exit(0)

campo = {
    "name": "urgencia", "type": "esriFieldTypeString", "alias": "Urgencia", "length": 10,
    "nullable": True, "editable": True,
    "domain": {"type": "codedValue", "name": "dom_urgencia", "codedValues": [
        {"name": "Alta", "code": "Alta"},
        {"name": "Media", "code": "Media"},
        {"name": "Baja", "code": "Baja"}]},
}
admin = layer_url.replace("/rest/services/", "/rest/admin/services/") + "/addToDefinition"
res = cli.post(admin, {"addToDefinition": json.dumps({"fields": [campo]})})
if not res.get("success"):
    sys.exit(f"No se pudo agregar el campo: {res}")
print("✔ Campo 'urgencia' agregado (Alta, Media, Baja).")
