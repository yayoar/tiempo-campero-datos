"""
Duelo diario en la casa de Yayo (Azcuénaga 98, Tunuyán): Tiempo Campero contra The Weather Channel (la app del celular
del hijo), medidos contra la estación de Contingencias "Tunuyán" (a 2,5 km).

Lado Tiempo Campero (19/11/2026): lo que muestra la app 0.35b, con los umbrales y el modelo del Valle que la app baja
de datos/reglas.json, aplicados a los mismos datos que baja la app (7 modelos de Open-Meteo y el ensamble ECMWF),
tomados del colector para el punto Tunuyán (a 0,5 km de la casa). Copia de Combinador.kt, ModeloValle.kt y EnsambleEcmwf.kt:
  - Modelos disparatados (mínima a más de 8° o máxima a más de 10° de la mediana) afuera, solo para la lluvia.
  - "Llueve": fraccion_aviso de los modelos (80%) con lluvia_aviso_mm (1 mm) o más; baja a "Posible lluvia"
    si menos del 80% de las corridas del ensamble europeo da 1 mm.
  - "Posible lluvia": 50% de los modelos con 5 mm o 40% con 2 mm.
  - Horas de lluvia: la mitad de los modelos con probabilidad 50% o 0,5 mm en esa hora, si el día era "Llueve"
    antes del ensamble (igual que la app, que marca las horas antes de consultar el ensamble).
  - Mínima y máxima: ModeloValle (fórmula de la mínima y corrección de máxima por estación y trimestre).
  No verificado: la app usa los valores diarios de Open-Meteo y acá se arman con los horarios.
Lado The Weather Channel: su pronóstico hora a hora tal como lo muestra la página.

Para cada día D se usa el pronóstico guardado más cercano a las 8 de la mañana del día anterior.
Salida: datos/duelo/registro.csv (un renglón por día y fuente) y datos/duelo/ultimo.md (resumen para mandarle a Yayo).
"""
import csv, datetime as dt, glob, gzip, io, json, math, os
RAIZ = os.path.dirname(os.path.abspath(__file__)); DATOS = os.path.join(RAIZ, "datos")
PUNTO = "Tunuyán"; ESTACION = "4"
PESOS = {"gem_seamless": 3, "icon_seamless": 2, "ukmo_seamless": 2, "ecmwf_ifs025": 1, "meteofrance_seamless": 1,
         "gfs_seamless": .5, "jma_seamless": .5}
AR = dt.timezone(dt.timedelta(hours=-3))

def hora_dacc(s):
    """'2026-07-29 1:00:00' / '2026-11-10 24:00:00' -> comienzo de la hora medida (la marca cierra la hora)."""
    f, h = s.strip().split(" ")[:2]; hh = int(h.split(":")[0])
    return dt.datetime.strptime(f, "%Y-%m-%d") + dt.timedelta(hours=hh - 1)

def snapshots(fuente):
    """[(datetime UTC, ruta)] de los pronósticos guardados."""
    out = []
    for f in glob.glob(os.path.join(DATOS, "pronosticos", "*", "*", "*", f"*_{fuente}.json.gz")):
        p = f.split(os.sep); d = p[-4] + p[-3] + p[-2] + os.path.basename(f)[:4]
        out.append((dt.datetime.strptime(d, "%Y%m%d%H%M").replace(tzinfo=dt.timezone.utc), f))
    return sorted(out)

def elegir(fuente, dia):
    """Pronóstico guardado más cercano a las 8 h del día anterior (entre las 0 h y las 14 h de ese día)."""
    objetivo = dt.datetime.combine(dia - dt.timedelta(days=1), dt.time(8), AR)
    c = [(abs((t - objetivo).total_seconds()), f) for t, f in snapshots(fuente)
         if objetivo - dt.timedelta(hours=8) <= t <= objetivo + dt.timedelta(hours=6)]
    return min(c)[1] if c else None

