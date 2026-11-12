"""Pronósticos viejos de Open-Meteo (corrida del día anterior, previous_day1) en cada estación de Contingencias,
para medir cuánto acertaba la app. Valle de Uco primero. Retoma donde quedó; frena si se acaba la cuota."""
import gzip, io, json, os, sys, time, urllib.request
RAIZ = os.path.dirname(os.path.abspath(__file__)); OUT = os.path.join(RAIZ, "datos", "historico", "open_meteo")
MOD = ["gem_seamless","icon_seamless","ukmo_seamless","ecmwf_ifs025","meteofrance_seamless","gfs_seamless","jma_seamless"]
VAR = ["temperature_2m","relative_humidity_2m","precipitation","wind_gusts_10m","weather_code"]
UCO = {"Tunuyán","Tunuyan","Tupungato","San Carlos"}
def get(u, t=240):
    return urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "TiempoCampero/0.33"}), timeout=t).read().decode()
def main():
    os.makedirs(OUT, exist_ok=True)
    try: lista = json.loads(get("https://agrometeo.mendoza.gov.ar/api/getEstaciones.php"))
    except Exception as e:   # la API de Contingencias a veces no responde: uso la lista que guarda el colector
        print("estaciones: uso la lista guardada", e)
        lista = json.load(open(os.path.join(RAIZ, "datos", "estaciones", "dacc", "estaciones.json"), encoding="utf8"))
    est = [x for x in lista if x.get("Estacion") and x.get("lat")]
    est.sort(key=lambda x: (0 if (x.get("Departamento") or "") in UCO else 1, x["Estacion"]))
    inicio = time.time()
    for x in est:
        for m in MOD:
            f = os.path.join(OUT, f"{x['Estacion']}_{m}.csv.gz")
            if os.path.exists(f): continue
            if time.time() - inicio > 5 * 3600: print("tiempo cumplido"); return
            ini = max(str(x.get("Primero") or "2024-04-10")[:10], "2024-04-10")
            filas = []; cab = None
            for a, b in [(ini, "2024-12-31"), ("2025-01-01", "2025-06-30"), ("2025-07-01", "2025-12-31"), ("2026-01-01", "2026-11-09")]:
                if b < ini: continue
                a = max(a, ini)
                u = ("https://previous-runs-api.open-meteo.com/v1/forecast?latitude=%.4f&longitude=%.4f&hourly=%s&models=%s"
                     "&start_date=%s&end_date=%s&timezone=America%%2FArgentina%%2FMendoza&wind_speed_unit=kmh") % (
                     x["lat"], x["lng"], ",".join(v + "_previous_day1" for v in VAR), m, a, b)
                for k in range(5):
                    try:
                        h = json.loads(get(u))["hourly"]; break
                    except urllib.error.HTTPError as e:
                        txt = e.read().decode()[:200]
                        if "Daily" in txt or "limit" in txt.lower(): print("CUOTA AGOTADA", x["Estacion"], m, txt); return
                        print("error", e.code, txt); time.sleep(60)
                    except Exception as e:
                        print("reintento", x["Estacion"], m, e); time.sleep(60)
                else:
                    filas = None; break
                cols = list(h.keys())
                if cab is None: cab = cols
                filas += [",".join("" if h[c][i] is None else str(h[c][i]) for c in cab) for i in range(len(h["time"]))]
                time.sleep(2)
            if not filas: continue
            with gzip.open(f, "wt") as g: g.write(",".join(cab) + "\n" + "\n".join(filas) + "\n")
            print(x["Estacion"], x.get("Departamento"), m, "ok", flush=True)
    print("TERMINADO")
if __name__ == "__main__": main()
