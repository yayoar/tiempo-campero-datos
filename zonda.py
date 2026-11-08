"""Ayudante diario de Tiempo Campero: puntaje de zonda para Mendoza.

Corre en GitHub (dos veces por día) y deja el resultado en datos/zonda.json, que la app lee.
  - noche (~19:30 h): aviso para MAÑANA con modelos globales + ensamble NOAA + WRF del SMN (corrida 18 UTC)
  - mañana (~06 h): actualización para HOY con las corridas 18, 00 y 06 UTC del WRF
Zonas: norte (Gran Mendoza y el Este, punto El Plumerillo), valle (Valle de Uco), sur (San Rafael, Alvear, Malargüe).
Niveles: 2 = "Zonda probable", 1 = "Posible zonda", 0 = sin aviso.
Uso: python zonda.py noche|manana [AAAA-MM-DD]
"""
import sys, os, io, json, time, datetime as dt, concurrent.futures as cf
import numpy as np, requests, h5py

AQUI = os.path.dirname(os.path.abspath(__file__))
MOD = json.load(open(os.path.join(AQUI, "modelo_zonda.json")))
S = requests.Session(); S.headers["User-Agent"] = "tiempo-campero-ayudante"
PTS = {"mza": (-32.83, -68.79), "sr": (-34.59, -68.40), "valle": (-33.73, -69.12)}
CHILE = (-33.45, -70.67)
ZONA = {"mza": "norte", "sr": "sur", "valle": "valle"}
WRF_PTS = {"mza": ["SAME"], "sr": ["SAMR"], "valle": ["LaConsulta", "Pareditas", "Tunuyan", "Tupungato"]}
# Los 5 modelos con ráfagas en el historial con que se entrenó (ECMWF y JMA no traían ráfagas y el laboratorio los salteaba).
M5 = ["gem_seamless", "icon_seamless", "ukmo_seamless", "meteofrance_seamless", "gfs_seamless"]
M3 = ["gfs_seamless", "ecmwf_ifs025", "icon_seamless"]


def get(url, **kw):
    for i in range(5):
        try:
            r = S.get(url, timeout=90, **kw)
            if r.status_code in (200, 206): return r
            if r.status_code == 404: return None
        except requests.RequestException:
            pass
        time.sleep(5 * (i + 1))
    return None


def om(la, lo, variables, modelos, d0, d1):
    u = ("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s&hourly=%s&models=%s&start_date=%s&end_date=%s"
         "&timezone=America%%2FArgentina%%2FMendoza&wind_speed_unit=kmh") % (la, lo, ",".join(variables), ",".join(modelos), d0, d1)
    r = get(u)
    if r is None: raise RuntimeError("Open-Meteo no respondió")
    return r.json()["hourly"]


def serie(H, var, modelos):
    out = []
    for m in modelos:
        v = H.get(f"{var}_{m}")
        out.append([np.nan if x is None else x for x in v] if v else [np.nan] * len(H["time"]))
    return np.array(out, float)


def globales(D):
    """Mismas cuentas que el laboratorio (votos, secos, ráfaga, humedad mínima, presión Chile-Mendoza, temperatura)."""
    ayer = D - dt.timedelta(1)
    Hc = om(*CHILE, ["pressure_msl"], M3, ayer, D)
    pch = np.nanmean(serie(Hc, "pressure_msl", M3), 0)
    res = {}
    for lugar, (la, lo) in PTS.items():
        H = om(la, lo, ["wind_gusts_10m", "relative_humidity_2m", "precipitation"], M5, D, D)
        g, h, p = (serie(H, v, M5) for v in ("wind_gusts_10m", "relative_humidity_2m", "precipitation"))
        votos = ((g >= 40) & (h <= 25) & ~(p >= 0.2)).sum(0).max()
        secos = (h <= 20).sum(0).max()
        Hp = om(la, lo, ["pressure_msl", "temperature_2m"], M3, ayer, D)
        pr = np.nanmean(serie(Hp, "pressure_msl", M3), 0); te = np.nanmean(serie(Hp, "temperature_2m", M3), 0)
        hoy = np.array([t.startswith(D.isoformat()) for t in Hp["time"]]); antes = ~hoy
        res[lugar] = {"votos": int(votos), "secos": int(secos), "raf": float(np.round(np.nanmax(g))), "hmin": float(np.round(np.nanmin(h))),
                      "dif_max": round(float(np.nanmax((pch - pr)[hoy])), 1), "p_min": round(float(np.nanmin(pr[hoy])), 1),
                      "caida": round(float(np.nanmin(pr[hoy]) - np.nanmean(pr[antes])), 1), "t_max": round(float(np.nanmax(te[hoy])), 1)}
    return res


