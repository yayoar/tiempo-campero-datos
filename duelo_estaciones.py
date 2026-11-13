"""
Duelo por estación (pedido de Yayo, 13/11/2026): en cada una de las 15 estaciones de Contingencias del Valle de Uco
compiten TODOS los pronósticos, medidos hora por hora y día por día contra la propia estación.

Competidores (pronóstico pedido en el lugar exacto de cada estación, guardado por colector.py):
  Tiempo Campero (con los ajustes medidos: corrección por estación, lluvia de 5 modelos, zonda con el SMN), los 7 modelos de Open-Meteo que usa la app, el SMN (modelo WRF de 4 km),
  The Weather Channel y MET Norway (yr.no).
Para cada día D se usa lo que cada fuente pronosticaba a la mañana del día anterior (el guardado más cercano a las 8 de D-1),
igual que en el duelo de la casa y en el duelo por departamento.

Cada hora (corre con el colector, que baja las 15 estaciones cada hora):
  * Hora por hora: temperatura pronosticada contra la medida (error en grados) y lluvia en esa hora
    (pronóstico: 0,5 mm o más, o probabilidad de 50% o más; medido: 0,2 mm o más).
  * Día por día (cuando el día está completo): helada, calor, lluvia, lluvia intensa, zonda, viento fuerte, nieve,
    tormenta y granizo (radar), con las mismas reglas que duelo_total.py.
Salidas: datos/duelo/estaciones/horario_AAAA-MM.csv.gz, datos/duelo/estaciones/diario.csv y datos/duelo/estaciones.md.
"""
import csv, datetime as dt, glob, gzip, json, math, os
import colector as C
import duelo_total as T

RAIZ = os.path.dirname(os.path.abspath(__file__)); DATOS = os.path.join(RAIZ, "datos")
CARPETA = os.path.join(DATOS, "duelo", "estaciones")
AR = dt.timezone(dt.timedelta(hours=-3))
EST = [(n, la, lo, str(e)) for n, _, la, lo, e in C.ESTACIONES]
NOMBRES = [n for n, *_ in EST]
APP7, PESOS = T.APP7, T.PESOS
CORTO = {"gem_seamless": "Canadá", "icon_seamless": "Alemania", "ukmo_seamless": "Reino Unido", "ecmwf_ifs025": "Centro Europeo",
         "meteofrance_seamless": "Francia", "gfs_seamless": "Estados Unidos", "jma_seamless": "Japón"}

