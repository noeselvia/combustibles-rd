"""Monitor de combustibles RD: descarga, limpia y une las fuentes en data/fuel.json.

Fuentes:
  - MICM datos abiertos: histórico semanal de precios 2010-hoy (CSV, enteros redondeados)
  - MICM avisos semanales (PDF): precios con decimales + desglose de impuestos, márgenes y subsidio
  - FRED: petróleo WTI diario (DCOILWTICO) y gasolina regular en EE. UU. (GASREGW)
  - Banco Central RD: tasa de cambio diaria USD/DOP
  - MICM datos abiertos: estaciones de expendio registradas

Uso: python3 pipeline/build_data.py
"""

from __future__ import annotations

import io
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "fuel.json"

UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130 Safari/537.36",
    "Accept-Language": "es",
}

URL_HISTORY = "https://micm.gob.do/transparencias/datos-abiertos/precios-de-combustibles/precios-de-combustibles-2010-2026.csv"
URL_AVISOS = "https://micm.gob.do/direcciones/combustibles/avisos-semanales-de-precios/avisos-semanales-de-precios-de-combustibles/"
URL_STATIONS = "https://micm.gob.do/transparencias/datos-abiertos/estaciones-registradas/Estaciones%20de%20Expendios%20Registradas%20MICM%202026.csv"
URL_WTI = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DCOILWTICO"
URL_USGAS = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=GASREGW"
URL_FX = "https://cdn.bancentral.gov.do/documents/estadisticas/mercado-cambiario/documents/TASA_DOLAR_REFERENCIA_MC.xlsx"

# Avisos que ya no aparecen en la página pero cubren semanas que el CSV aún no trae
EXTRA_AVISOS = [
    "https://micm.gob.do/wp-content/uploads/2026/10/AVISO-PRE.-SEM.CORTE-03-09-OCT-DE-2026.pdf",
]

# Columna del CSV del MICM -> clave interna. Solo los combustibles que usa el público.
FUELS = {
    "GASOLINA PREMIUM": "gasolina_premium",
    "GASOLINA REGULAR": "gasolina_regular",
    "GASOIL OPTIMO": "gasoil_optimo",
    "GASOIL REGULAR": "gasoil_regular",
    "GLP": "glp",
    "KEROSENE": "kerosene",
}

# Fila del aviso PDF -> clave interna
PDF_ROWS = {
    "Gasolina Premium": "gasolina_premium",
    "Gasolina Regular": "gasolina_regular",
    "Gasoil Optimo": "gasoil_optimo",
    "Gasoil Regular": "gasoil_regular",
    "Kerosene": "kerosene",
}

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
    "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}
MESES_CORTOS = {"ENE": 1, "FEB": 2, "MAR": 3, "ABR": 4, "MAY": 5, "JUN": 6, "JUL": 7,
                "AGO": 8, "SEP": 9, "SEPT": 9, "OCT": 10, "NOV": 11, "DIC": 12}


def fetch(url: str, name: str, headers: dict | None = UA) -> bytes:
    """Descarga una fuente y guarda una copia cruda; si falla, usa la última copia guardada."""
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / name
    try:
        r = requests.get(url, headers=headers, timeout=60)
        r.raise_for_status()
        path.write_bytes(r.content)
        return r.content
    except requests.RequestException as e:
        if path.exists():
            print(f"  ! {name}: {e}; usando copia guardada", file=sys.stderr)
            return path.read_bytes()
        raise


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().upper())


# ---------------------------------------------------------------- histórico CSV

