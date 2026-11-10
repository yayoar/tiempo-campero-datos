"""
Colector de Tiempo Campero (10/11/2026).

Guarda, para los 18 departamentos de Mendoza, lo que dicen TODAS las fuentes que un usuario puede tener a mano,
para después compararlas con lo que pasó de verdad:

  * Radar de Contingencias (DACC): cada imagen de 2 minutos, convertida a dBZ (1 km), más la capa de granizo.
    Contingencias solo guarda 3 días en su página, así que si no lo archivamos se pierde.
  * The Weather Channel (la app del clima de Samsung): pronóstico hora a hora (48 h) y de 15 minutos.
  * Open-Meteo: todos los modelos globales que ofrece (los 7 de la app y los demás).
  * Open-Meteo ensamble ECMWF (lo que usa la regla de lluvia de la app).
  * MET Norway (yr.no), usado por muchas apps.
  (El SMN WRF no hace falta guardarlo: el SMN lo deja publicado en AWS.)

Los pronósticos se guardan cada ~6 horas; el radar en cada corrida (cada hora).
"""
import datetime as dt, gzip, io, json, os, re, sys, time, urllib.request
import numpy as np
from PIL import Image

RAIZ = os.path.dirname(os.path.abspath(__file__))
DATOS = os.path.join(RAIZ, "datos")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36",
      "Accept-Language": "es-AR,es;q=0.9"}

DEPTOS = [  # departamento, región, lat, lon
    ("Capital", "Gran Mendoza", -32.890, -68.845), ("Godoy Cruz", "Gran Mendoza", -32.925, -68.845),
    ("Guaymallén", "Gran Mendoza", -32.900, -68.780), ("Las Heras", "Gran Mendoza", -32.850, -68.810),
    ("Luján de Cuyo", "Gran Mendoza", -33.040, -68.880), ("Maipú", "Gran Mendoza", -32.980, -68.790),
    ("Lavalle", "Gran Mendoza", -32.720, -68.590), ("San Martín", "Este", -33.080, -68.470),
    ("Junín", "Este", -33.145, -68.490), ("Rivadavia", "Este", -33.190, -68.460),
    ("Santa Rosa", "Este", -33.255, -68.150), ("La Paz", "Este", -33.460, -67.550),
    ("Tupungato", "Valle de Uco", -33.370, -69.150), ("Tunuyán", "Valle de Uco", -33.570, -69.020),
    ("San Carlos", "Valle de Uco", -33.770, -69.040), ("San Rafael", "Sur", -34.615, -68.330),
    ("General Alvear", "Sur", -34.975, -67.700), ("Malargüe", "Malargüe", -35.475, -69.585),
]

def get(url, binario=False, intentos=3, cab=None):
    for i in range(intentos):
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, headers=cab or UA), timeout=60)
            b = r.read()
            return b if binario else b.decode("utf8", "replace")
        except Exception as e:
            if i == intentos - 1: raise
            time.sleep(3 * (i + 1))

def guardar_gz(ruta, obj):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with gzip.open(ruta, "wt", encoding="utf8") as f: json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))

# ---------------------------------------------------------------- radar de Contingencias
BASE_RADAR = "https://www2.contingencias.mendoza.gov.ar/radar/web"
# Georreferencia tomada del visor oficial (radar/web): origen -34,-68; 450 km al norte, 250 al sur, este y oeste.
LAT_N = -34.00 + 450 / 110.924 + 0.046; LAT_S = -34.00 - 250 / 110.924 + 0.046
LON_O = -68.00 - 250 / 92.425;          LON_E = -68.00 + 250 / 92.425
# Escala de colores del radar (leyenda de las imágenes de Contingencias) -> dBZ (límite inferior de cada clase).
ESCALA = [((72, 61, 139), 5), ((0, 90, 0), 10), ((0, 112, 0), 20), ((8, 127, 219), 30), ((28, 71, 232), 35),
          ((110, 13, 198), 36), ((200, 15, 134), 39), ((192, 100, 135), 42), ((210, 136, 59), 45),
          ((250, 196, 49), 48), ((252, 249, 11), 51), ((254, 154, 88), 54), ((254, 95, 5), 57),
          ((253, 52, 28), 60), ((190, 190, 190), 65), ((211, 211, 211), 70)]
FILA0 = 200
_COL = np.array([c for c, _ in ESCALA], float); _DBZ = np.array([d for _, d in ESCALA], np.uint8)

