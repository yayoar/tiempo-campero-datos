"""Umbrales por estación: seguimiento mensual de los candidatos (24/11/2026, decidido con Yayo).

Para cada día nuevo (desde "desde" en datos/duelo/candidatos.json) y cada estación del Valle de Uco:
  - lo que habría mostrado la app en el punto de la estación, con el pronóstico guardado por el colector
    (el más cercano a las 8 h del día anterior, igual que duelo.py), con la regla actual y con el candidato;
  - lo que pasó, con las mismas reglas del duelo (helada <= 0°, lluvia >= 1 mm, calor >= 35°,
    viento fuerte = ráfaga >= 15 m/s sin zonda).
Cómo decide la app (leído en Combinador.kt y Pantallas.kt, APK 0.35b):
  helada: mínima del Valle <= riesgo_helada; llueve: al menos ceil(fraccion * n) modelos con lluvia >= lluvia_aviso_mm;
  calor: máxima del Valle >= calor_extremo; viento fuerte: segunda ráfaga más alta entre modelos >= rafaga_fuerte.
Escribe datos/duelo/candidatos.md (para leer) y datos/duelo/candidatos_estado.json.
Confirmado: con 10 casos o más en días nuevos, el candidato supera al actual por 5 puntos de efectividad o más,
no tiene más no avisados y no aumenta significativamente las falsas alarmas (como mucho un 5% más, o 1 más si son pocas; decidido con Yayo el 24/11/2026). La app hoy no admite umbrales por estación: lo confirmado queda para un APK nuevo.
"""
import datetime as dt, glob, gzip, json, math, os
import duelo as D, duelo_total as T, colector as C

AQUI = os.path.dirname(os.path.abspath(__file__)); DATOS = os.path.join(AQUI, "datos")
AR = dt.timezone(dt.timedelta(hours=-3))


def _num(c, i):
    try: return float(c[i])
    except Exception: return None


def verdad_dia(dia):
    filas = {}
    for f in {f"{dia:%Y-%m}", f"{(dia + dt.timedelta(days=1)):%Y-%m}"}:
        ruta = os.path.join(DATOS, "estaciones", "dacc", f + ".csv.gz")
        if not os.path.exists(ruta): continue
        for l in gzip.open(ruta, "rt"):
            c = l.rstrip("\n").split(",")
            try: t = T.hora_dacc(c[1])
            except Exception: continue
            if t.date() == dia: filas.setdefault(c[0], {})[t.hour] = c
    conf = T.zonda_valle(filas, _num)
    out = {}
    for e, h in filas.items():
        if len(h) < 20: continue
        tx = [x for x in (_num(c, 8) for c in h.values()) if x is not None and -30 < x < 50]
        tn = [x for x in (_num(c, 9) for c in h.values()) if x is not None and -30 < x < 50]
        if not tx or not tn: continue
        pp = [p for p in (_num(c, 27) for c in h.values()) if p is not None and 0 <= p < 80]
        ok = [b for a, b, cc in ((_num(c, 12), _num(c, 14), _num(c, 16)) for c in h.values())
              if None not in (a, b, cc) and b <= 45 and b <= cc * 5 + 6]
        zon = e in conf
        out[e] = dict(helada=min(tn) <= 0, lluvia=sum(pp) >= 1, calor=max(tx) >= 35,
                      viento=(not zon) and max(ok, default=0) >= 15)
    return out


def pronostico_dia(dia, M):
    f = D.elegir("open_meteo", dia)
    if not f: return {}
    dat = json.load(gzip.open(f))["datos"]
    nombres = [p[0] for p in C.PUNTOS]; out = {}
    for n, _, la, lo, e in C.ESTACIONES:
        if n not in nombres: continue
        i = nombres.index(n); por = {}
        for m in D.PESOS:
            r = dat.get(m); r = [r] if isinstance(r, dict) else r
            if not isinstance(r, list) or len(r) <= i or "hourly" not in r[i]: continue
            h = r[i]["hourly"]; ix = [q for q, x in enumerate(h["time"]) if x.startswith(dia.isoformat())]
            g = lambda v: [h[v][q] for q in ix if h[v][q] is not None] if v in h else []
            tt = g("temperature_2m")
            if len(tt) < 20: continue
            hr = g("relative_humidity_2m"); rf = g("wind_gusts_10m")
            por[m] = dict(tmin=min(tt), tmax=max(tt), pp=sum(h["precipitation"][q] or 0 for q in ix),
                          hr=int(min(hr)) if hr else None, raf=max(rf) if rf else None)
        if len(por) < 4: continue
        pe = D._pesos_valle(M, la, lo)
        try: tmin, tmax = D._valle_minima(M, pe, dia, por), D._valle_maxima(M, pe, dia, por)
        except Exception: continue
        rafs = sorted([x["raf"] for x in por.values() if x["raf"] is not None], reverse=True)
        out[str(e)] = dict(tmin=tmin, tmax=tmax, pps=[x["pp"] for x in por.values()],
                           raf2=rafs[1] if len(rafs) >= 2 else (rafs[0] if rafs else None))
    return out