def load_history() -> pd.DataFrame:
    raw = fetch(URL_HISTORY, "precios-historico.csv").decode("latin-1")
    df = pd.read_csv(io.StringIO(raw), sep=";")
    df.columns = [norm(c) for c in df.columns]
    df = df.rename(columns={"AÑO": "ANIO"})

    # Cada fila es una semana sábado-viernes, pero la fuente es inconsistente: "MES" a veces
    # es el mes del inicio y a veces el del final, y hay días mal escritos. Se prueban el
    # inicio y el final en el mes indicado y sus vecinos, se queda con los que caen en
    # sábado, y se elige el más cercano a "semana anterior + 7 días".
    starts: list[date] = []
    for _, row in df.iterrows():
        d0, d1 = int(row["DIA DESDE"]), int(row["DIA HASTA"])
        # Por las tres primeras letras: la fuente trae erratas como "Febreo"
        month, year = MESES_CORTOS[str(row["MES"]).strip().upper()[:3]], int(row["ANIO"])
        candidates = []
        for dm in (-1, 0, 1):
            m, y = month + dm, year
            if m == 0:
                m, y = 12, y - 1
            elif m == 13:
                m, y = 1, y + 1
            for day, offset in ((d0, 0), (d1, -6)):
                try:
                    c = date(y, m, day) + timedelta(days=offset)
                except ValueError:
                    continue
                if c.weekday() == 5:
                    candidates.append(c)
        expected = starts[-1] + timedelta(days=7) if starts else date(year, month, d0)
        if not candidates:
            candidates = [expected]
        starts.append(min(candidates, key=lambda c: abs((c - expected).days)))
    out = pd.DataFrame({"start": pd.to_datetime(starts)})
    for col, key in FUELS.items():
        out[key] = pd.to_numeric(df[col], errors="coerce").replace(0, pd.NA)
    out = out.sort_values("start").drop_duplicates("start", keep="last").reset_index(drop=True)

    # Errores de captura: una semana que salta más de 8 % y vuelve al valor anterior
    # (p. ej. GLP 133 → 164 → 133). Se dejan vacías en vez de inventar un valor.
    for key in FUELS.values():
        v = out[key]
        for i in range(1, len(v) - 1):
            a, b, c = v.iloc[i - 1], v.iloc[i], v.iloc[i + 1]
            if pd.isna(a) or pd.isna(b) or pd.isna(c):
                continue
            if abs(a - c) <= 0.01 * a and abs(b - a) > 0.08 * a:
                print(f"  ! {key} {out.start.iloc[i]:%Y-%m-%d}: {b} parece un error (vecinos {a}, {c}); se omite", file=sys.stderr)
                out.loc[i, key] = pd.NA
    return out


# ---------------------------------------------------------------- avisos PDF

NUM = r"\(?-?[\d,]+\.\d+\)?"


def to_num(tok: str) -> float:
    neg = tok.startswith("(")
    v = float(tok.strip("()").replace(",", ""))
    return -v if neg else v


def parse_aviso(pdf: bytes) -> dict:
    text = PdfReader(io.BytesIO(pdf)).pages[0].extract_text()
    flat = re.sub(r"\s+", " ", text)

    # "del sábado diez (10) al día viernes dieciséis (16) de octubre de ... (2026)"
    # o, si cruza de mes, "del sábado treinta y uno (31) de octubre al día viernes ..."
    m = re.search(r"s[áa]bado [^(]*\((\d+)\)(?: de ([a-z]+))? al d[íi]a viernes [^(]*\((\d+)\) de ([a-z]+) de [^(]*\((\d{4})\)", flat, re.I)
    if not m:
        raise ValueError("no se encontró el período del aviso")
    d0, d1, year = int(m.group(1)), int(m.group(3)), int(m.group(5))
    m1 = MESES[m.group(4).lower()]
    m0 = MESES[m.group(2).lower()] if m.group(2) else m1
    start = date(year - (1 if m0 > m1 else 0), m0, d0)

    fuels = {}
    for line in text.splitlines():
        for label, key in PDF_ROWS.items():
            # La fila exacta (excluye variantes EGP-C/EGP-T de generación eléctrica)
            if re.match(rf"^{label} {NUM}", line.strip()):
                nums = [to_num(t) for t in re.findall(NUM, line)]
                paridad, ley112, advalorem, dist, detall, transp, sin_subsidio, ajuste, variacion = nums[:9]
                fuels[key] = {
                    "paridad": paridad,                  # costo de importación
                    "impuesto_112_00": ley112,           # impuesto selectivo fijo por galón
                    "impuesto_495_06": advalorem,        # ad-valorem 16 %
                    "margen_distribuidor": dist,
                    "margen_detallista": detall,
                    "transporte": transp,
                    "precio_sin_subsidio": sin_subsidio,
                    "subsidio": -ajuste,                 # el aviso lo muestra en negativo
                    "precio": round(sin_subsidio + ajuste, 2),
                    "variacion": variacion,
                }
    glp = re.search(rf"Gas Licuado de Petr[óo]leo \(GLP\)\s*\**\s*((?:{NUM}\s*){{9,11}})", flat)
    if glp:
        nums = [to_num(t) for t in re.findall(NUM, glp.group(1))]
        # paridad, 112-00, 495-06, distribuidor, detallista, transporte, precio, ajuste, comisión, precio oficial, variación
        fuels["glp"] = {
            "paridad": nums[0], "impuesto_112_00": nums[1], "impuesto_495_06": nums[2],
            "margen_distribuidor": nums[3], "margen_detallista": nums[4] + nums[8], "transporte": nums[5],
            "precio_sin_subsidio": round(nums[6] + nums[8], 2), "subsidio": abs(nums[7]),
            "precio": nums[9], "variacion": nums[10] if len(nums) > 10 else 0.0,
        }
    cilindros = {int(lb): to_num(v) for lb, v in re.findall(rf"Cilindros de (\d+) Libras[^)]*\)\**\s*({NUM})", flat)}
    fx = re.search(r"Tasa de Cambio[^R]*RD\$\s*([\d.]+)", flat)
    return {"start": start.isoformat(), "end": (start + timedelta(days=6)).isoformat(),
            "tasa_aviso": float(fx.group(1)) if fx else None, "fuels": fuels, "cilindros_glp": cilindros}