def _reglas():
    """Umbrales y modelo del Valle tal como los baja la app (datos/reglas.json)."""
    r = json.load(open(os.path.join(DATOS, "reglas.json"), encoding="utf8"))
    return r.get("version", "?"), r["umbrales"], r["modelo_valle"]

def _mediana(v):
    s = sorted(v); n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2

def _pesos_valle(M, lat, lon):
    """Copia de ModeloValle.pesos: las 6 estaciones más cercanas, peso 1/distancia^2 (mínimo 1 km)."""
    o = sorted(((e["n"], math.hypot((lat - e["lat"]) * 111.0, (lon - e["lon"]) * 93.0)) for e in M["estaciones"]), key=lambda t: t[1])
    if not o or o[0][1] > M["max_km"]: return None
    c = {n: 1.0 / (max(d, 1.0) ** 2) for n, d in o[:M["vecinas"]]}; t = sum(c.values())
    return {n: v / t for n, v in c.items()} if t > 0 else None

def _valle_minima(M, pesos, dia, por):
    """Copia de ModeloValle.minima (sin SMN, como la app)."""
    de = {m: v for m, v in por.items() if m in M["modelos"]}
    if len(de) < 4: return None
    tn = {m: v["tmin"] for m, v in de.items()}; med = _mediana(list(tn.values()))
    hr = [float(v["hr"]) for v in de.values() if v["hr"] is not None and v["hr"] < 100]
    rf = [v["raf"] for v in de.values() if v["raf"] is not None and v["raf"] > 0]
    doy = dia.timetuple().tm_yday; sen = math.sin(2 * math.pi * doy / 365.25); cos = math.cos(2 * math.pi * doy / 365.25)
    x = {"med": med, "min": min(tn.values()), "amp": max(tn.values()) - min(tn.values()),
         "tmax_med": _mediana([v["tmax"] for v in de.values()]),
         "hr": _mediana(hr) if hr else M["hr_falta"], "raf": max(rf) if rf else M["raf_falta"],
         "pp": math.log(1.0 + max(_mediana([v["pp"] for v in de.values()]), 0.0)),
         "smn": 0.0, "smn_falta": 1.0, "sen": sen, "cos": cos}
    for m in M["modelos"]: x[f"d_{m}"] = (tn[m] - med) if m in tn else 0.0
    for e, p in pesos.items():
        x[f"e{e}"] = p; x[f"e{e}_sen"] = p * sen; x[f"e{e}_cos"] = p * cos; x[f"e{e}_med"] = p * (med - 5.0) / 5.0
    return M["b0"] + sum(M["coef"].get(k, 0.0) * v for k, v in x.items())