# ------------------------------------------------------------------ reglas de Tiempo Campero con los ajustes del 12-13/11
DEPTO = {e: d for d, es in (("Tupungato", "3 40 60 61"), ("Tunuyán", "4 5 36 42 44 57 62"), ("San Carlos", "11 21 31 52")) for e in es.split()}
_COR = None
def correccion(est, dia):
    """Grados a sumar a la mediana de los modelos (mínima, máxima) según estación y época del año."""
    global _COR
    if _COR is None:
        r = os.path.join(DATOS, "fuentes", "correcciones_estaciones.json")
        _COR = json.load(open(r, encoding="utf8")) if os.path.exists(r) else {}
    q = str((dia.month % 12) // 3); out = []
    for v in ("tmin", "tmax"):
        x = ((_COR.get("estaciones", {}).get(est) or {}).get(q) or {}).get(v)
        if x is None: x = ((_COR.get("deptos", {}).get(DEPTO.get(est)) or {}).get(q) or {}).get(v, 0)
        out.append(x)
    return out

def mediana(v):
    v = sorted(v); n = len(v)
    return (v[n // 2] + v[(n - 1) // 2]) / 2

def reglas_tc(p, est, dia, smn=None):
    """Avisos del día de Tiempo Campero con las reglas medidas en la presentación (13/11/2026):
    helada = mínima corregida por estación <= 0,5; calor = máxima corregida >= 35; lluvia = 5 de los 7 modelos con 1 mm;
    lluvia intensa = mediana de los modelos >= 20 mm; zonda = (mediana de humedad mínima <= 15% y ráfaga máxima >= 40 km/h)
    o aviso de zonda del SMN. Tormenta, granizo, viento fuerte y nieve: las reglas de la app."""
    cmin, cmax = correccion(est, dia); N = len(p); c = math.ceil
    tn = mediana([v["tmin"] for v in p.values()]) + cmin; tx = mediana([v["tmax"] for v in p.values()]) + cmax
    rafs = sorted((v["raf"] for v in p.values()), reverse=True)
    zon = mediana([v["hr"] for v in p.values()]) <= 15 and rafs[0] >= 40
    if smn: zon = zon or (smn.get("hr", 100) <= 15 and smn.get("raf", 0) >= 40)
    return dict(helada=tn <= .5, calor=tx >= 35, lluvia=sum(v["pp"] >= 1 for v in p.values()) >= 5,
                lluvia_intensa=mediana([v["pp"] for v in p.values()]) >= 20,
                tormenta=sum(any(x >= 95 for x in v["cod"]) for v in p.values()) >= 2,
                granizo=sum(any(x in (96, 99) for x in v["cod"]) for v in p.values()) >= 2,
                zonda=zon, viento_fuerte=rafs[0] >= 60,
                nieve=sorted((sum(1 for x in v["cod"] if x in (71, 73, 75, 77, 85, 86)) > 0 for v in p.values()), reverse=True)[2],
                _tmin=tn, _tmax=tx)

# ------------------------------------------------------------------ pronóstico hora por hora de cada fuente
def _om(h, dia):
    """{hora local: (temp, mm, prob)} de una serie de Open-Meteo."""
    out = {}
    for k, t in enumerate(h.get("time", [])):
        if t.startswith(dia.isoformat()) and h["temperature_2m"][k] is not None:
            g = lambda v: (h.get(v) or [None] * len(h["time"]))[k]
            out[int(t[11:13])] = (h["temperature_2m"][k], g("precipitation") or 0, g("precipitation_probability"))
    return out

def horarios(dia):
    """{fuente: {estación: {hora: (temp, llueve)}}} con el pronóstico de la mañana de D-1."""
    out = {}
    om = T.snap("open_meteo", dia)
    if om:
        for m in APP7:
            r = om.get(m)
            if not isinstance(r, list) or len(r) < len(C.PUNTOS): continue
            for (n, *_), x in zip(C.PUNTOS, r):
                if n not in NOMBRES or not isinstance(x, dict): continue
                s = _om(x.get("hourly", {}), dia)
                if len(s) >= 20:
                    out.setdefault(CORTO[m], {})[n] = {hh: (t, mm >= .5 or (p is not None and p >= 50)) for hh, (t, mm, p) in s.items()}
        # Tiempo Campero: temperatura = promedio pesado de los modelos (como la máxima de la app);
        # lluvia en la hora = la mitad de los modelos o más con 0,5 mm o probabilidad de 50% (regla de horas de la app).
        # La temperatura de cada hora se corrige por estación: en la hora más fría se suma la corrección de la mínima,
        # en la más cálida la de la máxima, y en el medio en proporción.
        num = {n: e for n, _, _, e in EST}
        for n in NOMBRES:
            ms = {m: out[CORTO[m]][n] for m in APP7 if n in out.get(CORTO[m], {})}
            if len(ms) < 4: continue
            pt = sum(PESOS[m] for m in ms); s = {}
            for hh in range(24):
                v = [(m, x[hh]) for m, x in ms.items() if hh in x]
                if len(v) < 4: continue
                s[hh] = (sum(PESOS[m] * t for m, (t, _) in v) / sum(PESOS[m] for m, _ in v), sum(l for _, (_, l) in v) * 2 >= len(v))
            if len(s) >= 20:
                cmin, cmax = correccion(num[n], dia); lo_ = min(t for t, _ in s.values()); hi = max(t for t, _ in s.values())
                s = {hh: (t + cmin + (cmax - cmin) * ((t - lo_) / (hi - lo_) if hi > lo_ else .5), l) for hh, (t, l) in s.items()}
            out.setdefault("Tiempo Campero", {})[n] = s
    tw = T.snap("twc", dia)
    if tw:
        for n in NOMBRES:
            h = (tw.get(n) or {}).get("horario")
            if not h: continue
            s = {}
            for k, t in enumerate(h["validTimeLocal"]):
                if t.startswith(dia.isoformat()):
                    q = (h["qpf"][k] or 0) * 25.4; p = h["precipChance"][k] or 0
                    s[int(t[11:13])] = ((h["temperature"][k] - 32) * 5 / 9, q >= .5 or p >= 50)
            if len(s) >= 20: out.setdefault("The Weather Channel", {})[n] = s
    mn = T.snap("met_norway", dia)
    if mn:
        for n in NOMBRES:
            s = {}
            for x in ((mn.get(n) or {}).get("properties") or {}).get("timeseries", []):
                t = dt.datetime.fromisoformat(x["time"].replace("Z", "+00:00")).astimezone(AR)
                if t.date() != dia: continue
                pp = ((x["data"].get("next_1_hours") or {}).get("details") or {}).get("precipitation_amount", 0) or 0
                s[t.hour] = (x["data"]["instant"]["details"]["air_temperature"], pp >= .5)
            if len(s) >= 20: out.setdefault("MET Norway", {})[n] = s
    sm = smn_horario(dia)
    if sm: out["SMN (4 km)"] = {n: {int(k): tuple(v) for k, v in s.items()} for n, s in sm.items()}
    return out

def smn_horario(dia):
    """SMN WRF (corrida 00Z de D-1) en cada estación: {estación: {hora local: (temp, llueve)}} y el resumen diario."""
    ruta = os.path.join(DATOS, "smn_wrf", f"estaciones_{dia}.json")
    if os.path.exists(ruta): return json.load(open(ruta))["horario"]
    try:
        import fsspec, h5py, numpy as np
    except ImportError: return None
    d0 = dia - dt.timedelta(days=1); fs = fsspec.filesystem("http")
    url = lambda h: f"https://smn-ar-wrf.s3.amazonaws.com/DATA/WRF/DET/{d0:%Y/%m/%d}/00/WRFDETAR_01H_{d0:%Y%m%d}_00_{h:03d}.nc"
    hor = {n: {} for n in NOMBRES}; dia_ = {n: [] for n in NOMBRES}; ix = None
    for h in range(28, 52):                        # 00Z de D-1 + 28 h = 01 h local de D ... + 51 h = 24 h
        try:
            with fs.open(url(h), block_size=2 ** 18) as fh:
                g = h5py.File(fh, "r")
                if ix is None:
                    lat = g["lat"][:]; lon = g["lon"][:]
                    ix = {n: np.unravel_index(np.argmin((lat - a) ** 2 + (lon - o) ** 2), lat.shape) for n, a, o, _ in EST}
                A = {v: g[v][0] for v in ("T2", "PP", "HR2", "magViento10") if v in g}
        except FileNotFoundError: return None
        except Exception as e: print("smn estaciones", h, e); continue
        hl = h - 28                                 # hora local que empieza (la marca de Contingencias cierra la hora)
        for n, (i, j) in ix.items():
            x = {v: float(A[v][i, j]) for v in A}
            hor[n][hl] = (x["T2"], x.get("PP", 0) >= .5); dia_[n].append(x)
    if min(len(s) for s in dia_.values()) < 20: return None
    resumen = {n: dict(tmin=min(x["T2"] for x in s), tmax=max(x["T2"] for x in s), pp=sum(x.get("PP", 0) for x in s),
                       pph=max(x.get("PP", 0) for x in s), hr=min(x.get("HR2", 100) for x in s),
                       raf=max(x.get("magViento10", 0) for x in s) * 3.6 * 1.4,
                       tormenta=max(x.get("PP", 0) for x in s) >= 5, granizo=False,
                       nieve=any(x.get("PP", 0) > .2 and x["T2"] <= 1 for x in s)) for n, s in dia_.items()}
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    json.dump({"horario": hor, "diario": resumen}, open(ruta, "w"))
    return hor

# ------------------------------------------------------------------ lo medido, hora por hora
def medido(dias):
    """{(estación, día, hora): (temp media, mm)} de las 15 estaciones."""
    ids = {e: n for n, _, _, e in EST}; out = {}
    meses = {f"{d:%Y-%m}" for d in dias} | {f"{(d + dt.timedelta(days=1)):%Y-%m}" for d in dias}
    for mes in meses:
        f = os.path.join(DATOS, "estaciones", "dacc", f"{mes}.csv.gz")
        if not os.path.exists(f): continue
        for l in gzip.open(f, "rt"):
            c = l.rstrip("\n").split(",")
            if c[0] not in ids: continue
            t = T.hora_dacc(c[1])
            if t.date() not in dias: continue
            try: tm = float(c[10]); mm = float(c[27])
            except Exception: continue
            if not (-30 < tm < 50): continue
            out[(ids[c[0]], t.date(), t.hour)] = (tm, mm if 0 <= mm < 80 else None)
    return out

# ------------------------------------------------------------------ día por día (fenómenos)
def diario(dia):
    """Filas [día, estación, fuente, fenómeno, avisó, pasó] reusando las reglas de duelo_total."""
    viejo = T.DEPTOS
    try:
        T.DEPTOS = [(n, la, lo) for n, _, la, lo in C.PUNTOS]           # mismo orden que lo que pidió el colector
        F = {fu: {n: a for n, a in por.items() if n in NOMBRES} for fu, por in T.fuentes(dia).items() if fu != "SMN (WRF)"}
        T.DEPTOS = [(n, la, lo) for n, la, lo, _ in EST]
        V = T.verdad(dia, cerca={n: [e] for n, _, _, e in EST})
    finally:
        T.DEPTOS = viejo
    nombre = {f"Open-Meteo {m.replace('_seamless', '')}": CORTO[m] for m in APP7}
    F = {nombre.get(fu, fu): por for fu, por in F.items() if not fu.startswith("Open-Meteo") or fu in nombre}
    ruta = os.path.join(DATOS, "smn_wrf", f"estaciones_{dia}.json"); smn = {}
    if smn_horario(dia) is not None and os.path.exists(ruta):
        smn = json.load(open(ruta))["diario"]
        F["SMN (4 km)"] = {n: T.avisos(d) for n, d in smn.items()}
    # Tiempo Campero con los ajustes (reemplaza las reglas viejas de la app que trae duelo_total)
    F.pop("Tiempo Campero", None)
    om = T.snap("open_meteo", dia)
    if om:
        num = {n: e for n, _, _, e in EST}
        for k, (n, *_) in enumerate(C.PUNTOS):
            if n not in NOMBRES: continue
            p = {}
            for m in APP7:
                r = om.get(m)
                if isinstance(r, list) and len(r) > k and isinstance(r[k], dict):
                    d = T.dia_modelo(r[k].get("hourly", {}), dia)
                    if d: p[m] = d
            if len(p) >= 6: F.setdefault("Tiempo Campero", {})[n] = reglas_tc(p, num[n], dia, smn.get(n))
    filas = []
    for fu, por in F.items():
        for n, a in por.items():
            for f in T.FEN:
                if n in V and f in V[n] and f in a:
                    filas.append([dia.isoformat(), n, fu, f, int(bool(a[f])), int(bool(V[n][f]))])
    return filas

# ------------------------------------------------------------------ principal
def main():
    os.makedirs(CARPETA, exist_ok=True)
    ahora = dt.datetime.now(AR); hoy = ahora.date()
    # --- hora por hora: últimas 48 h
    hechas_r = os.path.join(CARPETA, "horas_hechas.json")
    hechas = set(json.load(open(hechas_r))) if os.path.exists(hechas_r) else set()
    dias = {hoy - dt.timedelta(days=k) for k in range(0, 3)}
    M = medido(dias); P = {d: horarios(d) for d in dias}; nuevas = []
    for (n, d, hh), (tm, mm) in sorted(M.items()):
        clave = f"{n}|{d}|{hh}"
        if clave in hechas: continue
        for fu, por in P[d].items():
            x = por.get(n, {}).get(hh)
            if x is None: continue
            nuevas.append([f"{d} {hh:02d}", n, fu, round(x[0], 1), tm, int(bool(x[1])), "" if mm is None else int(mm >= .2)])
        if any(n in por for por in P[d].values()): hechas.add(clave)
    por_mes = {}
    for f in nuevas: por_mes.setdefault(f[0][:7], []).append(f)
    for mes, fs in por_mes.items():
        r = os.path.join(CARPETA, f"horario_{mes}.csv.gz"); nuevo = not os.path.exists(r)
        with gzip.open(r, "at", encoding="utf8", newline="") as fh:
            w = csv.writer(fh); nuevo and w.writerow(["hora", "estacion", "fuente", "temp_pron", "temp_med", "lluvia_pron", "lluvia_med"])
            w.writerows(fs)
    corte = (ahora - dt.timedelta(days=4)).date().isoformat()
    json.dump(sorted(h for h in hechas if h.split("|")[1] >= corte), open(hechas_r, "w"))
    # --- día por día: días completos de la última semana
    reg = os.path.join(CARPETA, "diario.csv")
    hechos = {r["dia"] for r in csv.DictReader(open(reg, encoding="utf8"))} if os.path.exists(reg) else set()
    filas = []
    for k in range(1, 6):
        d = hoy - dt.timedelta(days=k)
        if d.isoformat() not in hechos: filas += diario(d)
    if filas:
        nuevo = not os.path.exists(reg)
        with open(reg, "a", encoding="utf8", newline="") as fh:
            w = csv.writer(fh); nuevo and w.writerow(["dia", "estacion", "fuente", "fenomeno", "aviso", "paso"]); w.writerows(filas)
    resumen()
    print("duelo por estación: horas nuevas", len(nuevas), "· renglones diarios nuevos", len(filas))

def resumen():
    """datos/duelo/estaciones.md: tabla general y una por estación."""
    H = []
    for f in sorted(glob.glob(os.path.join(CARPETA, "horario_*.csv.gz"))): H += list(csv.DictReader(gzip.open(f, "rt", encoding="utf8")))
    D = list(csv.DictReader(open(os.path.join(CARPETA, "diario.csv"), encoding="utf8"))) if os.path.exists(os.path.join(CARPETA, "diario.csv")) else []
    def tabla(hs, ds):
        t = {}
        for r in hs:
            x = t.setdefault(r["fuente"], dict(n=0, err=0., a=0, f=0, na=0))
            x["n"] += 1; x["err"] += abs(float(r["temp_pron"]) - float(r["temp_med"]))
            if r["lluvia_med"] != "":
                p, m = r["lluvia_pron"] == "1", r["lluvia_med"] == "1"
                x["a"] += p and m; x["f"] += p and not m; x["na"] += m and not p
        fen = {}
        for r in ds:
            x = fen.setdefault(r["fuente"], [0, 0, 0]); a, p = r["aviso"] == "1", r["paso"] == "1"
            if a and p: x[0] += 1
            elif a: x[1] += 1
            elif p: x[2] += 1
        filas = []
        for fu, x in t.items():
            e = x["err"] / x["n"]; ll = x["a"] + x["f"] + x["na"]
            ff = fen.get(fu, [0, 0, 0]); tf = sum(ff)
            filas.append((e, fu, x["n"], f"{e:.1f}°", f"{100 * x['a'] / ll:.0f}% ({x['a']}/{x['f']}/{x['na']})" if ll else "—",
                          f"{100 * ff[0] / tf:.0f}% ({ff[0]}/{ff[1]}/{ff[2]})" if tf else "—"))
        filas.sort()
        out = ["| Puesto | Pronóstico | Horas medidas | Error medio de temperatura | Lluvia hora a hora | Fenómenos del día |", "|---|---|---|---|---|---|"]
        out += [f"| {i} | {'**' + fu + '**' if fu == 'Tiempo Campero' else fu} | {n} | {e} | {ll} | {fe} |" for i, (_, fu, n, e, ll, fe) in enumerate(filas, 1)]
        return out
    lin = [f"# Duelo por estación — Valle de Uco (actualizado {dt.datetime.now(AR):%d/%m %H:%M})", "",
           "Pronóstico de la mañana del día anterior contra lo medido en cada estación de Contingencias, hora por hora.",
           "Puesto: por error medio de temperatura (menos es mejor). Lluvia y fenómenos: efectividad = aciertos / (aciertos + falsas alarmas + no avisados);",
           "entre paréntesis, aciertos / falsas alarmas / no avisados.", "", "## Las 15 estaciones juntas", ""]
    lin += tabla(H, D) + [""]
    for n in NOMBRES:
        hs = [r for r in H if r["estacion"] == n]
        if not hs: continue
        lin += [f"## {n.replace('Est. ', '')}", ""] + tabla(hs, [r for r in D if r["estacion"] == n]) + [""]
    open(os.path.join(DATOS, "duelo", "estaciones.md"), "w", encoding="utf8").write("\n".join(lin) + "\n")

if __name__ == "__main__":
    main()