def avisa(fen, p, u):
    if fen == "helada": return p["tmin"] is not None and p["tmin"] <= u
    if fen == "calor": return p["tmax"] is not None and p["tmax"] >= u
    if fen == "viento": return p["raf2"] is not None and p["raf2"] >= u
    if fen == "lluvia":
        mm, f = u; n = len(p["pps"])
        return n >= 2 and sum(x >= mm for x in p["pps"]) >= math.ceil(f * n)


def contar(pares):
    tp = sum(a and r for a, r in pares); fp = sum(a and not r for a, r in pares); fn = sum(r and not a for a, r in pares)
    return dict(aciertos=tp, falsas=fp, no_avisados=fn, efectividad=round(tp / (tp + fp + fn), 3) if tp + fp + fn else None)


def main():
    cfg = json.load(open(os.path.join(DATOS, "duelo", "candidatos.json"), encoding="utf8"))
    ver, U, M = D._reglas()
    actual = {"helada": U["riesgo_helada"], "lluvia": (U["lluvia_aviso_mm"], U["fraccion_aviso"]),
              "calor": U["calor_extremo"], "viento": U["rafaga_fuerte"]}
    desde = dt.date.fromisoformat(cfg["desde"]); hoy = dt.datetime.now(AR).date()
    pares_act = {}; pares_cand = {}; dias = 0
    d = desde
    while d < hoy:
        V, P = verdad_dia(d), pronostico_dia(d, M)
        if V and P: dias += 1
        for e, p in P.items():
            v = V.get(e)
            if not v: continue
            for fen, u in actual.items():
                pares_act.setdefault((fen, e), []).append((avisa(fen, p, u), v[fen]))
            for i, c in enumerate(cfg["candidatos"]):
                if c["estacion"] == e:
                    u = tuple(c["candidato"]) if isinstance(c["candidato"], list) else c["candidato"]
                    pares_cand.setdefault(i, []).append((avisa(c["fenomeno"], p, u), v[c["fenomeno"]], avisa(c["fenomeno"], p, actual[c["fenomeno"]])))
        d += dt.timedelta(days=1)
    nombre = {str(e): n.replace("Est. ", "") for n, _, _, _, e in C.ESTACIONES}
    estado = {"actualizado": dt.datetime.now(AR).strftime("%Y-%m-%d %H:%M"), "desde": cfg["desde"], "dias": dias,
              "reglas": ver, "candidatos": [], "por_estacion": {}}
    L = [f"# Umbrales por estación — seguimiento (actualizado {estado['actualizado']})", "",
         f"Días nuevos medidos desde el {desde:%d/%m/%Y}: {dias}. Reglas de la app: {ver}.",
         "Efectividad = aciertos / (aciertos + falsas alarmas + no avisados). Confirmado: 10 casos o más, "
         "5 puntos más de efectividad, no más no avisados y no más de un 5% de falsas alarmas extra (o 1 si son pocas). La app hoy no admite umbrales por estación: "
         "lo confirmado queda para un APK nuevo, y se decide con Yayo.", "",
         "## Candidatos", "", "| Fenómeno | Estación | Actual → candidato | Casos | Actual | Candidato | Estado |", "|---|---|---|---|---|---|---|"]
    for i, c in enumerate(cfg["candidatos"]):
        pr = pares_cand.get(i, [])
        a = contar([(x[2], x[1]) for x in pr]); b = contar([(x[0], x[1]) for x in pr]); casos = sum(x[1] for x in pr)
        conf = (casos >= 10 and a["efectividad"] is not None and b["efectividad"] is not None
                and b["efectividad"] - a["efectividad"] >= 0.05 and b["no_avisados"] <= a["no_avisados"]
                and b["falsas"] <= a["falsas"] + max(1, 0.05 * a["falsas"]))
        est = "confirmado" if conf else ("faltan casos" if casos < 10 else "no se confirma (todavía)")
        estado["candidatos"].append(dict(c, casos_nuevos=casos, actual_nuevos=a, candidato_nuevos=b, estado=est))
        f = lambda m: "—" if m["efectividad"] is None else f"{m['efectividad']:.0%} ({m['aciertos']}/{m['falsas']}/{m['no_avisados']})"
        L.append(f"| {c['fenomeno']} | {c['nombre']} | {c['actual']} → {c['candidato']} | {casos} | {f(a)} | {f(b)} | {est} |")
    L += ["", "## Regla actual, estación por estación (días nuevos)", "",
          "| Estación | Helada | Lluvia | Calor | Viento fuerte |", "|---|---|---|---|---|"]
    for e in sorted({k[1] for k in pares_act}, key=lambda x: nombre.get(x, x)):
        celdas = []
        for fen in ("helada", "lluvia", "calor", "viento"):
            m = contar(pares_act.get((fen, e), [])); estado["por_estacion"].setdefault(nombre.get(e, e), {})[fen] = m
            celdas.append("—" if m["efectividad"] is None else f"{m['efectividad']:.0%} ({m['aciertos']}/{m['falsas']}/{m['no_avisados']})")
        L.append(f"| {nombre.get(e, e)} | " + " | ".join(celdas) + " |")
    json.dump(estado, open(os.path.join(DATOS, "duelo", "candidatos_estado.json"), "w", encoding="utf8"), ensure_ascii=False, indent=1)
    open(os.path.join(DATOS, "duelo", "candidatos.md"), "w", encoding="utf8").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
