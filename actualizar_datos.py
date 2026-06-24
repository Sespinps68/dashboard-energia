"""
ACTUALIZAR DATOS - Dashboard Energía
----------------------------------------
Este script se ejecuta automáticamente cada día (vía GitHub Actions).
Hace tres cosas:
1. Descarga el consumo/vertido más reciente de Datadis
2. Descarga los precios PVPC de esos mismos días desde ESIOS (REE)
3. Junta todo y lo guarda en data/historico.csv (sin duplicar lo que ya había)

No necesitas ejecutar esto a mano normalmente: GitHub Actions lo hace solo.
"""

import os
import csv
import requests
from datetime import datetime, timedelta

# Credenciales (vienen de los "Secrets" de GitHub, nunca están escritas aquí)
NIF = os.environ["DATADIS_NIF"]
PASSWORD = os.environ["DATADIS_PASSWORD"]
CUPS_OBJETIVO = "ES0021000011765921CS"  # El suministro de Torre Pacheco (con la instalación solar)

BASE_URL_DATADIS = "https://datadis.es"
RUTA_CSV = "data/historico.csv"


# ----------------------------
# DATADIS
# ----------------------------

def obtener_token():
    url = f"{BASE_URL_DATADIS}/nikola-auth/tokens/login"
    response = requests.post(
        url,
        data={"username": NIF, "password": PASSWORD},
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    response.raise_for_status()
    return response.text


def obtener_suministro_objetivo(token):
    """Busca, entre todos tus suministros, el que coincide con el CUPS que nos interesa."""
    url = f"{BASE_URL_DATADIS}/api-private/api/get-supplies"
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    suministros = response.json()

    for s in suministros:
        if s["cups"] == CUPS_OBJETIVO:
            return s
    raise Exception(f"No se encontró el CUPS {CUPS_OBJETIVO} entre tus suministros")


def obtener_consumo(token, suministro, fecha_inicio, fecha_fin):
    url = f"{BASE_URL_DATADIS}/api-private/api/get-consumption-data"
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "cups": suministro["cups"],
        "distributorCode": suministro["distributorCode"],
        "startDate": fecha_inicio,
        "endDate": fecha_fin,
        "measurementType": "0",
        "pointType": suministro["pointType"]
    }
    response = requests.get(url, headers=headers, params=params)
    response.raise_for_status()
    return response.json()


# ----------------------------
# ESIOS / REE - Precios PVPC
# ----------------------------

def obtener_precios_pvpc(fecha_inicio, fecha_fin):
    """
    Descarga precios horarios PVPC desde la API de ESIOS (Red Eléctrica).
    Necesita un token personal gratuito, pedido por email a consultasios@ree.es
    (ver instrucciones). Se lee del secret ESIOS_TOKEN.
    Devuelve un diccionario {("2026/05/01", "01:00"): precio_eur_kwh, ...}
    """
    esios_token = os.environ.get("ESIOS_TOKEN")
    if not esios_token:
        raise Exception("Falta el secret ESIOS_TOKEN (token personal de la API de ESIOS)")

    url = "https://api.esios.ree.es/indicators/1001"  # Indicador PVPC término de energía
    headers = {
        "Accept": "application/json; application/vnd.esios-api-v1+json",
        "Content-Type": "application/json",
        "Authorization": f'Token token="{esios_token}"'
    }
    params = {
        "start_date": f"{fecha_inicio}T00:00:00",
        "end_date": f"{fecha_fin}T23:59:59"
    }
    response = requests.get(url, headers=headers, params=params)
    response.raise_for_status()
    datos = response.json()

    precios = {}
    for valor in datos.get("indicator", {}).get("values", []):
        dt = datetime.fromisoformat(valor["datetime"])
        clave = (dt.strftime("%Y/%m/%d"), dt.strftime("%H:00"))
        precios[clave] = valor["value"] / 1000  # ESIOS da €/MWh, lo pasamos a €/kWh
    return precios


