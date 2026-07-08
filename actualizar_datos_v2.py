"""
ACTUALIZAR DATOS - Dashboard Energía
----------------------------------------
Se ejecuta automáticamente cada día vía GitHub Actions a las 6:00h España.
- Descarga consumo + vertido de Datadis (mes actual + anterior)
- Calcula coste con tarifa discriminada (punta/llano/valle)
- Añade solo registros nuevos al historico.csv (sin duplicar)
- Guarda fecha/hora de última conexión y último dato disponible
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

def get_precio(fecha_str, hora_str):
    try:
        dt = datetime.strptime(f"{fecha_str} {hora_str}", "%Y/%m/%d %H:%M")
        if dt.weekday() >= 5:
            return PRECIO["valle"]
        h = dt.hour
        if h in range(9, 14) or h in range(19, 22):
            return PRECIO["punta"]
        elif h in [8, 14, 15, 16, 17, 18, 22]:
            return PRECIO["llano"]
        else:
            return PRECIO["valle"]
    except:
        return PRECIO["llano"]

BASE_URL = "https://datadis.es"
RUTA_CSV = "data/historico.csv"
RUTA_STATUS = "data/status.json"


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


def obtener_ultimo_dato():
    """Lee el CSV y devuelve la fecha y hora del registro más reciente."""
    if not os.path.exists(RUTA_CSV):
        return None, None
    ultima_fecha = ""
    ultima_hora = ""
    with open(RUTA_CSV, "r", newline="", encoding="utf-8") as f:
        for fila in csv.DictReader(f):
            f_fecha = fila.get("fecha", "")
            f_hora = fila.get("hora", "")
            if f_fecha > ultima_fecha or (f_fecha == ultima_fecha and f_hora > ultima_hora):
                ultima_fecha = f_fecha
                ultima_hora = f_hora
    return ultima_fecha, ultima_hora


def guardar_status(conexion_ok, nuevos, con_vertido):
    """Guarda el estado de la última ejecución en data/status.json"""
    import json
    ahora = datetime.utcnow()
    # Convertir a hora España (UTC+2 en verano, UTC+1 en invierno)
    # Aproximación simple: mayo-octubre = +2, resto = +1
    mes = ahora.month
    offset = 2 if 4 <= mes <= 10 else 1
    ahora_spain = ahora + timedelta(hours=offset)

    ultima_fecha, ultima_hora = obtener_ultimo_dato()

    status = {
        "ultima_conexion_fecha": ahora_spain.strftime("%Y/%m/%d"),
        "ultima_conexion_hora": ahora_spain.strftime("%H:%M"),
        "conexion_ok": conexion_ok,
        "registros_nuevos": nuevos,
        "registros_con_vertido": con_vertido,
        "ultimo_dato_fecha": ultima_fecha,
        "ultimo_dato_hora": ultima_hora
    }

    os.makedirs("data", exist_ok=True)
    with open(RUTA_STATUS, "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2)

    print(f"📋 Status guardado:")
    print(f"   Última conexión: {ahora_spain.strftime('%d/%m/%Y %H:%M')} (hora España)")
    print(f"   Último dato disponible: {ultima_fecha} {ultima_hora}")


if __name__ == "__main__":
    print("Iniciando actualización de datos...")

    hoy = datetime.now()
    mes_anterior = (hoy.replace(day=1) - timedelta(days=1))
    fecha_inicio = mes_anterior.strftime("%Y/%m")
    fecha_fin = hoy.strftime("%Y/%m")

    conexion_ok = False
    nuevos_count = 0
    vertido_count = 0

    try:
        print("Conectando a Datadis...")
        token = obtener_token()
        suministro = obtener_suministro(token)
        print(f"Suministro: *** (distributorCode={suministro['distributorCode']})")

        print(f"Descargando {fecha_inicio} → {fecha_fin}...")
        datos = obtener_consumo(token, suministro, fecha_inicio, fecha_fin)
        print(f"Registros recibidos: {len(datos)}")

        conexion_ok = True
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
            nuevos_count = len(nuevos)
            vertido_count = len([r for r in nuevos if float(r["vertido_kwh"]) > 0])
            print(f"✅ {nuevos_count} registros nuevos guardados")
            print(f"   De los cuales {vertido_count} tienen vertido > 0")
        else:
            print("Sin registros nuevos (todo actualizado)")

    except Exception as e:
        print(f"❌ Error: {e}")
        conexion_ok = False

    finally:
        guardar_status(conexion_ok, nuevos_count, vertido_count)