def _valle_maxima(M, pesos, dia, por):
    """Copia de ModeloValle.maxima: mediana de los modelos + corrección de la estación por trimestre."""
    de = [v for m, v in por.items() if m in M["modelos"]]
    if len(de) < 4: return None
    q = str((dia.month % 12) // 3); num = den = 0.0
    for e, p in pesos.items():
        v = M["cor_max"].get(str(e), {}).get(q)
        if v is None: continue
        num += p * v; den += p
    return _mediana([v["tmax"] for v in de]) + num / den if den > 0 else None

def app(dia):
    """Lo que muestra la app 0.35b para el punto Tunuyán (Combinador.kt, ModeloValle.kt y EnsambleEcmwf.kt).
    Diferencia conocida y no verificada: la app usa los valores DIARIOS de Open-Meteo; acá se arman con los horarios
    guardados por el colector (mínimo, máximo, suma). Tampoco se aplica la regla de estabilidad, que depende del
    pronóstico anterior guardado en el teléfono."""
    f = elegir("open_meteo", dia)
    if not f: return None
    version, U, M = _reglas()
    C = __import__("colector"); fila = [x for x in C.DEPTOS if x[0] == PUNTO][0]; lat, lon = fila[2], fila[3]
    d = json.load(gzip.open(f))["datos"]; i = [n for n, *_ in C.DEPTOS].index(PUNTO)
    por = {}; horas = {}
    for m in PESOS:
        r = d.get(m); r = [r] if isinstance(r, dict) else r
        if not isinstance(r, list) or len(r) <= i or "hourly" not in r[i]: continue
        h = r[i]["hourly"]; t = h["time"]; ix = [k for k, x in enumerate(t) if x.startswith(dia.isoformat())]
        if len(ix) < 20: continue
        g = lambda v: [h[v][k] for k in ix] if v in h else [None] * len(ix)
        tt = [x for x in g("temperature_2m") if x is not None]
        if len(tt) < 20: continue
        pp = [x or 0 for x in g("precipitation")]
        hrs = [x for x in g("relative_humidity_2m") if x is not None]
        rfs = [x for x in g("wind_gusts_10m") if x is not None]
        por[m] = dict(tmin=min(tt), tmax=max(tt), pp=sum(pp), cod=max((x or 0) for x in g("weather_code")),
                      hr=int(min(hrs)) if hrs else None, raf=max(rfs) if rfs else None)
        prob = g("precipitation_probability")
        horas[m] = [(prob[k] is not None and prob[k] >= 50) or pp[k] >= .5 for k in range(len(ix))]
    if len(por) < 4: return None
    # descartarAbsurdos: solo para las reglas de lluvia; ModeloValle recibe todos los modelos
    datos = dict(por)
    if len(datos) >= 3:
        mn = _mediana([v["tmin"] for v in datos.values()]); mx = _mediana([v["tmax"] for v in datos.values()])
        buenos = {m: v for m, v in datos.items() if abs(v["tmin"] - mn) <= 8 and abs(v["tmax"] - mx) <= 10}
        datos = buenos or datos
    n = len(datos); cuenta = lambda c: sum(1 for v in datos.values() if c(v))
    fr = lambda f, c: n >= 2 and cuenta(c) >= math.ceil(f * n)
    llueve = fr(U["fraccion_aviso"], lambda v: v["pp"] >= U["lluvia_aviso_mm"])
    nivel = 3 if llueve else (1 if fr(.5, lambda v: v["pp"] >= 5) or fr(.4, lambda v: v["pp"] >= 2) else 0)
    # Horas de lluvia: se calculan con el "llueve" de antes del ensamble, como en la app
    hm = [m for m in datos if m in horas]
    hs = [k for k in range(24) if llueve and sum(1 for m in hm if k < len(horas[m]) and horas[m][k]) * 2 >= len(hm)]
    # EnsambleEcmwf.confirmar: si menos del 80% de las corridas da 1 mm, el día baja a "Posible lluvia"
    ens = d.get("ensamble_ecmwf"); ens = [ens] if isinstance(ens, dict) else ens
    if llueve and isinstance(ens, list) and len(ens) > i and "hourly" in ens[i]:
        eh = ens[i]["hourly"]; ix = [k for k, x in enumerate(eh["time"]) if x.startswith(dia.isoformat())]
        mem = [k for k in eh if k.startswith("precipitation")]
        if len(mem) >= 20 and len(ix) >= 20:
            frac = sum(1 for k in mem if sum((eh[k][j] or 0) for j in ix) >= 1.0) / len(mem)
            if frac < .8: llueve, nivel = False, 1
    pesos = _pesos_valle(M, lat, lon)
    tmin = _valle_minima(M, pesos, dia, por) if pesos else None
    tmax = _valle_maxima(M, pesos, dia, por) if pesos else None
    pt = sum(PESOS[m] for m in datos)
    if tmin is None: tmin = min(v["tmin"] for v in datos.values())
    if tmax is None: tmax = sum(PESOS[m] * v["tmax"] for m, v in datos.items()) / pt
    return dict(fuente="Tiempo Campero", llueve=llueve, nivel=nivel,
                mm=round(sum(PESOS[m] * v["pp"] for m, v in datos.items()) / pt, 1), horas=hs,
                tmin=round(tmin, 1), tmax=round(tmax, 1), tormenta=cuenta(lambda v: v["cod"] >= 95),
                modelos=n, reglas=version, archivo=os.path.relpath(f, RAIZ))

def twc(dia):
    f = elegir("twc", dia)
    if not f: return None
    h = (json.load(gzip.open(f))["datos"].get(PUNTO) or {}).get("horario")
    if not h: return None
    ix = [k for k, x in enumerate(h["validTimeLocal"]) if x.startswith(dia.isoformat())]
    if len(ix) < 20: return None
    F = lambda x: (x - 32) * 5 / 9
    prob = [h["precipChance"][k] for k in ix]; qpf = [(h["qpf"][k] or 0) * 25.4 for k in ix]
    frases = [h["wxPhraseLong"][k] or "" for k in ix]
    hs = [int(h["validTimeLocal"][k][11:13]) for k, p, q in zip(ix, prob, qpf) if p >= 50 or q >= .5]
    return dict(fuente="The Weather Channel", llueve=sum(qpf) >= 1 and max(prob) >= 50, nivel=None, mm=round(sum(qpf), 1),
                horas=hs, tmin=round(F(min(h["temperature"][k] for k in ix)), 1), tmax=round(F(max(h["temperature"][k] for k in ix)), 1),
                tormenta=sum(1 for x in frases if "ormenta" in x), modelos=None, archivo=os.path.relpath(f, RAIZ))

def medido(dia):
    filas = []
    for f in glob.glob(os.path.join(DATOS, "estaciones", "dacc", "*.csv.gz")):
        for l in gzip.open(f, "rt"):
            c = l.rstrip("\n").split(",")
            if c[0] != ESTACION: continue
            t = hora_dacc(c[1])            # la marca cierra la hora: 01:00 = de 0 a 1
            if t.date() == dia: filas.append((t.hour, c))
    if len(filas) < 20: return None
    num = lambda c, i: float(c[i]) if c[i] not in ("", "None") else None
    # columnas guardadas: est, fecha, luego la fila original desde el índice 4 -> índice original k está en k-2
    tmax = [num(c, 8) for _, c in filas]; tmin = [num(c, 9) for _, c in filas]; pp = [(h, num(c, 27)) for h, c in filas]
    return dict(fuente="Medido (estación Tunuyán)", mm=round(sum(p for _, p in pp if p and 0 <= p < 80), 1),
                horas=sorted(h for h, p in pp if p and p >= .2), llueve=sum(p for _, p in pp if p and 0 <= p < 80) >= 1,
                tmin=min(x for x in tmin if x is not None and -30 < x < 50), tmax=max(x for x in tmax if x is not None and -30 < x < 50))

def rango(hs):
    if not hs: return "—"
    tramos, ini, prev = [], hs[0], hs[0]
    for h in hs[1:] + [None]:
        if h is None or h != prev + 1: tramos.append(f"{ini}-{prev + 1} h" if prev > ini else f"{ini} h"); ini = h
        prev = h if h is not None else prev
    return ", ".join(tramos)

def main():
    hoy = dt.datetime.now(AR).date(); out = os.path.join(DATOS, "duelo"); os.makedirs(out, exist_ok=True)
    reg = os.path.join(out, "registro.csv")
    # Se recalcula todo el registro en cada corrida: así, cuando cambian las reglas de la app, los días
    # anteriores se vuelven a medir con las reglas nuevas (pronósticos y mediciones quedan guardados).
    primero = dt.date(2026, 11, 11)
    if os.path.exists(reg):
        ds = [r["dia"] for r in csv.DictReader(open(reg, encoding="utf8"))]
        if ds: primero = min(primero, dt.date.fromisoformat(min(ds)))
    nuevos = []
    for k in range((hoy - primero).days):
        dia = primero + dt.timedelta(days=k)
        m = medido(dia); a = app(dia); w = twc(dia)
        if not m or not (a or w): continue
        for p in (a, w):
            if not p: continue
            ah = set(p["horas"]) & set(m["horas"])
            nuevos.append(dict(dia=dia.isoformat(), fuente=p["fuente"], dijo_llueve=p["llueve"], llovio=m["llueve"],
                               acierto_lluvia=p["llueve"] == m["llueve"], mm_pron=p["mm"], mm_medido=m["mm"],
                               horas_pron=rango(p["horas"]), horas_medidas=rango(m["horas"]), horas_acertadas=len(ah),
                               tmin_pron=p["tmin"], tmin_medida=m["tmin"], tmax_pron=p["tmax"], tmax_medida=m["tmax"],
                               err_tmin=round(p["tmin"] - m["tmin"], 1), err_tmax=round(p["tmax"] - m["tmax"], 1),
                               reglas=p.get("reglas", "")))
    if nuevos:
        with open(reg, "w", encoding="utf8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(nuevos[0])); w.writeheader(); w.writerows(nuevos)
    # resumen
    filas = list(csv.DictReader(open(reg, encoding="utf8"))) if os.path.exists(reg) else []
    lin = [f"# Duelo en tu casa — actualizado {dt.datetime.now(AR):%d/%m %H:%M}", ""]
    for fu in ("Tiempo Campero", "The Weather Channel"):
        r = [x for x in filas if x["fuente"] == fu]
        if not r: continue
        ac = sum(x["acierto_lluvia"] == "True" for x in r)
        et = sum(abs(float(x["err_tmin"])) for x in r) / len(r); eM = sum(abs(float(x["err_tmax"])) for x in r) / len(r)
        lin.append(f"- **{fu}**: {len(r)} días medidos; acertó si llovía o no {ac} de {len(r)}; error medio mínima {et:.1f}°, máxima {eM:.1f}°.")
    lin += ["", "| Día | Fuente | ¿Llueve? | mm | Horas de lluvia | Mín | Máx |", "|---|---|---|---|---|---|---|"]
    for x in filas[-14:]:
        lin.append(f"| {x['dia'][8:]}/{x['dia'][5:7]} | {x['fuente']} | {'sí' if x['dijo_llueve']=='True' else 'no'} (llovió: {'sí' if x['llovio']=='True' else 'no'}) "
                   f"| {x['mm_pron']} (medido {x['mm_medido']}) | {x['horas_pron']} (medidas {x['horas_medidas']}) | {x['tmin_pron']} ({x['tmin_medida']}) | {x['tmax_pron']} ({x['tmax_medida']}) |")
    lin += ["", "## Lo que viene", "", "| Día | Fuente | ¿Llueve? | mm | Horas | Mín | Máx | Tormenta |", "|---|---|---|---|---|---|---|---|"]
    for k in range(0, 6):
        dia = hoy + dt.timedelta(days=k)
        for fn in (lambda d: app_futuro(d), lambda d: twc_futuro(d)):
            p = fn(dia)
            if p: lin.append(f"| {dia:%d/%m} | {p['fuente']} | {'Llueve' if p['llueve'] else ('Posible lluvia' if p.get('nivel') == 1 else 'no')} | {p['mm']} | {rango(p['horas'])} | {p['tmin']} | {p['tmax']} | {p['tormenta'] or ''} |")
    open(os.path.join(out, "ultimo.md"), "w", encoding="utf8").write("\n".join(lin) + "\n")
    print("duelo: renglones", len(nuevos))

def _ultimo(fuente):
    s = snapshots(fuente); return s[-1][1] if s else None
def app_futuro(dia):
    global elegir
    viejo = elegir; elegir = lambda fu, d: _ultimo(fu)
    try: return app(dia)
    finally: elegir = viejo
def twc_futuro(dia):
    global elegir
    viejo = elegir; elegir = lambda fu, d: _ultimo(fu)
    try: return twc(dia)
    finally: elegir = viejo

if __name__ == "__main__":
    main()
