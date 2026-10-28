"""Crea la Hosted Feature Layer 'Reportes' en ArcGIS Online (se ejecuta una sola vez).

Uso:
    docker compose run --rm bot python crear_capa.py

Usa ARCGIS_USERNAME / ARCGIS_PASSWORD del archivo .env.
Al terminar imprime la URL de la capa para pegarla en ARCGIS_LAYER_URL.
"""
import json
import os
import sys

from arcgis_client import ArcGISClient, lonlat_a_webmercator

NOMBRE = os.getenv("NOMBRE_SERVICIO", "Reportes_Ciudadanos_Telegram")
portal = os.getenv("ARCGIS_PORTAL", "https://www.arcgis.com").rstrip("/")
usuario = os.getenv("ARCGIS_USERNAME", "")
cli = ArcGISClient(portal, username=usuario, password=os.getenv("ARCGIS_PASSWORD", ""))

if not usuario:
    sys.exit("Define ARCGIS_USERNAME y ARCGIS_PASSWORD en .env")

# Extensión inicial: Ecuador continental
sw, ne = lonlat_a_webmercator(-81.2, -5.1), lonlat_a_webmercator(-75.1, 1.5)
extent = {"xmin": sw["x"], "ymin": sw["y"], "xmax": ne["x"], "ymax": ne["y"],
          "spatialReference": {"wkid": 102100}}

# 1) Crear el servicio vacío -----------------------------------------------
print(f"Creando servicio '{NOMBRE}'…")
crear = cli.post(f"{portal}/sharing/rest/content/users/{usuario}/createService", {
    "outputType": "featureService",
    "createParameters": json.dumps({
        "name": NOMBRE,
        "serviceDescription": "Reportes ciudadanos enviados desde un bot de Telegram",
        "hasStaticData": False,
        "maxRecordCount": 2000,
        "supportedQueryFormats": "JSON",
        "capabilities": "Create,Delete,Query,Update,Editing",
        "spatialReference": {"wkid": 102100},
        "initialExtent": extent,
        "allowGeometryUpdates": True,
        "units": "esriMeters",
        "xssPreventionInfo": {"xssPreventionEnabled": True,
                              "xssPreventionRule": "InputOnly",
                              "xssInputRule": "rejectInvalid"},
    }),
})
if not crear.get("success"):
    sys.exit(f"No se pudo crear el servicio: {crear}")
service_url = crear["serviceurl"]
print("  ✔ Servicio:", service_url)

# 2) Agregar la capa con sus campos ---------------------------------------
color = lambda r, g, b: {"type": "esriSMS", "style": "esriSMSCircle", "size": 11,  # noqa: E731
                         "color": [r, g, b, 230],
                         "outline": {"type": "esriSLS", "style": "esriSLSSolid",
                                     "color": [255, 255, 255, 255], "width": 1.2}}
capa = {
    "id": 0,
    "name": "Reportes",
    "type": "Feature Layer",
    "displayField": "categoria",
    "description": "Reportes ciudadanos (Telegram → ArcGIS)",
    "geometryType": "esriGeometryPoint",
    "hasZ": False, "hasM": False,
    "objectIdField": "OBJECTID",
    "globalIdField": "GlobalID",
    "hasAttachments": True,
    "capabilities": "Create,Delete,Query,Update,Editing",
    "allowGeometryUpdates": True,
    "extent": extent,
    "fields": [
        {"name": "OBJECTID", "type": "esriFieldTypeOID", "alias": "ID", "nullable": False, "editable": False},
        {"name": "GlobalID", "type": "esriFieldTypeGlobalID", "alias": "GlobalID", "length": 38,
         "nullable": False, "editable": False},
        {"name": "categoria", "type": "esriFieldTypeString", "alias": "Categoría", "length": 50,
         "nullable": True, "editable": True},
        {"name": "descripcion", "type": "esriFieldTypeString", "alias": "Descripción", "length": 500,
         "nullable": True, "editable": True},
        {"name": "estado", "type": "esriFieldTypeString", "alias": "Estado", "length": 20,
         "nullable": True, "editable": True, "defaultValue": "Nuevo",
         "domain": {"type": "codedValue", "name": "dom_estado", "codedValues": [
             {"name": "Nuevo", "code": "Nuevo"},
             {"name": "En proceso", "code": "En proceso"},
             {"name": "Atendido", "code": "Atendido"}]}},
        {"name": "fecha", "type": "esriFieldTypeDate", "alias": "Fecha", "length": 8,
         "nullable": True, "editable": True},
        {"name": "fuente", "type": "esriFieldTypeString", "alias": "Fuente", "length": 30,
         "nullable": True, "editable": True},
    ],
    "drawingInfo": {"renderer": {
        "type": "uniqueValue", "field1": "estado",
        "defaultSymbol": color(150, 150, 150), "defaultLabel": "Otro",
        "uniqueValueInfos": [
            {"value": "Nuevo", "label": "Nuevo", "symbol": color(230, 57, 70)},
            {"value": "En proceso", "label": "En proceso", "symbol": color(244, 162, 97)},
            {"value": "Atendido", "label": "Atendido", "symbol": color(42, 157, 143)},
        ]}},
    "templates": [{"name": "Reporte", "description": "",
                   "prototype": {"attributes": {"estado": "Nuevo", "fuente": "Manual"}},
                   "drawingTool": "esriFeatureEditToolPoint"}],
}
admin_url = service_url.replace("/rest/services/", "/rest/admin/services/") + "/addToDefinition"
res = cli.post(admin_url, {"addToDefinition": json.dumps({"layers": [capa]})})
if not res.get("success"):
    sys.exit(f"No se pudo agregar la capa: {res}")

layer_url = service_url + "/0"
print("  ✔ Capa creada\n")
print("Copia esta línea en tu archivo .env:\n")
print(f"ARCGIS_LAYER_URL={layer_url}\n")
print(f"Ítem en ArcGIS Online: {portal}/home/item.html?id={crear['itemId']}")