def a_dbz(png_bytes):
    im = np.asarray(Image.open(io.BytesIO(png_bytes)).convert("RGBA"))
    a = im[..., 3] > 0
    out = np.zeros(im.shape[:2], np.uint8)
    if a.any():
        rgb = im[..., :3][a].astype(float)
        d = ((rgb[:, None, :] - _COL[None]) ** 2).sum(-1)
        out[a] = _DBZ[d.argmin(1)]
    return out

def pixel(lat, lon, alto, ancho):
    f = (LAT_N - lat) / (LAT_N - LAT_S) * alto; c = (lon - LON_O) / (LON_E - LON_O) * ancho
    return int(f), int(c)

def radar():
    lista = json.loads(get(f"{BASE_RADAR}/file_list.json?v={int(time.time())}"))
    nuevos = 0
    for dia in sorted(lista):
        carpeta = os.path.join(DATOS, "radar", dia[:4], dia[4:6])
        npz = os.path.join(carpeta, f"{dia}.npz")
        previo = dict(np.load(npz)) if os.path.exists(npz) else {}
        horas = sorted(f[:6] for f in lista[dia] if f.endswith("_dbz.png") or re.fullmatch(r"\d{6}\.png", f))
        faltan = [h for h in dict.fromkeys(horas) if f"d{h}" not in previo]
        if not faltan: continue
        filas = []
        def bajar(h):
            nombre = f"{h}_dbz.png" if f"{h}_dbz.png" in lista[dia] else f"{h}.png"
            try: z = a_dbz(get(f"{BASE_RADAR}/{dia}/{nombre}", binario=True))
            except Exception as e: print("radar", dia, h, e); return h, None, None
            g = None
            if f"{h}_granizo.png" in lista[dia]:
                try:
                    g = np.asarray(Image.open(io.BytesIO(get(f"{BASE_RADAR}/{dia}/{h}_granizo.png", binario=True))).convert("RGBA"))
                    if not (g[..., 3] > 0).any(): g = None
                except Exception as e: print("granizo", dia, h, e)
            return h, z, g
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(8) as ex: res = list(ex.map(bajar, faltan))
        for h, z, g in res:
            if z is None: continue
            previo[f"d{h}"] = z[FILA0:]; nuevos += 1   # se guarda desde la fila 200 (lat -31,7°) hacia el sur: Mendoza
            if g is not None:
                os.makedirs(os.path.join(carpeta, "granizo"), exist_ok=True)
                Image.fromarray(g).save(os.path.join(carpeta, "granizo", f"{dia}_{h}.png"), optimize=True)
            hora = f"{dia[:4]}-{dia[4:6]}-{dia[6:]}T{h[:2]}:{h[2:4]}:{h[4:]}Z"
            for n, reg, la, lo in DEPTOS:
                fi, co = pixel(la, lo, *z.shape)
                v = [int(z[max(fi - r, 0):fi + r + 1, max(co - r, 0):co + r + 1].max()) for r in (2, 10, 25)]
                filas.append(f"{hora},{n},{v[0]},{v[1]},{v[2]}")
        os.makedirs(carpeta, exist_ok=True)
        np.savez_compressed(npz, **previo)
        csv = os.path.join(carpeta, f"deptos_{dia}.csv")
        nuevo = not os.path.exists(csv)
        with open(csv, "a", encoding="utf8") as f:
            if nuevo: f.write("hora_utc,depto,dbz_2km,dbz_10km,dbz_25km\n")
            f.write("\n".join(filas) + ("\n" if filas else ""))
    print("radar: imágenes nuevas", nuevos)

# ---------------------------------------------------------------- The Weather Channel
def _bloque(s, clave):
    i = s.find(clave)
    if i < 0: return None
    j = s.find('\\"data\\":{', i)
    if j < 0: return None
    j += len('\\"data\\":'); d = 0
    for k in range(j, len(s)):
        if s[k] == "{": d += 1
        elif s[k] == "}":
            d -= 1
            if d == 0: break
    t = s[j:k + 1].replace('\\\\\\"', "\u0001").replace('\\"', '"').replace("\u0001", '\\"')
    return json.loads(t)

