"""
Duelo de todas las fuentes, fenómeno por fenómeno (pedido de Yayo, 11/11/2026).

Compite Tiempo Campero (reglas de la app 0.33) contra The Weather Channel, el SMN (modelo WRF de 4 km), MET Norway (yr.no)
y cada modelo de Open-Meteo por separado. Para cada día D se toma lo que cada fuente pronosticaba a la mañana del día
anterior (D-1, el pronóstico guardado más cercano a las 8) y se compara con lo que pasó.

Árbitros (lo medido):
  * estaciones de Contingencias a 10 km o menos del punto de cada departamento: helada (mínima <= 0), calor (máxima >= 35),
    lluvia (1 mm o más en el día), lluvia intensa (10 mm en una hora o 25 mm en el día), zonda (2 horas o más con humedad
    <= 15%, ráfaga >= 36 km/h y viento del oeste), viento fuerte (ráfaga >= 54 km/h sin zonda), nieve (1 mm o más con
    máxima <= 3);
  * radar de Contingencias: tormenta (45 dBZ o más a menos de 10 km), granizo (55 dBZ o más a menos de 2 km).
Puntaje por fuente y fenómeno: aciertos (avisó y pasó), falsas alarmas, no avisados, y la EFECTIVIDAD
= aciertos / (aciertos + falsas alarmas + no avisados), que no premia callarse ni avisar siempre.
Salida: datos/duelo/total_registro.csv (día, depto, fuente, fenómeno, pronosticó, pasó) y datos/duelo/total.md.
"""
import csv, datetime as dt, glob, gzip, json, math, os
import colector as C
RAIZ = os.path.dirname(os.path.abspath(__file__)); DATOS = os.path.join(RAIZ, "datos")
AR = dt.timezone(dt.timedelta(hours=-3))

def hora_dacc(s):
    """'2026-07-29 1:00:00' / '2026-11-10 24:00:00' -> comienzo de la hora medida (la marca cierra la hora)."""
    f, h = s.strip().split(" ")[:2]; hh = int(h.split(":")[0])
    return dt.datetime.strptime(f, "%Y-%m-%d") + dt.timedelta(hours=hh - 1)
APP7 = ["gem_seamless", "icon_seamless", "ukmo_seamless", "ecmwf_ifs025", "meteofrance_seamless", "gfs_seamless", "jma_seamless"]
PESOS = dict(zip(APP7, [3, 2, 2, 1, 1, .5, .5]))
FEN = ["helada", "calor", "lluvia", "lluvia_intensa", "tormenta", "granizo", "zonda", "viento_fuerte", "nieve"]
DEPTOS = [(n, la, lo) for n, _, la, lo in C.DEPTOS]

def km(a, b, c, d): return math.hypot((a - c) * 111, (b - d) * 93)

# ------------------------------------------------------------------ pronósticos guardados
def snap(fuente, dia):
    obj = dt.datetime.combine(dia - dt.timedelta(days=1), dt.time(8), AR); mejor = None
    for f in glob.glob(os.path.join(DATOS, "pronosticos", "*", "*", "*", f"*_{fuente}.json.gz")):
        p = f.split(os.sep)
        t = dt.datetime.strptime(p[-4] + p[-3] + p[-2] + os.path.basename(f)[:4], "%Y%m%d%H%M").replace(tzinfo=dt.timezone.utc)
        if obj - dt.timedelta(hours=8) <= t <= obj + dt.timedelta(hours=6):
            d = abs((t - obj).total_seconds())
            if mejor is None or d < mejor[0]: mejor = (d, f)
    return json.load(gzip.open(mejor[1]))["datos"] if mejor else None

def dia_modelo(h, dia):
    """Resumen diario de una serie horaria de Open-Meteo para el día local dia."""
    ix = [k for k, x in enumerate(h["time"]) if x.startswith(dia.isoformat())]
    T = [h["temperature_2m"][k] for k in ix if h["temperature_2m"][k] is not None]
    if len(T) < 20: return None
    v = lambda n: [h[n][k] for k in ix if n in h and h[n][k] is not None]
    pp = v("precipitation"); cod = v("weather_code"); raf = v("wind_gusts_10m"); hr = v("relative_humidity_2m")
    return dict(tmin=min(T), tmax=max(T), pp=sum(pp), pph=max(pp or [0]), cod=cod, raf=max(raf or [0]), hr=min(hr or [100]))

