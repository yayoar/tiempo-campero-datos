"""Alertas oficiales del SMN para Mendoza (formato CAP, público).

Cada 3 horas:
  - datos/smn_alertas.json: alertas vigentes o futuras que tocan Mendoza, con su polígono (la app ve si su finca cae adentro).
  - datos/historial_smn/AAAA-MM.jsonl: cada alerta nueva, una sola vez, para medir después cuánto acierta el SMN
    con la misma vara que usamos para la app.
Niveles: severity Moderate = amarilla, Severe = naranja, Extreme = roja.
"""
import os, re, json, datetime as dt, html, requests

AQUI = os.path.dirname(os.path.abspath(__file__))
S = requests.Session(); S.headers["User-Agent"] = "tiempo-campero-ayudante"
CAJA = (-37.6, -32.0, -70.6, -66.5)          # Mendoza
NIVEL = {"Minor": "amarilla", "Moderate": "amarilla", "Severe": "naranja", "Extreme": "roja"}


def get(url):
    for _ in range(3):
        try:
            r = S.get(url, timeout=60)
            if r.status_code == 200: return r
        except requests.RequestException:
            pass
    return None


def etiqueta(x, t):
    m = re.search(rf"<{t}>(.*?)</{t}>", x, re.S)
    return html.unescape(m.group(1).strip()) if m else ""


def main():
    ahora = dt.datetime.now(dt.timezone.utc)
    rss = get("https://ssl.smn.gob.ar/CAP/AR.php")
    if rss is None: raise SystemExit("SMN no respondió")
    links = sorted(set(re.findall(r"<link>(https://ssl\.smn\.gob\.ar/feeds/CAP/xml_generados/[^<]+)</link>", rss.text)))
    alertas = []
    for u in links:
        r = get(u)
        if r is None: continue
        x = r.text
        for info in re.findall(r"<info>(.*?)</info>", x, re.S):
            for area in re.findall(r"<area>(.*?)</area>", info, re.S):
                pol = etiqueta(area, "polygon")
                if not pol: continue
                pts = [[float(a) for a in p.split(",")] for p in pol.split()]
                if not any(CAJA[0] <= la <= CAJA[1] and CAJA[2] <= lo <= CAJA[3] for la, lo in pts): continue
                alertas.append({"id": etiqueta(x, "identifier"), "enviada": etiqueta(x, "sent"), "evento": etiqueta(info, "event"),
                                "nivel": NIVEL.get(etiqueta(info, "severity"), etiqueta(info, "severity")),
                                "desde": etiqueta(info, "onset"), "hasta": etiqueta(info, "expires"),
                                "descripcion": etiqueta(info, "description"), "zona": etiqueta(area, "areaDesc"),
                                "poligono": [[round(la, 3), round(lo, 3)] for la, lo in pts]})
    os.makedirs(os.path.join(AQUI, "datos", "historial_smn"), exist_ok=True)
    json.dump({"actualizado": ahora.replace(microsecond=0).isoformat(), "fuente": "Servicio Meteorológico Nacional (CAP)",
               "alertas": alertas}, open(os.path.join(AQUI, "datos", "smn_alertas.json"), "w"), ensure_ascii=False)
    # historial: una línea por alerta y polígono, sin repetir
    hist = os.path.join(AQUI, "datos", "historial_smn", ahora.strftime("%Y-%m") + ".jsonl")
    vistos = set()
    if os.path.exists(hist):
        for l in open(hist):
            try: a = json.loads(l); vistos.add((a["id"], str(a["poligono"][:3])))
            except Exception: pass
    with open(hist, "a") as f:
        for a in alertas:
            k = (a["id"], str(a["poligono"][:3]))
            if k not in vistos: f.write(json.dumps({**a, "guardada": ahora.isoformat()}, ensure_ascii=False) + "\n"); vistos.add(k)
    print(len(links), "alertas en el país;", len(alertas), "tocan Mendoza")


if __name__ == "__main__":
    main()