def gefs(D):
    """Ensamble GEFS 00 UTC del día anterior, plazos 42 y 45 h (15 y 18 h del día D)."""
    import eccodes
    D0 = D - dt.timedelta(1)

    def campos(url):
        r = get(url + ".idx")
        if r is None: return None
        idx = r.text.splitlines(); out = {}
        for i, l in enumerate(idx):
            p = l.split(":"); clave = p[3] + ":" + p[4]
            if clave in ("GUST:surface", "RH:2 m above ground"):
                ini = int(p[1]); fin = int(idx[i + 1].split(":")[1]) - 1 if i + 1 < len(idx) else ""
                b = get(url, headers={"Range": f"bytes={ini}-{fin}"})
                if b is None: return None
                gm = eccodes.codes_new_from_message(b.content)
                out[clave[:2]] = {n: eccodes.codes_grib_find_nearest(gm, la, lo % 360)[0]["value"] for n, (la, lo) in PTS.items()}
                eccodes.codes_release(gm)
        return out if len(out) == 2 else None

    def miembro(mf):
        m, f = mf
        return m, campos(f"https://noaa-gefs-pds.s3.amazonaws.com/gefs.{D0:%Y%m%d}/00/atmos/pgrb2sp25/gep{m:02d}.t00z.pgrb2s.0p25.f{f:03d}")

    with cf.ThreadPoolExecutor(10) as ex:
        res = list(ex.map(miembro, [(m, f) for m in range(1, 21) for f in (42, 45)]))
    out = {}
    for lugar in PTS:
        g, h = {}, {}
        for m, r in res:
            if not r: continue
            g[m] = max(g.get(m, -1), r["GU"][lugar] * 3.6); h[m] = min(h.get(m, 999), r["RH"][lugar])
        if len(g) < 10: raise RuntimeError("ensamble GEFS incompleto")
        G = np.array([g[m] for m in g]); Hh = np.array([h[m] for m in g])
        out[lugar] = {"ens_raf_med": float(np.median(G)), "ens_hr_med": float(np.median(Hh)),
                      "ens_frac_seco_ventoso": float(((Hh <= 20) & (G >= 25)).mean()), "ens_raf_p90": float(np.quantile(G, 0.9))}
    return out


def wrf(D, corrida):
    """WRF 4 km del SMN en los puntos de cada zona, de 6 a 6 h local; corridas '18' (día anterior), '00' y '06'."""
    import fsspec
    fs = fsspec.filesystem("http")
    base, plazos = {"18": (D - dt.timedelta(1), range(15, 37, 2)), "00": (D, range(12, 34, 2)), "06": (D, range(3, 25, 2))}[corrida]

    def leer(h):
        url = f"https://smn-ar-wrf.s3.amazonaws.com/DATA/WRF/DET/{base:%Y/%m/%d}/{corrida}/WRFDETAR_01H_{base:%Y%m%d}_{corrida}_{h:03d}.nc"
        for _ in range(3):
            try:
                with fs.open(url, block_size=2 ** 20) as fh:
                    g = h5py.File(fh, "r")
                    return h, {n: (float(g["magViento10"][0, i, j]) * 3.6, float(g["dirViento10"][0, i, j]), float(g["HR2"][0, i, j]))
                               for n, (i, j) in MOD["wrf_puntos"].items()}
            except FileNotFoundError:
                return h, None
            except Exception:
                time.sleep(3)
        return h, None

    with cf.ThreadPoolExecutor(6) as ex:
        res = list(ex.map(leer, plazos))
    filas = []
    for h, v in res:
        if v is None: continue
        hl = int(corrida) + h - 3 - (24 if corrida == "18" else 0)
        if 6 <= hl <= 30: filas += [(n, *x) for n, x in v.items()]
    if len(filas) < 30: return None
    out = {}
    for lugar, pts in WRF_PTS.items():
        x = [f for f in filas if f[0] in pts]; w = [f for f in x if 180 <= f[2] <= 360]
        out[lugar] = {f"wo{corrida}": max([f[1] for f in w], default=0.0), f"hr{corrida}": min(f[3] for f in x),
                      f"ws{corrida}": max([f[1] * (f[3] <= 30) for f in w], default=0.0)}
    return out


def puntaje(m, feats, relleno):
    x = np.array([feats.get(c, relleno.get(c, np.nan)) for c in m["columnas"]], float)
    z = (x - np.array(m["media"])) / np.array(m["escala"])
    return float(1 / (1 + np.exp(-(z @ np.array(m["coef"]) + m["b"]))))


def main():
    producto = sys.argv[1]
    hoy = (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(hours=3)).date()
    D = dt.date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else (hoy + dt.timedelta(1) if producto == "noche" else hoy)
    corridas = ["18"] if producto == "noche" else ["18", "00", "06"]
    gl = globales(D)
    candidato = any(f["votos"] >= 1 or f["dif_max"] >= MOD["lugares"][l]["dif_max_q90"] or f["raf"] >= 60 for l, f in gl.items())
    zonas, faltan, en, W = {}, [], {}, {}
    if candidato:
        en = gefs(D)
        for c in corridas:
            r = wrf(D, c)
            if r is None: faltan.append(c); continue
            for l, v in r.items(): W.setdefault(l, {}).update(v)
    for lugar, f in gl.items():
        L = MOD["lugares"][lugar]
        if not candidato:
            zonas[ZONA[lugar]] = {"nivel": 0, "puntaje": None}; continue
        m = L["manana" if producto == "manana" else "noche"]
        p = puntaje(m, {**f, **en[lugar], **W.get(lugar, {})}, L["relleno_wrf"])
        nivel = 2 if p >= m["probable"] else 1 if p >= m["posible"] else 0
        zonas[ZONA[lugar]] = {"nivel": nivel, "puntaje": round(p, 3)}
    ruta = os.path.join(AQUI, "datos", "zonda.json")
    try: actual = json.load(open(ruta))
    except Exception: actual = {"dias": {}}
    dias = {k: v for k, v in actual.get("dias", {}).items() if k >= (hoy - dt.timedelta(1)).isoformat()}
    ahora = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).replace(microsecond=0).isoformat() + "Z"
    dias[D.isoformat()] = {"producto": producto, "generado": ahora, "zonas": zonas,
                           **({"faltan_corridas_smn": faltan} if faltan else {})}
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    json.dump({"actualizado": ahora, "version": 1, "dias": dict(sorted(dias.items()))}, open(ruta, "w"), indent=1, ensure_ascii=False)
    print(json.dumps({"fecha": D.isoformat(), "producto": producto, "candidato": candidato, "globales": gl, "ensamble": en, "wrf": W, "zonas": zonas, "faltan": faltan}, ensure_ascii=False))


if __name__ == "__main__":
    main()