def load_avisos() -> list[dict]:
    html = fetch(URL_AVISOS, "avisos.html").decode("utf-8", "replace")
    urls = list(dict.fromkeys(re.findall(r'href="([^"]*AVISO[^"]*\.pdf)"', html, re.I) + EXTRA_AVISOS))
    avisos = []
    for url in urls:
        try:
            avisos.append(parse_aviso(fetch(url, Path(url).name)))
        except Exception as e:  # un PDF con formato distinto no debe tumbar el pipeline
            print(f"  ! {Path(url).name}: {e}", file=sys.stderr)
    # Archivo local de avisos ya procesados, para no perder semanas que salen de la página
    cache = RAW / "avisos.json"
    known = {a["start"]: a for a in (json.loads(cache.read_text()) if cache.exists() else [])}
    known.update({a["start"]: a for a in avisos})
    merged = sorted(known.values(), key=lambda a: a["start"])
    cache.write_text(json.dumps(merged, ensure_ascii=False, indent=1))
    return merged


# ---------------------------------------------------------------- mercados

def load_fred(url: str, name: str) -> pd.Series:
    # FRED corta la conexión con el User-Agent de navegador que piden los sitios del gobierno
    df = pd.read_csv(io.BytesIO(fetch(url, name, headers=None)))
    df.columns = ["date", "value"]
    df["date"] = pd.to_datetime(df["date"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df.dropna().set_index("date")["value"]


def load_fx() -> pd.Series:
    df = pd.read_excel(io.BytesIO(fetch(URL_FX, "tasa-bcrd.xlsx")), sheet_name="Diaria", header=2)
    df = df.rename(columns=lambda c: str(c).strip())
    df = df[pd.to_numeric(df["Año"], errors="coerce").notna()]
    dates = [date(int(y), MESES_CORTOS[str(m).strip().upper()[:3]], int(d)) for y, m, d in zip(df["Año"], df["Mes"], df["Día"])]
    return pd.Series(pd.to_numeric(df["Venta"]).values, index=pd.to_datetime(dates)).sort_index()


def weekly_mean(s: pd.Series, starts: pd.Series) -> list:
    """Promedio de la serie en los 7 días previos a cada semana (lo que el MICM usa para fijar precios)."""
    out = []
    for st in starts:
        win = s[(s.index >= st - pd.Timedelta(days=7)) & (s.index < st)]
        if win.empty:
            win = s[s.index < st].tail(1)
        out.append(round(float(win.mean()), 2) if not win.empty else None)
    return out


# ---------------------------------------------------------------- estaciones

def load_stations() -> list[dict]:
    raw = fetch(URL_STATIONS, "estaciones.csv").decode("latin-1")
    df = pd.read_csv(io.StringIO(raw), sep=";")
    df.columns = [c.strip() for c in df.columns]
    # "Tipo de combustible" viene escrito de muchas formas ("GNV - Líquidos", "GNV  GLP"…)
    tipo = df["Tipo de combustible"].astype(str).str.lower()
    df["liquidos"] = tipo.str.contains("quido").astype(int)
    df["glp"] = tipo.str.contains("glp").astype(int)
    df["gnv"] = tipo.str.contains("gnv").astype(int)
    # Mismas provincias escritas distinto ("San José De Ocoa", "Altagracia")
    prov = df["Provincia"].astype(str).str.strip().str.replace(r"\s+", " ", regex=True)
    prov = prov.str.title().str.replace(r"\b(De|Del|La|Las|Los)\b(?!$)", lambda m: m.group(1).lower(), regex=True)
    prov = prov.str[0].str.upper() + prov.str[1:]
    df["Provincia"] = prov.replace({"Altagracia": "La Altagracia"})
    g = df.groupby("Provincia").agg(total=("liquidos", "size"), liquidos=("liquidos", "sum"),
                                     glp=("glp", "sum"), gnv=("gnv", "sum"))
    g = g.sort_values("total", ascending=False)
    return [{"provincia": p, **{k: int(v) for k, v in row.items()}} for p, row in g.iterrows()]


# ---------------------------------------------------------------- unión

def build() -> dict:
    print("Descargando histórico MICM…")
    hist = load_history()
    print(f"  {len(hist)} semanas, {hist.start.min():%Y-%m-%d} a {hist.start.max():%Y-%m-%d}")
    print("Leyendo avisos semanales…")
    avisos = load_avisos()
    print(f"  {len(avisos)} avisos, último {avisos[-1]['start'] if avisos else '-'}")

    # Los avisos (con decimales) reemplazan o extienden las semanas del CSV
    rows = {}
    for _, r in hist.iterrows():
        rows[r["start"].date().isoformat()] = {k: (None if pd.isna(r[k]) else float(r[k])) for k in FUELS.values()}
    for a in avisos:
        rows.setdefault(a["start"], {k: None for k in FUELS.values()})
        for key, f in a["fuels"].items():
            rows[a["start"]][key] = f["precio"]
    weeks = sorted(rows)
    starts = pd.to_datetime(pd.Series(weeks))

    print("Descargando WTI, gasolina EE. UU. y tasa BCRD…")
    wti = load_fred(URL_WTI, "wti.csv")
    usgas = load_fred(URL_USGAS, "usgas.csv")
    fx = load_fx()

    series = {"semana": weeks, **{k: [rows[w][k] for w in weeks] for k in FUELS.values()},
              "wti_usd": weekly_mean(wti, starts), "tasa": weekly_mean(fx, starts),
              "gasolina_eeuu_usd": weekly_mean(usgas, starts)}

    latest = avisos[-1] if avisos else None
    print("Contando estaciones registradas…")
    stations = load_stations()

    return {
        "generado": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "semana_actual": latest,
        "series": series,
        "estaciones": stations,
        "fuentes": {
            "Histórico de precios (MICM, datos abiertos)": URL_HISTORY,
            "Avisos semanales de precios (MICM)": URL_AVISOS,
            "Petróleo WTI (FRED, EIA)": "https://fred.stlouisfed.org/series/DCOILWTICO",
            "Gasolina regular EE. UU. (FRED, EIA)": "https://fred.stlouisfed.org/series/GASREGW",
            "Tasa de cambio de referencia (Banco Central RD)": URL_FX,
            "Estaciones de expendio registradas (MICM)": URL_STATIONS,
        },
    }


def check_fresh(data: dict) -> list[str]:
    """Problemas que harían que la página muestre precios viejos o incompletos."""
    problems = []
    week = data["semana_actual"]
    if not week:
        return ["no se pudo leer ningún aviso semanal"]
    today_rd = (datetime.now(timezone.utc) - timedelta(hours=4)).date()  # RD es UTC-4 todo el año
    if date.fromisoformat(week["end"]) < today_rd:
        problems.append(f"el último aviso venció el {week['end']}; el MICM no lo ha publicado o cambió el formato")
    missing = [k for k in [*PDF_ROWS.values(), "glp"] if k not in week["fuels"]]
    if missing:
        problems.append(f"el aviso del {week['start']} no trae: {', '.join(missing)}")
    return problems


if __name__ == "__main__":
    data = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    print(f"Listo: {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")
    # Falla a propósito para que GitHub Actions no publique y mande el correo de error
    problems = check_fresh(data)
    for p in problems:
        print(f"ERROR: {p}", file=sys.stderr)
    sys.exit(1 if problems else 0)
