"""Reportes de productores (Tiempo Campero 0.28).

Lee las respuestas del formulario "Tiempo Campero - reportes" (hoja publicada como CSV), se queda con las
válidas de las últimas 24 horas y escribe datos/reportes.json para la app. Guarda además todo en
datos/historial_reportes/AAAA-MM.jsonl: cada granizo informado es un dato medido para verificar los avisos.
Solo se publica tipo, zona (redondeada a 0,02°) y hora. El detalle libre NO se publica.
"""
import csv, io, json, os, urllib.request
from datetime import datetime, timedelta, timezone

CSV_URL = os.environ.get("REPORTES_CSV", "")
TIPOS = {"granizo_ahora", "granizo_cayo", "zonda"}
AR = timezone(timedelta(hours=-3))


def hora(txt):
    for f in ("%d/%m/%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(txt.strip(), f).replace(tzinfo=AR)
        except ValueError:
            pass
    return None


def main():
    os.makedirs("datos/historial_reportes", exist_ok=True)
    if not CSV_URL:
        print("Falta REPORTES_CSV"); return
    texto = urllib.request.urlopen(CSV_URL, timeout=30).read().decode("utf-8")
    filas = list(csv.reader(io.StringIO(texto)))[1:]
    ahora = datetime.now(AR)
    validos = []
    for f in filas:
        if len(f) < 4:
            continue
        h, tipo = hora(f[0]), f[1].strip()
        try:
            lat, lon = round(round(float(f[2]) / 0.02) * 0.02, 2), round(round(float(f[3]) / 0.02) * 0.02, 2)
        except ValueError:
            continue
        if h is None or tipo not in TIPOS or not (-37.6 <= lat <= -32.0 and -70.6 <= lon <= -66.4):
            continue
        if h > ahora + timedelta(minutes=5):
            continue
        validos.append({"tipo": tipo, "lat": lat, "lon": lon, "hora": h.isoformat(timespec="minutes")})
    # mismo tipo, misma zona y mismos 10 minutos = un solo reporte (evita dobles toques)
    vistos, unicos = set(), []
    for r in validos:
        k = (r["tipo"], r["lat"], r["lon"], r["hora"][:15])
        if k not in vistos:
            vistos.add(k); unicos.append(r)
    recientes = [r for r in unicos if datetime.fromisoformat(r["hora"]) >= ahora - timedelta(hours=24)]
    with open("datos/reportes.json", "w") as fo:
        json.dump({"reportes": recientes}, fo, ensure_ascii=False)
    # historial por mes (se reescribe completo desde la hoja: no se duplica)
    por_mes = {}
    for r in unicos:
        por_mes.setdefault(r["hora"][:7], []).append(r)
    for mes, lista in por_mes.items():
        with open(f"datos/historial_reportes/{mes}.jsonl", "w") as fo:
            for r in lista:
                fo.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(len(filas), "filas,", len(unicos), "válidos,", len(recientes), "en 24 h")


if __name__ == "__main__":
    main()