def avisos(d):
    """Qué avisa una fuente (dict diario) según reglas comunes y simples."""
    if d is None: return {}
    cod = d.get("cod", [])
    return dict(helada=d["tmin"] <= 0, calor=d["tmax"] >= 35, lluvia=d["pp"] >= 1,
                lluvia_intensa=d["pp"] >= 25 or d.get("pph", 0) >= 10,
                tormenta=d.get("tormenta", any(c >= 95 for c in cod)), granizo=d.get("granizo", any(c in (96, 99) for c in cod)),
                zonda=d.get("hr", 100) <= 20 and d.get("raf", 0) >= 40, viento_fuerte=d.get("raf", 0) >= 60,
                nieve=d.get("nieve", any(c in (71, 73, 75, 77, 85, 86) for c in cod)))

def fuentes(dia):
    """{fuente: {depto: avisos}}"""
    out = {}
    om = snap("open_meteo", dia)
    if om:
        app = {}
        for m, r in om.items():
            if m == "ensamble_ecmwf" or not isinstance(r, list): continue
            for (n, *_), x in zip(DEPTOS, r):
                d = dia_modelo(x.get("hourly", {}), dia) if isinstance(x, dict) else None
                if d: out.setdefault(f"Open-Meteo {m.replace('_seamless','')}", {})[n] = avisos(d)
                if d and m in APP7: app.setdefault(n, {})[m] = d
        ens = om.get("ensamble_ecmwf") if isinstance(om.get("ensamble_ecmwf"), list) else None
        for k, (n, *_) in enumerate(DEPTOS):
            p = app.get(n, {})
            if len(p) < 4: continue
            c = math.ceil; N = len(p); pt = sum(PESOS[m] for m in p)
            llueve = sum(v["pp"] >= 5 for v in p.values()) >= c(.7 * N)
            if llueve and ens:
                eh = ens[k]["hourly"]; ix = [j for j, x in enumerate(eh["time"]) if x.startswith(dia.isoformat())]
                mem = [q for q in eh if q.startswith("precipitation")]
                if mem and sum(1 for q in mem if sum((eh[q][j] or 0) for j in ix) >= 1) / len(mem) < .8: llueve = False
            rafs = sorted((v["raf"] for v in p.values()), reverse=True)
            a = dict(helada=min(v["tmin"] for v in p.values()) <= 0,            # el modelo más frío (regla de la app)
                     calor=sum(PESOS[m] * v["tmax"] for m, v in p.items()) / pt >= 35,
                     lluvia=llueve,                                              # "Llueve" de la app
                     lluvia_intensa=sum(v["pp"] >= 10 for v in p.values()) >= c(.7 * N),
                     tormenta=sum(any(x >= 95 for x in v["cod"]) for v in p.values()) >= 2,
                     granizo=sum(any(x in (96, 99) for x in v["cod"]) for v in p.values()) >= 2,
                     zonda=min(v["hr"] for v in p.values()) <= 25 and rafs[1] >= 40,
                     viento_fuerte=rafs[0] >= 60,
                     nieve=sorted((sum(1 for x in v["cod"] if x in (71, 73, 75, 77, 85, 86)) > 0 for v in p.values()), reverse=True)[2])
            out.setdefault("Tiempo Campero", {})[n] = a
    tw = snap("twc", dia)
    if tw:
        for n, *_ in DEPTOS:
            h = (tw.get(n) or {}).get("horario")
            if not h: continue
            ix = [k for k, x in enumerate(h["validTimeLocal"]) if x.startswith(dia.isoformat())]
            if len(ix) < 20: continue
            F = lambda x: (x - 32) * 5 / 9; g = lambda c: [h[c][k] for k in ix]
            qpf = [(x or 0) * 25.4 for x in g("qpf")]; fr = " ".join(x or "" for x in g("wxPhraseLong")).lower()
            d = dict(tmin=F(min(g("temperature"))), tmax=F(max(g("temperature"))), pp=sum(qpf), pph=max(qpf),
                     raf=max((x or 0) for x in g("windGust")) * 1.609 if "windGust" in h else 0, hr=min(g("relativeHumidity")),
                     tormenta="tormenta" in fr, granizo="granizo" in fr, nieve="nieve" in fr or any((x or 0) > 0 for x in g("qpfSnow")))
            out.setdefault("The Weather Channel", {})[n] = avisos(d)
    mn = snap("met_norway", dia)
    if mn:
        for n, *_ in DEPTOS:
            ts = ((mn.get(n) or {}).get("properties") or {}).get("timeseries", [])
            hs = [x for x in ts if (dt.datetime.fromisoformat(x["time"].replace("Z", "+00:00")).astimezone(AR).date() == dia)]
            if len(hs) < 20: continue
            T = [x["data"]["instant"]["details"]["air_temperature"] for x in hs]
            pp = [((x["data"].get("next_1_hours") or {}).get("details") or {}).get("precipitation_amount", 0) for x in hs]
            sym = " ".join(((x["data"].get("next_1_hours") or {}).get("summary") or {}).get("symbol_code", "") for x in hs)
            d = dict(tmin=min(T), tmax=max(T), pp=sum(pp), pph=max(pp), hr=min(x["data"]["instant"]["details"].get("relative_humidity", 100) for x in hs),
                     raf=max(x["data"]["instant"]["details"].get("wind_speed_of_gust", 0) or 0 for x in hs) * 3.6,
                     tormenta="thunder" in sym, granizo=False, nieve="snow" in sym or "sleet" in sym)
            out.setdefault("MET Norway", {})[n] = avisos(d)
    smn = smn_dia(dia)
    if smn:
        for n, d in smn.items(): out.setdefault("SMN (WRF)", {})[n] = avisos(d)
    return out

