"""
ACTUALIZAR DATOS - Dashboard Energía
----------------------------------------
Se ejecuta automáticamente cada día vía GitHub Actions a las 6:00h España.
- Descarga consumo + vertido de Datadis (mes actual + anterior)
- Calcula coste con tarifa discriminada (punta/llano/valle)
- Añade solo registros nuevos al historico.csv (sin duplicar)
"""

import os
import csv
import requests
from datetime import datetime, timedelta

NIF = os.environ["DATADIS_NIF"]
PASSWORD = os.environ["DATADIS_PASSWORD"]
CUPS_OBJETIVO = os.environ["DATADIS_CUPS"]

# Tarifas discriminadas 2.0TD
PRECIO = {
    "punta": 0.195,
    "llano": 0.116,
    "valle": 0.079
}
PRECIO_VENTA = 0.06

# Horas punta y llano en días laborables (lunes-viernes)
# Punta: 9-14h y 19-22h
# Llano: 8-9h, 14-19h, 22-23h
# Valle: resto (23-8h) y todo sábado/domingo
def get_precio(fecha_str, hora_str):
    try:
        dt = datetime.strptime(f"{fecha_str} {hora_str}", "%Y/%m/%d %H:%M")
        if dt.weekday() >= 5:  # sábado o domingo → siempre valle
            return PRECIO["valle"]
        h = dt.hour
        if h in range(9, 14) or h in range(19, 22):
            return PRECIO["punta"]
        elif h in [8, 14, 15, 16, 17, 18, 22]:
            return PRECIO["llano"]
        else:
            return PRECIO["valle"]
    except:
        return PRECIO["llano"]  # fallback

BASE_URL = "https://datadis.es"
RUTA_CSV = "data/historico.csv"


def obtener_token():
    r = requests.post(
        f"{BASE_URL}/nikola-auth/tokens/login",
        data={"username": NIF, "password": PASSWORD},
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    r.raise_for_status()
    return r.text


def obtener_suministro(token):
    r = requests.get(
        f"{BASE_URL}/api-private/api/get-supplies",
        headers={"Authorization": f"Bearer {token}"}
    )
    r.raise_for_status()
    for s in r.json():
        if s["cups"] == CUPS_OBJETIVO:
            return s
    raise Exception(f"CUPS {CUPS_OBJETIVO} no encontrado")


def obtener_consumo(token, suministro, fecha_inicio, fecha_fin):
    r = requests.get(
        f"{BASE_URL}/api-private/api/get-consumption-data",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "cups": suministro["cups"],
            "distributorCode": suministro["distributorCode"],
            "startDate": fecha_inicio,
            "endDate": fecha_fin,
            "measurementType": "0",
            "pointType": suministro["pointType"]
        }
    )
    if r.status_code == 429:
        print(f"⚠️ Límite Datadis para {fecha_inicio}-{fecha_fin}: {r.text.strip()}")
        return []
    r.raise_for_status()
    return r.json()


def cargar_existentes():
    existentes = set()
    if os.path.exists(RUTA_CSV):
        with open(RUTA_CSV, "r", newline="", encoding="utf-8") as f:
            for fila in csv.DictReader(f):
                existentes.add((fila["fecha"], fila["hora"]))
    return existentes


def guardar(registros):
    existe = os.path.exists(RUTA_CSV)
    with open(RUTA_CSV, "a", newline="", encoding="utf-8") as f:
        campos = ["fecha", "hora", "consumo_kwh", "vertido_kwh", "precio_eur_kwh", "coste_eur", "ingreso_eur"]
        w = csv.DictWriter(f, fieldnames=campos)
        if not existe:
            w.writeheader()
        for r in registros:
            w.writerow(r)


if __name__ == "__main__":
    print("Iniciando actualización de datos...")

    hoy = datetime.now()
    mes_anterior = (hoy.replace(day=1) - timedelta(days=1))
    fecha_inicio = mes_anterior.strftime("%Y/%m")
    fecha_fin = hoy.strftime("%Y/%m")

    print("Conectando a Datadis...")
    token = obtener_token()
    suministro = obtener_suministro(token)
    print(f"Suministro: *** (distributorCode={suministro['distributorCode']})")

    print(f"Descargando {fecha_inicio} → {fecha_fin}...")
    datos = obtener_consumo(token, suministro, fecha_inicio, fecha_fin)
    print(f"Registros recibidos: {len(datos)}")

    existentes = cargar_existentes()
    nuevos = []

    for r in datos:
        clave = (r["date"], r["time"])
        if clave in existentes:
            continue

        consumo = r.get("consumptionKWh") or 0
        vertido = r.get("surplusEnergyKWh") or 0
        precio = get_precio(r["date"], r["time"])
        coste = round(consumo * precio, 4)
        ingreso = round(vertido * PRECIO_VENTA, 4)

        nuevos.append({
            "fecha": r["date"],
            "hora": r["time"],
            "consumo_kwh": consumo,
            "vertido_kwh": vertido,
            "precio_eur_kwh": precio,
            "coste_eur": coste,
            "ingreso_eur": ingreso
        })

    if nuevos:
        guardar(nuevos)
        print(f"✅ {len(nuevos)} registros nuevos guardados")
        # Mostrar resumen de vertido
        con_vertido = [r for r in nuevos if float(r["vertido_kwh"]) > 0]
        print(f"   De los cuales {len(con_vertido)} tienen vertido > 0")
    else:
        print("Sin registros nuevos (todo actualizado)")
