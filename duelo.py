"""
Duelo diario en la casa de Yayo (Azcuénaga 98, Tunuyán): Tiempo Campero contra The Weather Channel (la app del celular
del hijo), medidos contra la estación de Contingencias "Tunuyán" (a 2,5 km).

Lado Tiempo Campero: las MISMAS reglas que muestra la app 0.33 (Combinador.kt), aplicadas a los mismos datos que baja la
app (7 modelos de Open-Meteo y el ensamble ECMWF), tomados del colector para el punto Tunuyán (a 0,5 km de la casa).
  - "Llueve": 70% o más de los modelos con 5 mm o más; se baja a "Posible lluvia" si menos del 80% del ensamble da 1 mm.
  - "Posible lluvia": 50% de los modelos con 5 mm o 40% con 2 mm.
  - Horas de lluvia: la mitad de los modelos con probabilidad 50% o 0,5 mm en esa hora (solo si el día es "Llueve").
  - Helada: el modelo más frío con 0° o menos; máxima: promedio pesado de los modelos.
  (No incluye la corrección de máxima por sitio ni el puntaje de tormenta de zona.)
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

def app(dia):
    f = elegir("open_meteo", dia)
    if not f: return None
    d = json.load(gzip.open(f))["datos"]; i = [n for n, *_ in __import__("colector").DEPTOS].index(PUNTO)
    por = {}; horas = {}
    for m in PESOS:
        r = d.get(m); r = [r] if isinstance(r, dict) else r
        if not isinstance(r, list) or len(r) <= i or "hourly" not in r[i]: continue
        h = r[i]["hourly"]; t = h["time"]; ix = [k for k, x in enumerate(t) if x.startswith(dia.isoformat())]
        if len(ix) < 20: continue
        g = lambda v: [h[v][k] for k in ix] if v in h else [None] * len(ix)
        if sum(x is not None for x in g("temperature_2m")) < 20: continue
        pp = [x or 0 for x in g("precipitation")]
        por[m] = dict(tmin=min(x for x in g("temperature_2m") if x is not None), tmax=max(x for x in g("temperature_2m") if x is not None),
                      pp=sum(pp), cod=max((x or 0) for x in g("weather_code")),
                      raf=max((x or 0) for x in g("wind_gusts_10m")))
        prob = [x for x in g("precipitation_probability")]
        horas[m] = [((prob[k] or 0) >= 50 if prob[k] is not None else False) or pp[k] >= .5 for k in range(len(ix))]
    if len(por) < 4: return None
    n = len(por); ceil = lambda fr: math.ceil(fr * n)
    cuenta = lambda c: sum(1 for v in por.values() if c(v))
    llueve = cuenta(lambda v: v["pp"] >= 5) >= ceil(.7)
    nivel = 3 if llueve else (1 if cuenta(lambda v: v["pp"] >= 5) >= ceil(.5) or cuenta(lambda v: v["pp"] >= 2) >= ceil(.4) else 0)
    ens = d.get("ensamble_ecmwf"); ens = [ens] if isinstance(ens, dict) else ens
    if llueve and isinstance(ens, list) and len(ens) > i:
        eh = ens[i]["hourly"]; ix = [k for k, x in enumerate(eh["time"]) if x.startswith(dia.isoformat())]
        mem = [k for k in eh if k.startswith("precipitation")]
        if mem and ix:
            frac = sum(1 for k in mem if sum((eh[k][j] or 0) for j in ix) >= 1) / len(mem)
            if frac < .8: llueve, nivel = False, 1
    pt = sum(PESOS[m] for m in por)
    hs = [k for k in range(24) if llueve and sum(1 for m in horas if k < len(horas[m]) and horas[m][k]) * 2 >= len(horas)]
    return dict(fuente="Tiempo Campero", llueve=nivel == 3, nivel=nivel,
                mm=round(sum(PESOS[m] * v["pp"] for m, v in por.items()) / pt, 1), horas=hs,
                tmin=round(min(v["tmin"] for v in por.values()), 1),
                tmax=round(sum(PESOS[m] * v["tmax"] for m, v in por.items()) / pt, 1),
                tormenta=cuenta(lambda v: v["cod"] >= 95), modelos=n, archivo=os.path.relpath(f, RAIZ))

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
    reg = os.path.join(out, "registro.csv"); hechos = set()
    if os.path.exists(reg):
        hechos = {r["dia"] for r in csv.DictReader(open(reg, encoding="utf8"))}
    nuevos = []
    for k in range(1, 8):
        dia = hoy - dt.timedelta(days=k)
        if dia.isoformat() in hechos: continue
        m = medido(dia); a = app(dia); w = twc(dia)
        if not m or not (a or w): continue
        for p in (a, w):
            if not p: continue
            ah = set(p["horas"]) & set(m["horas"])
            nuevos.append(dict(dia=dia.isoformat(), fuente=p["fuente"], dijo_llueve=p["llueve"], llovio=m["llueve"],
                               acierto_lluvia=p["llueve"] == m["llueve"], mm_pron=p["mm"], mm_medido=m["mm"],
                               horas_pron=rango(p["horas"]), horas_medidas=rango(m["horas"]), horas_acertadas=len(ah),
                               tmin_pron=p["tmin"], tmin_medida=m["tmin"], tmax_pron=p["tmax"], tmax_medida=m["tmax"],
                               err_tmin=round(p["tmin"] - m["tmin"], 1), err_tmax=round(p["tmax"] - m["tmax"], 1)))
    if nuevos:
        nuevo = not os.path.exists(reg)
        with open(reg, "a", encoding="utf8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(nuevos[0])); nuevo and w.writeheader(); w.writerows(nuevos)
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
    print("duelo: días nuevos", len(nuevos) // 2)

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