# ------------------------------------------------------------------ SMN WRF (corrida 00Z del día anterior)
def smn_dia(dia):
    ruta = os.path.join(DATOS, "smn_wrf", f"{dia}.json")
    if os.path.exists(ruta): return json.load(open(ruta))
    try:
        import fsspec, h5py, numpy as np
    except ImportError: return None
    d0 = dia - dt.timedelta(days=1); fs = fsspec.filesystem("http")
    url = lambda h: f"https://smn-ar-wrf.s3.amazonaws.com/DATA/WRF/DET/{d0:%Y/%m/%d}/00/WRFDETAR_01H_{d0:%Y%m%d}_00_{h:03d}.nc"
    serie = {n: [] for n, *_ in DEPTOS}; ix = None
    for h in range(28, 52):
        try:
            with fs.open(url(h), block_size=2 ** 18) as fh:
                g = h5py.File(fh, "r")
                if ix is None:
                    lat = g["lat"][:]; lon = g["lon"][:]
                    ix = {n: np.unravel_index(np.argmin((lat - a) ** 2 + (lon - o) ** 2), lat.shape) for n, a, o in DEPTOS}
                A = {v: g[v][0] for v in ("T2", "PP", "HR2", "magViento10") if v in g}
            for n, (i, j) in ix.items(): serie[n].append({v: float(A[v][i, j]) for v in A})
        except FileNotFoundError: return None
        except Exception as e: print("smn", h, e)
    if min(len(s) for s in serie.values()) < 20: return None
    out = {n: dict(tmin=min(x["T2"] for x in s), tmax=max(x["T2"] for x in s), pp=sum(x.get("PP", 0) for x in s),
                   pph=max(x.get("PP", 0) for x in s), hr=min(x.get("HR2", 100) for x in s),
                   raf=max(x.get("magViento10", 0) for x in s) * 3.6 * 1.4,     # viento medio -> ráfaga aproximada
                   tormenta=max(x.get("PP", 0) for x in s) >= 5, granizo=False, nieve=any(x.get("PP", 0) > .2 and x["T2"] <= 1 for x in s))
           for n, s in serie.items()}
    os.makedirs(os.path.dirname(ruta), exist_ok=True); json.dump(out, open(ruta, "w"))
    return out