def twc():
    ruta_ids = os.path.join(DATOS, "fuentes", "twc_lugares.json")
    ids = json.load(open(ruta_ids, encoding="utf8")) if os.path.exists(ruta_ids) else {}
    salida = {}
    for n, reg, la, lo in DEPTOS:
        try:
            if n not in ids:
                s = get(f"https://weather.com/weather/hourbyhour/l/{la:.2f},{lo:.2f}")
                m = re.search(r'placeId\\*":\\*"([0-9a-f]{64})', s)
                if not m: print("twc sin lugar", n); continue
                ids[n] = m.group(1)
            s = get(f"https://weather.com/es-US/tiempo/horario/l/{ids[n]}")
            h = _bloque(s, "getSunV3HourlyForecastWithHeadersUrlConfig")
            q = _bloque(s, "getSunV3FifteenMinuteForecastWithHeadersUrlConfig")
            for b in (h, q):
                if b: b.pop("responseHeaders", None)
            salida[n] = {"horario": h, "15min": q}
        except Exception as e:
            print("twc", n, e)
        time.sleep(1)
    os.makedirs(os.path.dirname(ruta_ids), exist_ok=True)
    json.dump(ids, open(ruta_ids, "w", encoding="utf8"), ensure_ascii=False, indent=0)
    return salida  # unidades inglesas (°F, mph, pulgadas): se convierten al analizar

# ---------------------------------------------------------------- Open-Meteo y MET Norway
MODELOS = ["gem_seamless", "icon_seamless", "ukmo_seamless", "ecmwf_ifs025", "meteofrance_seamless", "gfs_seamless",
           "jma_seamless", "ecmwf_aifs025_single", "gfs_graphcast025", "cma_grapes_global",
           "ukmo_global_deterministic_10km", "knmi_seamless", "dmi_seamless", "metno_seamless", "kma_seamless", "bom_access_global"]

def open_meteo():
    la = ",".join(f"{d[2]:.3f}" for d in DEPTOS); lo = ",".join(f"{d[3]:.3f}" for d in DEPTOS)
    var = "temperature_2m,relative_humidity_2m,precipitation,precipitation_probability,weather_code,wind_speed_10m,wind_gusts_10m,wind_direction_10m,cape,snowfall"
    out = {}
    for m in MODELOS:
        try:
            out[m] = json.loads(get(f"https://api.open-meteo.com/v1/forecast?latitude={la}&longitude={lo}&hourly={var}"
                                    f"&models={m}&wind_speed_unit=kmh&timezone=America%2FArgentina%2FMendoza&forecast_days=3"))
        except Exception as e: print("open-meteo", m, e)
        time.sleep(2)
    try:
        out["ensamble_ecmwf"] = json.loads(get(f"https://ensemble-api.open-meteo.com/v1/ensemble?latitude={la}&longitude={lo}"
                                               "&hourly=precipitation&models=ecmwf_ifs025&forecast_days=3&timezone=America%2FArgentina%2FMendoza"))
    except Exception as e: print("ensamble", e)
    return out

def met_norway():
    out = {}
    cab = {"User-Agent": "TiempoCampero/0.32 github.com/yayoar/tiempo-campero-datos"}
    for n, reg, la, lo in DEPTOS:
        try: out[n] = json.loads(get(f"https://api.met.no/weatherapi/locationforecast/2.0/complete?lat={la:.3f}&lon={lo:.3f}", cab=cab))
        except Exception as e: print("met.no", n, e)
        time.sleep(1)
    return out

def pronosticos():
    ahora = dt.datetime.now(dt.timezone.utc)
    marca = os.path.join(DATOS, "pronosticos", "ultimo.txt")
    if os.path.exists(marca):
        ult = dt.datetime.fromisoformat(open(marca).read().strip())
        if (ahora - ult).total_seconds() < 5.5 * 3600 and "--forzar" not in sys.argv:
            print("pronósticos: todavía no toca"); return
    base = os.path.join(DATOS, "pronosticos", ahora.strftime("%Y/%m/%d"), ahora.strftime("%H%M"))
    for nombre, f in (("twc", twc), ("open_meteo", open_meteo), ("met_norway", met_norway)):
        try:
            guardar_gz(f"{base}_{nombre}.json.gz", {"guardado_utc": ahora.isoformat(), "datos": f()})
            print("pronósticos:", nombre, "ok")
        except Exception as e: print("pronósticos:", nombre, e)
    os.makedirs(os.path.dirname(marca), exist_ok=True)
    open(marca, "w").write(ahora.isoformat())

if __name__ == "__main__":
    for paso in (radar, pronosticos):
        try: paso()
        except Exception as e: print(paso.__name__, "falló:", e)
