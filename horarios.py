"""Horarios de los avisos contra el dato cada 5 minutos (24/11/2026, decidido con Yayo).

Para cada día desde que hay dato cada 5 minutos (23/11/2026) y cada estación del Valle de Uco compara:
  1) La franja horaria que mostraría la app en el punto de la estación (helada, riesgo de helada, lluvia, viento fuerte,
     calor y calor extremo), replicando Combinador.franjas/horasPeores (APK 0.35b): para cada hora se toma el peor
     valor entre los modelos; lluvia si al menos la mitad de los modelos marca probabilidad >= 50% o >= 0,5 mm;
     helada si el modelo más frío marca <= 0°; riesgo si <= riesgo_helada; viento si la segunda ráfaga >= rafaga_fuerte;
     calor si el modelo más caliente marca >= 30°, calor extremo si >= calor_extremo.
  2) Lo que midió la estación cada ~5 minutos (datos/estaciones/dacc/inst): el minuto en que empezó y terminó cada
     fenómeno. Helada y calor con la temperatura del momento; viento con la velocidad media (el dato cada 5 minutos no
     trae ráfaga, así que el viento medido queda por debajo de la ráfaga); lluvia cuando sube el acumulado
     "precipitacion" (qué período acumula ese campo no está verificado: se usa solo para saber cuándo llueve).
  3) Hora por hora, el error de temperatura: la temperatura de cada hora como la muestra la app (promedio de los modelos
     estirado a la mínima y la máxima del día, Combinador.horasDelDia) contra el promedio medido en esa hora.
El pronóstico es el guardado más cercano a las 8 h del día anterior, igual que duelo.py.
Escribe datos/duelo/horarios.md y datos/duelo/horarios_estado.json. No cambia nada de la app.
"""
import datetime as dt, gzip, json, os, statistics as st
import duelo as D, colector as C

AQUI = os.path.dirname(os.path.abspath(__file__)); DATOS = os.path.join(AQUI, "datos")
AR = dt.timezone(dt.timedelta(hours=-3))
DESDE = dt.date(2026, 11, 23)
EVENTOS = ("helada", "riesgo_helada", "lluvia", "viento", "calor", "calor_extremo")


def medido(dia):
    """{estación: [(datetime, temp, humedad, velocidad km/h, lluvia acumulada)]} del día (hora local)."""
    out = {}
    for mes in {f"{dia:%Y-%m}"}:
        ruta = os.path.join(DATOS, "estaciones", "dacc", "inst", mes + ".csv.gz")
        if not os.path.exists(ruta): continue
        for l in gzip.open(ruta, "rt"):
            c = l.rstrip("\n").split(",")
            if len(c) < 7 or not c[1].startswith(dia.isoformat()): continue
            f = lambda x: float(x) if x not in ("", "None") and float(x) < 900 else None   # 999.9 = sin dato
            try: t = dt.datetime.fromisoformat(c[1])
            except ValueError: continue
            v = f(c[5]); v = None if v is None or v > 45 else v   # más de 45 m/s de viento medio no es creíble
            out.setdefault(c[0], []).append((t, f(c[2]), f(c[3]), None if v is None else v * 3.6, f(c[6])))
    # Anemómetro roto: si la mediana del viento medio del día pasa de 20 m/s (72 km/h), el viento de esa estación no se usa
    # (Cruz Negra: anemómetro roto desde el 12/11/2026, marca 999,9 o 63-66 m/s).
    for e, x in out.items():
        vs = [y[3] for y in x if y[3] is not None]
        if vs and st.median(vs) > 72: out[e] = [(a, b_, c_, None, d_) for a, b_, c_, _, d_ in x]
    return {e: sorted(x) for e, x in out.items()}