# ------------------------------------------------------------------ lo que pasó
def verdad(dia, cerca=None):
    """cerca: {lugar: [números de estación]}; si no se da, las estaciones a 10 km o menos de cada departamento."""
    if cerca is None:
        est = json.load(open(os.path.join(DATOS, "estaciones", "dacc", "estaciones.json"), encoding="utf8"))
        cerca = {n: [str(x["Estacion"]) for x in est if x.get("lat") and km(x["lat"], x["lng"], la, lo) <= 10] for n, la, lo in DEPTOS}
    filas = {}
    for f in glob.glob(os.path.join(DATOS, "estaciones", "dacc", f"{dia:%Y-%m}.csv.gz")) + \
             glob.glob(os.path.join(DATOS, "estaciones", "dacc", f"{(dia + dt.timedelta(days=1)):%Y-%m}.csv.gz")):
        for l in gzip.open(f, "rt"):
            c = l.rstrip("\n").split(",")
            t = hora_dacc(c[1])
            if t.date() == dia: filas.setdefault(c[0], {})[t.hour] = c
    def num(c, i):
        try: return float(c[i])
        except Exception: return None
    rad = radar_dia(dia)
    out = {}
    for n, *_ in DEPTOS:
        v = {}
        hs = [filas[e] for e in cerca[n] if e in filas and len(filas[e]) >= 20]
        if hs:
            def una(h):
                tx = [num(c, 8) for c in h.values()]; tn = [num(c, 9) for c in h.values()]
                pp = [num(c, 27) for c in h.values()]; pp = [p for p in pp if p is not None and 0 <= p < 80]
                hr = [num(c, 12) for c in h.values()]; vx = [num(c, 14) for c in h.values()]; vm = [num(c, 16) for c in h.values()]
                dr = [num(c, 19) for c in h.values()]
                tmax = max(x for x in tx if x is not None); tmin = min(x for x in tn if x is not None)
                ok = [(a, b, d) for a, b, cc, d in zip(hr, vx, vm, dr) if None not in (a, b, cc) and b <= 45 and b <= cc * 5 + 6]
                zon = sum(1 for a, b, d in ok if a <= 15 and b >= 10 and d is not None and 225 <= d <= 360) >= 2
                return dict(helada=tmin <= 0, calor=tmax >= 35, lluvia=sum(pp) >= 1,
                            lluvia_intensa=sum(pp) >= 25 or max(pp or [0]) >= 10, zonda=zon,
                            viento_fuerte=(not zon) and max((b for _, b, _ in ok), default=0) >= 15,
                            nieve=sum(pp) >= 1 and tmax <= 3)
            us = [una(h) for h in hs]
            v = {k: any(u[k] for u in us) if k in ("lluvia", "lluvia_intensa", "zonda", "viento_fuerte", "nieve") else
                    sum(u[k] for u in us) * 2 >= len(us) for k in us[0]}
        if n in rad:
            v["tormenta"] = rad[n][1] >= 45; v["granizo"] = rad[n][0] >= 55
        if v: out[n] = v
    return out

