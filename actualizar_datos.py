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
CUPS_OBJETIVO = os.environ["DATADIS_CUPS"]  # El suministro de Torre Pacheco (con la instalación solar)

# Tarifa de precio FIJO (no PVPC). Si en el futuro cambias a una tarifa con
# precio variable por hora, aquí es donde habría que ajustar el cálculo.
PRECIO_COMPRA_EUR_KWH = 0.115  # lo que pagas por cada kWh consumido de la red
PRECIO_VENTA_EUR_KWH = 0.06   # lo que te pagan por cada kWh vertido a la red

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

    if response.status_code == 429:
        print(f"⚠️ Límite de Datadis alcanzado para el rango {fecha_inicio}-{fecha_fin}: "
              f"'{response.text.strip()}'. Datadis solo permite una consulta idéntica cada 24h. "
              f"Se reintentará en la próxima ejecución diaria.")
        return []

    response.raise_for_status()
    return response.json()


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

    # Pedimos solo el mes en curso y el anterior (suficiente para rellenar el día de hoy
    # y cualquier dato que Datadis publicase tarde del mes pasado). Pedir rangos más
    # amplios no hace falta día a día, y además puede chocar con el límite de Datadis
    # de "una consulta idéntica cada 24h" por cada combinación de fechas.
    hoy = datetime.now()
    mes_anterior = (hoy.replace(day=1) - timedelta(days=1))
    fecha_inicio = mes_anterior.strftime("%Y/%m")
    fecha_fin = hoy.strftime("%Y/%m")

    print("Conectando a Datadis...")
    token = obtener_token()
    suministro = obtener_suministro_objetivo(token)
    print(f"Suministro encontrado: {suministro['cups']} (distributorCode={suministro['distributorCode']})")

    print(f"Descargando consumo de {fecha_inicio} a {fecha_fin}...")
    consumo_datos = obtener_consumo(token, suministro, fecha_inicio, fecha_fin)
    print(f"Registros de consumo recibidos: {len(consumo_datos)}")

    existentes = cargar_fechas_existentes()
    nuevos_registros = []

    for registro in consumo_datos:
        clave = (registro["date"], registro["time"])
        if clave in existentes:
            continue  # ya lo teníamos guardado

        consumo_kwh = registro.get("consumptionKWh") or 0
        vertido_kwh = registro.get("surplusEnergyKWh") or 0

        coste_eur = round(consumo_kwh * PRECIO_COMPRA_EUR_KWH, 4)
        ingreso_eur = round(vertido_kwh * PRECIO_VENTA_EUR_KWH, 4)

        nuevos_registros.append({
            "fecha": registro["date"],
            "hora": registro["time"],
            "consumo_kwh": consumo_kwh,
            "vertido_kwh": vertido_kwh,
            "precio_eur_kwh": "",  # ya no aplica con tarifa fija; se deja vacío por compatibilidad
            "coste_eur": coste_eur,
            "ingreso_eur": ingreso_eur
        })

    if nuevos_registros:
        guardar_registros(nuevos_registros)
        print(f"✅ Guardados {len(nuevos_registros)} registros nuevos en {RUTA_CSV}")
    else:
        print("No hay registros nuevos que guardar (todo estaba ya actualizado).")