# ----------------------------
# CSV - Guardar histórico sin duplicar
# ----------------------------

def cargar_fechas_existentes():
    """Devuelve el conjunto de (fecha, hora) que ya tenemos guardadas, para no duplicar."""
    existentes = set()
    if os.path.exists(RUTA_CSV):
        with open(RUTA_CSV, "r", newline="", encoding="utf-8") as f:
            lector = csv.DictReader(f)
            for fila in lector:
                existentes.add((fila["fecha"], fila["hora"]))
    return existentes


def guardar_registros(registros):
    """Añade filas nuevas al CSV, creando el archivo con cabecera si no existe."""
    existe_archivo = os.path.exists(RUTA_CSV)
    with open(RUTA_CSV, "a", newline="", encoding="utf-8") as f:
        campos = ["fecha", "hora", "consumo_kwh", "vertido_kwh", "precio_eur_kwh", "coste_eur", "ingreso_eur"]
        escritor = csv.DictWriter(f, fieldnames=campos)
        if not existe_archivo:
            escritor.writeheader()
        for r in registros:
            escritor.writerow(r)


# ----------------------------
# PROGRAMA PRINCIPAL
# ----------------------------

if __name__ == "__main__":
    print("Iniciando actualización de datos...")

    # Calculamos el rango: desde hace 3 meses hasta hoy, por si Datadis tenía
    # algún día pendiente de publicar. El csv ya filtra duplicados, así que
    # pedir de más no hace daño.
    hoy = datetime.now()
    hace_3_meses = hoy - timedelta(days=90)
    fecha_inicio = hace_3_meses.strftime("%Y/%m")
    fecha_fin = hoy.strftime("%Y/%m")

    print("Conectando a Datadis...")
    token = obtener_token()
    suministro = obtener_suministro_objetivo(token)
    print(f"Suministro encontrado: {suministro['cups']} (distributorCode={suministro['distributorCode']})")

    print(f"Descargando consumo de {fecha_inicio} a {fecha_fin}...")
    consumo_datos = obtener_consumo(token, suministro, fecha_inicio, fecha_fin)
    print(f"Registros de consumo recibidos: {len(consumo_datos)}")

    print("Descargando precios PVPC de ESIOS...")
    fecha_inicio_iso = hace_3_meses.strftime("%Y-%m-%d")
    fecha_fin_iso = hoy.strftime("%Y-%m-%d")
    try:
        precios = obtener_precios_pvpc(fecha_inicio_iso, fecha_fin_iso)
        print(f"Precios PVPC recibidos: {len(precios)} franjas horarias")
    except Exception as e:
        print(f"⚠️ No se pudieron descargar precios PVPC: {e}")
        precios = {}

    existentes = cargar_fechas_existentes()
    nuevos_registros = []

    for registro in consumo_datos:
        clave = (registro["date"], registro["time"])
        if clave in existentes:
            continue  # ya lo teníamos guardado

        consumo_kwh = registro.get("consumptionKWh") or 0
        vertido_kwh = registro.get("surplusEnergyKWh") or 0
        precio = precios.get(clave)

        coste_eur = round(consumo_kwh * precio, 4) if precio is not None else ""
        ingreso_eur = round(vertido_kwh * precio, 4) if precio is not None else ""

        nuevos_registros.append({
            "fecha": registro["date"],
            "hora": registro["time"],
            "consumo_kwh": consumo_kwh,
            "vertido_kwh": vertido_kwh,
            "precio_eur_kwh": precio if precio is not None else "",
            "coste_eur": coste_eur,
            "ingreso_eur": ingreso_eur
        })

    if nuevos_registros:
        guardar_registros(nuevos_registros)
        print(f"✅ Guardados {len(nuevos_registros)} registros nuevos en {RUTA_CSV}")
    else:
        print("No hay registros nuevos que guardar (todo estaba ya actualizado).")