def radar_dia(dia):
    """Máximo de reflectividad por departamento (a 2 y 10 km) en el día local, sin ecos fijos (cerros, ciudad).
    Eco fijo = píxel con 30 dBZ o más en más de la mitad de las imágenes del día o de los días vecinos.
    Además se exige que el valor aparezca en 2 imágenes o más (descarta píxeles sueltos)."""
    import numpy as np
    # Mapa de ecos fijos: necesita al menos 14 días de radar archivado; hasta entonces no se usa el radar como árbitro
    # (con 3 días los cerros y la ciudad aparecían como "tormenta" casi todo el tiempo).
    todos = sorted(glob.glob(os.path.join(DATOS, "radar", "*", "*", "*.npz")))
    if len(todos) < 14: return {}
    mapa = os.path.join(DATOS, "radar", "ecos_fijos.npy")
    if not os.path.exists(mapa) or os.path.getmtime(mapa) < os.path.getmtime(todos[-1]) - 7 * 86400:
        acum = None; n = 0
        for f in todos[-30:]:
            z = np.load(f)
            for key in z.files[::5]:
                b = (z[key] >= 30).astype(np.float32); acum = b if acum is None else acum + b; n += 1
        np.save(mapa, acum / n > .1)       # eco de 30 dBZ en más del 10% de las imágenes de un mes = fijo
    fijo_mes = np.load(mapa)
    zs = []; fijos = []
    for k in (-1, 0, 1):
        d = dia + dt.timedelta(days=k)
        for dd in (d, d + dt.timedelta(days=1)):   # el día local abarca dos días UTC
            f = os.path.join(DATOS, "radar", f"{dd:%Y}", f"{dd:%m}", f"{dd:%Y%m%d}.npz")
            if not os.path.exists(f): continue
            z = np.load(f)
            for key in z.files:
                t = dt.datetime.strptime(dd.strftime("%Y%m%d") + key[1:], "%Y%m%d%H%M%S").replace(tzinfo=dt.timezone.utc).astimezone(AR)
                if t.date() == dia: zs.append(z[key])
                if abs((t.date() - dia).days) <= 1: fijos.append(z[key] >= 30)
    if len(zs) < 300: return {}               # menos de 10 h de radar
    fijo = (np.mean(fijos, axis=0) > .5) | fijo_mes
    Z = np.stack(zs); Z[:, fijo] = 0
    segundo = np.sort(Z, axis=0)[-2]           # el valor que aparece al menos en 2 imágenes
    out = {}
    for n, la, lo in DEPTOS:
        fi, co = C.pixel(la, lo, 700, 500); fi -= C.FILA0
        r = lambda q: int(segundo[max(fi - q, 0):fi + q + 1, max(co - q, 0):co + q + 1].max())
        out[n] = (r(2), r(10))
    return out

# ------------------------------------------------------------------ principal
def main():
    hoy = dt.datetime.now(AR).date(); carpeta = os.path.join(DATOS, "duelo"); os.makedirs(carpeta, exist_ok=True)
    reg = os.path.join(carpeta, "total_registro.csv")
    hechos = {r["dia"] for r in csv.DictReader(open(reg, encoding="utf8"))} if os.path.exists(reg) else set()
    nuevas = []
    for k in range(1, 6):
        dia = hoy - dt.timedelta(days=k)
        if dia.isoformat() in hechos: continue
        V = verdad(dia)
        if not V: continue
        for fu, por in fuentes(dia).items():
            for n, a in por.items():
                for f in FEN:
                    if n in V and f in V[n] and f in a:
                        nuevas.append([dia.isoformat(), n, fu, f, int(bool(a[f])), int(bool(V[n][f]))])
    if nuevas:
        nuevo = not os.path.exists(reg)
        with open(reg, "a", encoding="utf8", newline="") as fh:
            w = csv.writer(fh); nuevo and w.writerow(["dia", "depto", "fuente", "fenomeno", "aviso", "paso"]); w.writerows(nuevas)
    if not os.path.exists(reg): return
    R = list(csv.DictReader(open(reg, encoding="utf8")))
    dias = len({r["dia"] for r in R})
    lin = [f"# Duelo de todas las fuentes — {dias} días medidos (actualizado {dt.datetime.now(AR):%d/%m %H:%M})", "",
           "Efectividad = aciertos / (aciertos + falsas alarmas + no avisados). Entre paréntesis: aciertos / falsas alarmas / no avisados.", ""]
    for f in FEN:
        t = {}
        for r in R:
            if r["fenomeno"] != f: continue
            x = t.setdefault(r["fuente"], [0, 0, 0]); a, p = r["aviso"] == "1", r["paso"] == "1"
            if a and p: x[0] += 1
            elif a: x[1] += 1
            elif p: x[2] += 1
        casos = max((x[0] + x[2] for x in t.values()), default=0)
        lin.append(f"## {f.replace('_', ' ').capitalize()} — {casos} casos medidos")
        if not casos: lin += ["Todavía no pasó en los días medidos.", ""]; continue
        orden = sorted(t.items(), key=lambda kv: -(kv[1][0] / max(sum(kv[1]), 1)))
        for fu, (a, fa, na) in orden:
            lin.append(f"- {fu}: {round(100 * a / max(a + fa + na, 1))}% ({a} / {fa} / {na})")
        lin.append("")
    open(os.path.join(carpeta, "total.md"), "w", encoding="utf8").write("\n".join(lin) + "\n")
    print("duelo total: renglones nuevos", len(nuevas))

if __name__ == "__main__":
    main()