def franjas(dia, U, M):
    """{estación: {evento: [horas]}, '_temp': {estación: {hora: temp promedio}}} con el pronóstico guardado."""
    f = D.elegir("open_meteo", dia)
    if not f: return {}, {}
    dat = json.load(gzip.open(f))["datos"]; nombres = [p[0] for p in C.PUNTOS]
    out, temp = {}, {}
    for n, _, la, lo, e in C.ESTACIONES:
        if n not in nombres: continue
        i = nombres.index(n); por_hora = {}; por_dia = {}
        for m in D.PESOS:
            r = dat.get(m); r = [r] if isinstance(r, dict) else r
            if not isinstance(r, list) or len(r) <= i or "hourly" not in r[i]: continue
            h = r[i]["hourly"]; tt, hr_, pp_, rf_ = [], [], 0.0, []
            for q, x in enumerate(h["time"]):
                if not x.startswith(dia.isoformat()): continue
                g = lambda k: h[k][q] if k in h else None
                por_hora.setdefault(int(x[11:13]), []).append(dict(t=g("temperature_2m"), pp=g("precipitation"), w=D.PESOS[m],
                                                                   prob=g("precipitation_probability"), raf=g("wind_gusts_10m")))
                if g("temperature_2m") is not None: tt.append(g("temperature_2m"))
                if g("relative_humidity_2m") is not None: hr_.append(g("relative_humidity_2m"))
                if g("wind_gusts_10m") is not None: rf_.append(g("wind_gusts_10m"))
                pp_ += g("precipitation") or 0
            if len(tt) >= 20:
                por_dia[m] = dict(tmin=min(tt), tmax=max(tt), pp=pp_, hr=int(min(hr_)) if hr_ else None, raf=max(rf_) if rf_ else None)
        if not por_hora: continue
        # Como la app (Combinador.horasDelDia): promedio de los modelos por hora, estirado para que coincida con la
        # mínima y la máxima del día que muestra la app (fórmula del Valle en el punto de la estación).
        try:
            pe = D._pesos_valle(M, la, lo); tmin_app = D._valle_minima(M, pe, dia, por_dia); tmax_app = D._valle_maxima(M, pe, dia, por_dia)
        except Exception:
            tmin_app = tmax_app = None
        prom = {hh: sum(x["w"] * x["t"] for x in L if x["t"] is not None) / sum(x["w"] for x in L if x["t"] is not None)
                for hh, L in por_hora.items() if any(x["t"] is not None for x in L)}
        if prom and tmin_app is not None and tmax_app is not None:
            mn, mx = min(prom.values()), max(prom.values())
            prom = {hh: v + (tmin_app - mn) * (1 - ((v - mn) / (mx - mn) if mx > mn else 0.5))
                        + (tmax_app - mx) * ((v - mn) / (mx - mn) if mx > mn else 0.5) for hh, v in prom.items()}
        ev = {k: [] for k in EVENTOS}; tp = dict(prom)
        for hora, L in sorted(por_hora.items()):
            ts = [x["t"] for x in L if x["t"] is not None]; rf = sorted([x["raf"] for x in L if x["raf"] is not None], reverse=True)
            if ts:
                if min(ts) <= 0: ev["helada"].append(hora)
                if min(ts) <= U["riesgo_helada"]: ev["riesgo_helada"].append(hora)
                if max(ts) >= 30: ev["calor"].append(hora)
                if max(ts) >= U["calor_extremo"]: ev["calor_extremo"].append(hora)
            if sum(1 for x in L if (x["prob"] or 0) >= 50 or (x["pp"] or 0) >= 0.5) * 2 >= len(L): ev["lluvia"].append(hora)
            r2 = rf[1] if len(rf) >= 2 else (rf[0] if rf else None)
            if r2 is not None and r2 >= U["rafaga_fuerte"]: ev["viento"].append(hora)
        out[str(e)] = ev; temp[str(e)] = tp
    return out, temp


def observado(serie, U):
    """{evento: (inicio, fin)} con el minuto en que se cumplió por primera y última vez la condición."""
    cond = {"helada": lambda x: x[1] is not None and x[1] <= 0,
            "riesgo_helada": lambda x: x[1] is not None and x[1] <= U["riesgo_helada"],
            "calor": lambda x: x[1] is not None and x[1] >= 30,
            "calor_extremo": lambda x: x[1] is not None and x[1] >= U["calor_extremo"],
            "viento": lambda x: x[3] is not None and x[3] >= U["rafaga_fuerte"]}
    out = {}
    for k, c in cond.items():
        ts = [x[0] for x in serie if c(x)]
        if ts: out[k] = (ts[0], ts[-1])
    sube = [b[0] for a, b in zip(serie, serie[1:]) if a[4] is not None and b[4] is not None and b[4] > a[4]]
    if serie and serie[0][4] and serie[0][4] > 0: sube = [serie[0][0]] + sube
    if sube: out["lluvia"] = (sube[0], sube[-1])
    return out


def main():
    ver, U, M = D._reglas()
    hoy = dt.datetime.now(AR).date(); nombre = {str(e): n.replace("Est. ", "") for n, _, _, _, e in C.ESTACIONES}
    casos = []; err_hora = {}
    d = DESDE
    while d < hoy:
        F, TP = franjas(d, U, M); OBS = medido(d)
        for e, serie in OBS.items():
            if e not in F or len(serie) < 50: continue   # al menos ~4 h de datos cada 5 minutos
            # Horas con dato: 6 lecturas o más (el colector tuvo huecos el 23/11, antes de pasar a dos turnos por hora).
            cuenta = {}
            for x in serie: cuenta[x[0].hour] = cuenta.get(x[0].hour, 0) + 1
            cubiertas = {h for h, n in cuenta.items() if n >= 6}
            ob = observado(serie, U)
            for k in EVENTOS:
                pron = F[e][k]; o = ob.get(k)
                if not pron and not o: continue
                c = dict(dia=d.isoformat(), estacion=nombre.get(e, e), evento=k,
                         franja_pron=[min(pron), max(pron) + 1] if pron else None,
                         inicio_med=o[0].strftime("%H:%M") if o else None, fin_med=o[1].strftime("%H:%M") if o else None)
                if pron and o:
                    ini = dt.datetime.combine(d, dt.time(min(pron)))
                    c["desfase_inicio_min"] = round((o[0] - ini).total_seconds() / 60)
                    horas_med = {x[0].hour for x in serie if (x[0] >= o[0] and x[0] <= o[1])}
                    c["horas_coinciden"] = len(horas_med & set(pron)); c["horas_pron"] = len(pron); c["horas_med"] = len(horas_med)
                c["resultado"] = "acierto" if pron and o else ("falsa alarma" if pron else "no avisado")
                # Sin el dato de la franja avisada no se puede decir que no pasó: queda "sin dato" y no cuenta.
                if pron and not o and len(set(pron) & cubiertas) * 2 < len(pron): c["resultado"] = "sin dato"
                casos.append(c)
            # temperatura hora por hora
            por_h = {}
            for x in serie:
                if x[1] is not None: por_h.setdefault(x[0].hour, []).append(x[1])
            for h, v in por_h.items():
                if h in TP.get(e, {}) and h in cubiertas: err_hora.setdefault(nombre.get(e, e), {}).setdefault(h, []).append(TP[e][h] - st.mean(v))
        d += dt.timedelta(days=1)
    resumen = {}
    for c in casos:
        if c["resultado"] == "sin dato": continue
        r = resumen.setdefault((c["estacion"], c["evento"]), dict(acierto=0, falsa=0, no_avisado=0, desfases=[]))
        r[{"acierto": "acierto", "falsa alarma": "falsa", "no avisado": "no_avisado"}[c["resultado"]]] += 1
        if "desfase_inicio_min" in c: r["desfases"].append(c["desfase_inicio_min"])
    L = [f"# Horarios de los avisos contra el dato cada 5 minutos (actualizado {dt.datetime.now(AR):%Y-%m-%d %H:%M})", "",
         f"Desde el {DESDE:%d/%m/%Y}. Reglas de la app: {ver}. Desfase: minutos entre el comienzo de la franja que mostraría "
         "la app y el momento en que la estación lo midió por primera vez (positivo = llegó después de lo avisado).",
         "El viento medido es la velocidad media (el dato cada 5 minutos no trae ráfaga).", "",
         "## Por estación y fenómeno", "", "| Estación | Fenómeno | Aciertos | Falsas | No avisados | Desfase mediano (min) |", "|---|---|---|---|---|---|"]
    for (e, k), r in sorted(resumen.items()):
        L.append(f"| {e} | {k} | {r['acierto']} | {r['falsa']} | {r['no_avisado']} | "
                 + (f"{st.median(r['desfases']):+.0f}" if r["desfases"] else "—") + " |")
    L += ["", "## Temperatura hora por hora: error medio (pronóstico − medido, °C)", "",
          "Por bloque de horas, el error con signo (+ = la app más caliente que lo medido). Al final, el error medio sin signo y las horas medidas.", "",
          "| Estación | 0–5 h | 6–11 h | 12–17 h | 18–23 h | Error sin signo | Horas |", "|---|---|---|---|---|---|---|"]
    for e, hs in sorted(err_hora.items()):
        def bloque(a, b):
            v = [x for h in range(a, b + 1) for x in hs.get(h, [])]
            return f"{st.mean(v):+.1f}" if v else "—"
        todo = [x for v in hs.values() for x in v]
        L.append(f"| {e} | {bloque(0, 5)} | {bloque(6, 11)} | {bloque(12, 17)} | {bloque(18, 23)} | "
                 f"{st.mean(abs(x) for x in todo):.1f}° | {len(todo)} |")
    L += ["", "## Casos", "", "| Día | Estación | Fenómeno | Franja de la app | Medido | Desfase (min) | Resultado |", "|---|---|---|---|---|---|---|"]
    for c in casos:
        fr = f"{c['franja_pron'][0]:02d}–{c['franja_pron'][1]:02d} h" if c["franja_pron"] else "—"
        me = f"{c['inicio_med']}–{c['fin_med']}" if c["inicio_med"] else "—"
        L.append(f"| {c['dia']} | {c['estacion']} | {c['evento']} | {fr} | {me} | {c.get('desfase_inicio_min', '—')} | {c['resultado']} |")
    estado = dict(actualizado=f"{dt.datetime.now(AR):%Y-%m-%d %H:%M}", desde=DESDE.isoformat(), reglas=ver, casos=casos,
                  error_temp_por_hora={e: {h: round(st.mean(v), 2) for h, v in sorted(hs.items())} for e, hs in err_hora.items()})
    json.dump(estado, open(os.path.join(DATOS, "duelo", "horarios_estado.json"), "w", encoding="utf8"), ensure_ascii=False, indent=1)
    open(os.path.join(DATOS, "duelo", "horarios.md"), "w", encoding="utf8").write("\n".join(L) + "\n")
    print("\n".join(L[:60]))


if __name__ == "__main__":
    main()
