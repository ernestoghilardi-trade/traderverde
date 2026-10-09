#!/usr/bin/env python3
"""
SCANNER LÍNEAS VERDES v5 — ~1.000 activos (S&P 500 + MidCap 400 + Latam + ETFs + Criptos)
Señal: réplica del CM_Stoch_MTF (14, 3, 3, niveles 80/20) en diario.

Qué agrega el reporte:
  1. Plan de operación por señal: entrada, stop (ATR + mínimo reciente), objetivo y cantidad
     según el % de capital que arriesgás por operación.
  2. Semáforo de mercado: SPY, QQQ, VIX y BTC.
  3. Tasa de acierto histórica de la señal verde en cada activo (a 10 días).
  4. Alerta de balances (earnings) en los próximos 7 días.
  5. Confirmación por volumen del día de la señal.
  6. Sector, capitalización y si la empresa gana o pierde plata.
  7. Tu cartera de IBKR: alertas de venta solo sobre lo que tenés.
  8. Historial: guarda cada señal y mide cómo le fue a 5, 10 y 20 días.
  + Puntaje 0–100 que combina todo lo anterior para ordenar las señales.

Uso: ▶ en VS Code, doble clic en Escanear_senales.bat, o desde Telegram (bot_telegram_scanner.py).
Opciones: --solo cripto|acciones  --dias 3  --tickers IBRX NU  --ver IBRX  --actualizar
Ajustes (capital, riesgo, IBKR): archivo ajustes_scanner.json (se crea solo la primera vez).
"""
import argparse
import asyncio
import datetime as dt
import importlib.util
import io
import json
import math
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def asegurar_librerias():
    faltan = [p for p in ("yfinance", "pandas", "numpy", "lxml")
              if importlib.util.find_spec(p) is None]
    if faltan:
        print(f"Instalando librerías: {', '.join(faltan)} (solo la primera vez)...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", *faltan])
        print("Listo.\n")


asegurar_librerias()

import numpy as np
import pandas as pd
import yfinance as yf

# =============================== PARÁMETROS ===============================
LARGO, SUAV_K, SUAV_D = 14, 3, 3
SOBRECOMPRA, SOBREVENTA = 80, 20
MARGEN_CERCA = 3                 # puntos entre %K y %D para "POR ENTRAR"
MIN_VOLUMEN_USD = 2_000_000      # filtra acciones con poco volumen diario
LOTE = 100                       # tickers por descarga
HORIZONTE_ACIERTO = 10           # días para medir si una verde "acertó"
DIAS_BALANCE = 7                 # alerta si presenta resultados dentro de estos días

CARPETA = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(CARPETA, "universo_acciones.json")
AJUSTES_ARCH = os.path.join(CARPETA, "ajustes_scanner.json")
HIST_ARCH = os.path.join(CARPETA, "senales_historial.csv")

AJUSTES_DEFAULT = {
    "capital": 10000,          # USD — se usa si no se conecta a IBKR
    "riesgo_pct": 1.0,         # % del capital que aceptás perder por operación
    "rr": 2.0,                 # objetivo = riesgo x rr (2 = gano el doble de lo que arriesgo)
    "max_posicion_pct": 20.0,  # tope de una sola posición sobre el capital
    "acciones_fraccionadas": True,  # IBKR permite fracciones en acciones de EE.UU.
    "comision_min_usd": 1.0,   # IBKR Pro Fijo: USD 0,005/acción, mínimo 1, máximo 1% del monto
    "comision_max_pct": 1.0,
    "usar_ibkr": False,        # True = leer capital y posiciones de TWS (solo en tu PC)
    "ibkr_host": "127.0.0.1",
    "ibkr_puerto": 7496,       # TWS real 7496 · TWS paper 7497 · Gateway real 4001 · paper 4002
    "ibkr_client_id": 77,      # distinto al del robot
    "cartera_manual": [],      # [{"ticker": "F", "cantidad": 1, "costo": 12.24}, ...]
}

MERCADO = ["SPY", "QQQ", "^VIX", "BTC-USD"]

ETFS = ["SPY", "QQQ", "IWM", "DIA", "EWZ", "ARGT", "GLD", "SLV", "USO", "TLT",
        "XLE", "XLF", "XLK", "XLV", "SMH", "ARKK", "EEM", "ECH", "IBIT"]

EXTRAS = [
    "NU", "MELI", "GGAL", "YPF", "BMA", "PAM", "VIST", "TGS", "CEPU", "LOMA", "SUPV",
    "CRESY", "IRS", "EDN", "TEO", "BBAR", "DESP", "GLOB", "SQM", "BCH", "BSAC", "LTM",
    "PBR", "VALE", "ITUB", "BBD", "ABEV", "STNE", "PAGS", "XP", "CIB", "AMX", "BSBR",
    "IBRX", "SOFI", "RIVN", "NIO", "HOOD", "MARA", "RIOT", "RKLB", "SHOP", "BABA",
    "TSM", "ASML", "ARM", "SPOT", "SNOW", "NET", "CRWD", "DKNG", "LCID", "PATH",
] + ETFS

CRIPTOS = [f"{c}-USD" for c in [
    "BTC", "ETH", "SOL", "XRP", "BNB", "ADA", "DOGE", "AVAX", "LINK", "DOT",
    "TRX", "LTC", "BCH", "XLM", "ATOM", "NEAR", "ETC", "HBAR", "ALGO", "XMR",
    "FIL", "ICP", "VET", "AAVE", "XTZ", "EOS", "SAND", "MANA", "AXS", "THETA",
    "SHIB", "TON11419", "SUI20947", "APT21794", "PEPE24478", "UNI7083",
    "ARB11841", "OP", "INJ", "RNDR", "KAS", "SEI", "TIA22861", "MKR", "CRV",
]]

FALLBACK_SP = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD", "NFLX", "AVGO",
    "JPM", "BAC", "V", "MA", "KO", "PEP", "WMT", "DIS", "XOM", "CVX", "UNH", "JNJ",
    "PFE", "INTC", "CSCO", "ORCL", "CRM", "ADBE", "PYPL", "UBER", "PLTR", "COIN",
]


# =============================== AJUSTES ===============================
def cargar_ajustes():
    aj = dict(AJUSTES_DEFAULT)
    if os.path.exists(AJUSTES_ARCH):
        try:
            with open(AJUSTES_ARCH, encoding="utf-8") as f:
                aj.update(json.load(f))
        except Exception:
            pass
    else:
        guardar_ajustes(aj)
    return aj


def guardar_ajustes(aj):
    with open(AJUSTES_ARCH, "w", encoding="utf-8") as f:
        json.dump(aj, f, indent=2, ensure_ascii=False)


# =============================== UNIVERSO ===============================
def tickers_wikipedia(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8")
    for tabla in pd.read_html(io.StringIO(html)):
        for col in ("Symbol", "Ticker", "Ticker symbol"):
            if col in tabla.columns:
                return [str(s).strip().replace(".", "-") for s in tabla[col] if str(s) != "nan"]
    return []


def leer_cache():
    try:
        with open(CACHE, encoding="utf-8") as f:
            c = json.load(f)
        if isinstance(c, list):
            return 0, c
        return c.get("ts", 0), c.get("lista", [])
    except Exception:
        return 0, []


def universo_acciones(forzar=False):
    ts, lista_cache = leer_cache()
    if not forzar and lista_cache and time.time() - ts < 7 * 86400:
        return lista_cache
    print("Bajando lista de acciones del S&P 500 y S&P MidCap 400...")
    lista = []
    for url in ("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies"):
        try:
            lista += tickers_wikipedia(url)
        except Exception as ex:
            print(f"  ⚠ No se pudo bajar {url.rsplit('/', 1)[-1]}: {ex}")
    if lista:
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "lista": lista}, f)
        return lista
    if lista_cache:
        return lista_cache
    print("  Uso lista reducida de respaldo.")
    return FALLBACK_SP


def tipo_de(t):
    if t.endswith("-USD"):
        return "Cripto"
    return "ETF" if t in ETFS else "Acción"


def nombre_visible(t):
    if t.endswith("-USD"):
        return "".join(ch for ch in t[:-4] if not ch.isdigit())
    return t


def resolver_ticker(texto):
    t = texto.upper().strip()
    for c in CRIPTOS:
        if t in (c, c[:-4], nombre_visible(c)):
            return c
    return t


# =============================== INDICADORES ===============================
def estocastico(df):
    ll = df["Low"].rolling(LARGO).min()
    hh = df["High"].rolling(LARGO).max()
    k = (100 * (df["Close"] - ll) / (hh - ll)).rolling(SUAV_K).mean()
    d = k.rolling(SUAV_D).mean()
    e = pd.DataFrame({"Close": df["Close"], "Low": df["Low"], "K": k, "D": d})
    e["ema50"] = df["Close"].ewm(span=50, adjust=False).mean()
    e["ema200"] = df["Close"].ewm(span=200, adjust=False).mean()
    tr = pd.concat([df["High"] - df["Low"],
                    (df["High"] - df["Close"].shift()).abs(),
                    (df["Low"] - df["Close"].shift()).abs()], axis=1).max(axis=1)
    e["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    vol_med = df["Volume"].rolling(20).mean()
    e["volrel"] = (df["Volume"] / vol_med).replace([np.inf, -np.inf], np.nan)
    arriba = (e["K"] > e["D"]) & (e["K"].shift(1) <= e["D"].shift(1))
    abajo = (e["K"] < e["D"]) & (e["K"].shift(1) >= e["D"].shift(1))
    e["verde"] = arriba & (e["K"].shift(1) < SOBREVENTA)
    e["roja"] = abajo & (e["K"].shift(1) > SOBRECOMPRA)
    return e


def acierto_historico(e):
    """Verdes pasadas del activo: cuántas subieron a HORIZONTE_ACIERTO días y retorno promedio."""
    pos = np.where(e["verde"].values)[0]
    pos = pos[pos + HORIZONTE_ACIERTO < len(e)]
    if not len(pos):
        return 0, 0, None
    c = e["Close"].values
    rets = c[pos + HORIZONTE_ACIERTO] / c[pos] - 1
    return int(len(rets)), int((rets > 0).sum()), float(rets.mean() * 100)


def comision(cant, precio, aj):
    """Comisión estimada de UNA orden (modelo IBKR Pro Fijo)."""
    if cant <= 0:
        return 0.0
    monto = cant * precio
    return min(max(aj["comision_min_usd"], 0.005 * cant), monto * aj["comision_max_pct"] / 100)


def plan_operacion(e, aj, capital, es_cripto):
    u = e.iloc[-1]
    c, a = float(u["Close"]), float(u["atr"])
    if not a or math.isnan(a) or c <= 0:
        return None
    minimo = float(e["Low"].tail(10).min())
    stop = max(minimo - 0.25 * a, c - 3 * a)   # no más lejos que 3 ATR
    stop = min(stop, c - 1 * a)                  # no más cerca que 1 ATR
    if stop <= 0:
        return None
    riesgo_u = c - stop
    objetivo = c + aj["rr"] * riesgo_u
    cant = (capital * aj["riesgo_pct"] / 100) / riesgo_u
    cant = min(cant, capital * aj["max_posicion_pct"] / 100 / c)
    if es_cripto:
        cant = round(cant, 4)
    elif aj.get("acciones_fraccionadas"):
        cant = math.floor(cant * 1000) / 1000
    else:
        cant = math.floor(cant)
    com = 0.0 if es_cripto else 2 * comision(cant, c, aj)   # ida y vuelta
    inversion = cant * c
    return {"stop": stop, "objetivo": objetivo, "cantidad": cant,
            "riesgo_usd": cant * riesgo_u, "inversion": inversion,
            "stop_pct": (stop / c - 1) * 100, "obj_pct": (objetivo / c - 1) * 100,
            "comision": com, "comision_pct": com / inversion * 100 if inversion else None}


def tendencia_de(u):
    if u["Close"] > u["ema50"] > u["ema200"]:
        return "alcista"
    if u["Close"] < u["ema50"] < u["ema200"]:
        return "bajista"
    return "mixta"


# =============================== SEMÁFORO ===============================
def semaforo(datos):
    out = {"indices": [], "estado": "amarillo", "texto": "", "cripto": "mixta"}
    puntos = 0.0
    for t in ("SPY", "QQQ"):
        df = datos.get(t)
        if df is None or len(df) < 60:
            continue
        e = estocastico(df)
        u = e.iloc[-1]
        tend = tendencia_de(u)
        roja = bool(e["roja"].iloc[-5:].any())
        puntos += {"alcista": 1, "bajista": -1}.get(tend, 0) - (0.5 if roja else 0)
        cambio = (u["Close"] / e["Close"].iloc[-2] - 1) * 100
        out["indices"].append({"nombre": t, "precio": round(float(u["Close"]), 2),
                               "cambio": round(float(cambio), 2), "tendencia": tend,
                               "K": round(float(u["K"]), 1), "roja": roja})
    vix = datos.get("^VIX")
    if vix is not None and len(vix):
        v = float(vix["Close"].iloc[-1])
        nivel = "calma" if v < 15 else "normal" if v < 20 else "nervios" if v < 25 else "miedo"
        puntos += 1 if v < 20 else 0 if v < 25 else -1 if v < 30 else -2
        out["indices"].append({"nombre": "VIX", "precio": round(v, 2), "nivel": nivel})
    btc = datos.get("BTC-USD")
    if btc is not None and len(btc) >= 60:
        e = estocastico(btc)
        u = e.iloc[-1]
        out["cripto"] = tendencia_de(u)
        out["indices"].append({"nombre": "BTC", "precio": round(float(u["Close"]), 0),
                               "cambio": round(float((u["Close"] / e["Close"].iloc[-2] - 1) * 100), 2),
                               "tendencia": out["cripto"], "K": round(float(u["K"]), 1), "roja": False})
    if puntos >= 2:
        out["estado"], out["texto"] = "verde", "Mercado favorable: las señales de compra tienen viento a favor."
    elif puntos >= 0:
        out["estado"], out["texto"] = "amarillo", "Mercado mixto: entrá solo en las señales de puntaje alto."
    else:
        out["estado"], out["texto"] = "rojo", "Mercado desfavorable: las verdes fallan más seguido. Tamaño chico o esperar."
    return out


# =============================== FUNDAMENTALES / BALANCES ===============================
def info_empresa(t):
    out = {"sector": "", "mcap": None, "gana": None, "balance": None}
    tk = yf.Ticker(t)
    try:
        cal = tk.calendar
        fechas = None
        if isinstance(cal, dict):
            fechas = cal.get("Earnings Date")
        elif hasattr(cal, "loc") and "Earnings Date" in getattr(cal, "index", []):
            fechas = list(cal.loc["Earnings Date"].values)
        if fechas is not None:
            f = fechas[0] if isinstance(fechas, (list, tuple)) else fechas
            out["balance"] = pd.Timestamp(f).date()
    except Exception:
        pass
    try:
        inf = tk.info or {}
        out["sector"] = inf.get("sector") or inf.get("industry") or ""
        out["mcap"] = inf.get("marketCap")
        eps, margen = inf.get("trailingEps"), inf.get("profitMargins")
        if eps is not None:
            out["gana"] = eps > 0
        elif margen is not None:
            out["gana"] = margen > 0
    except Exception:
        pass
    return out


def info_masiva(tickers):
    if not tickers:
        return {}
    print(f"  Buscando balances y datos de {len(tickers)} empresas...")
    with ThreadPoolExecutor(max_workers=8) as ex:
        return dict(zip(tickers, ex.map(info_empresa, tickers)))


def fmt_mcap(v):
    if not v:
        return ""
    return f"{v / 1e12:.1f} T" if v >= 1e12 else f"{v / 1e9:.1f} B" if v >= 1e9 else f"{v / 1e6:.0f} M"


# =============================== IBKR ===============================
def leer_ibkr(aj):
    """Devuelve ({'capital', 'posiciones'}, None) o (None, motivo)."""
    try:
        asyncio.set_event_loop(asyncio.new_event_loop())
        try:
            from ib_insync import IB
        except ImportError:
            from ib_async import IB
    except Exception:
        return None, "falta ib_insync (pip install ib_insync)"
    ib = IB()
    try:
        ib.connect(aj["ibkr_host"], int(aj["ibkr_puerto"]), clientId=int(aj["ibkr_client_id"]),
                   readonly=True, timeout=10)
        posiciones = []
        for p in ib.positions():
            c = p.contract
            if not p.position or c.secType not in ("STK", "CRYPTO"):
                continue
            t = f"{c.symbol}-USD" if c.secType == "CRYPTO" else c.symbol
            posiciones.append({"ticker": t, "cantidad": float(p.position), "costo": float(p.avgCost)})
        capital = None
        for v in ib.accountSummary():
            if v.tag == "NetLiquidation" and v.currency in ("USD", "BASE"):
                capital = float(v.value)
        return {"capital": capital, "posiciones": posiciones}, None
    except Exception as ex:
        return None, f"no se pudo conectar a IBKR ({ex.__class__.__name__}). ¿TWS abierto y API habilitada?"
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass


def analizar_cartera(posiciones, datos):
    filas = []
    for p in posiciones:
        t = p["ticker"]
        df = datos.get(t)
        if df is None or len(df) < 30:
            filas.append({"Ticker": nombre_visible(t), "Yahoo": t, "Tipo": tipo_de(t),
                          "nivel": "gris", "Estado": "Sin datos de precio",
                          "Cantidad": p.get("cantidad"), "Costo": p.get("costo"),
                          "Precio": None, "PnL": None, "K": None, "D": None})
            continue
        e = estocastico(df)
        u = e.iloc[-1]
        precio = float(u["Close"])
        pnl = (precio / p["costo"] - 1) * 100 if p.get("costo") else None
        roja = bool(e["roja"].iloc[-3:].any())
        if roja:
            nivel, estado = "rojo", "Señal roja: zona de venta / tomar ganancia"
        elif precio < u["ema50"] and pnl is not None and pnl < 0:
            nivel, estado = "rojo", "Perdió la EMA50 y está en pérdida: revisar stop"
        elif u["K"] > SOBRECOMPRA:
            nivel, estado = "amarillo", "Sobrecompra: vigilar, puede venir la roja"
        elif precio < u["ema50"]:
            nivel, estado = "amarillo", "Debajo de la EMA50: tendencia débil"
        else:
            nivel, estado = "verde", "Sin alertas"
        filas.append({"Ticker": nombre_visible(t), "Yahoo": t, "Tipo": tipo_de(t),
                      "Cantidad": p.get("cantidad"), "Costo": p.get("costo"),
                      "Precio": round(precio, 4 if precio < 1 else 2),
                      "PnL": round(pnl, 1) if pnl is not None else None,
                      "K": round(float(u["K"]), 1), "D": round(float(u["D"]), 1),
                      "nivel": nivel, "Estado": estado})
    orden = {"rojo": 0, "amarillo": 1, "verde": 2, "gris": 3}
    return sorted(filas, key=lambda f: orden[f["nivel"]])


# =============================== ANÁLISIS ===============================
def analizar(t, df, dias, aj, capital):
    df = df.dropna(subset=["Close"])
    if len(df) < 60:
        return None
    tipo = tipo_de(t)
    if tipo != "Cripto" and (df["Close"] * df["Volume"]).tail(20).mean() < MIN_VOLUMEN_USD:
        return None
    e = estocastico(df)
    u, prev = e.iloc[-1], e.iloc[-2]

    ult = e["verde"].iloc[-dias:].values
    volrel = float(u["volrel"]) if not pd.isna(u["volrel"]) else None
    if ult.any():
        i = int(np.where(ult)[0][-1])
        hace = dias - 1 - i
        estado, detalle = "verde", "hoy" if hace == 0 else f"hace {hace} día(s)"
        v = e["volrel"].iloc[-dias + i]
        volrel = float(v) if not pd.isna(v) else None
        fecha_senal = e.index[-dias + i]
    elif (u["K"] < SOBREVENTA and u["D"] < SOBREVENTA and u["K"] < u["D"]
          and u["K"] > prev["K"] and (u["D"] - u["K"]) <= MARGEN_CERCA):
        estado, detalle, fecha_senal = "entrar", f"faltan {u['D'] - u['K']:.1f} pts", None
    elif e["roja"].iloc[-dias:].any():
        estado, detalle, fecha_senal = "roja", "zona de venta", None
    else:
        return None

    n, w, prom = acierto_historico(e)
    fila = {
        "Ticker": nombre_visible(t), "Yahoo": t, "Tipo": tipo,
        "Cierre": round(float(u["Close"]), 4 if u["Close"] < 1 else 2),
        "K": round(float(u["K"]), 1), "D": round(float(u["D"]), 1),
        "Tendencia": tendencia_de(u), "SobreEMA50": bool(u["Close"] > u["ema50"]),
        "e": estado, "Detalle": detalle, "VolRel": round(volrel, 2) if volrel else None,
        "AcN": n, "AcW": w, "AcProm": round(prom, 1) if prom is not None else None,
        "FechaSenal": fecha_senal.strftime("%Y-%m-%d") if fecha_senal is not None else None,
        "Sector": "", "Mcap": "", "Gana": None, "Balance": None, "BalanceDias": None,
        "Stop": None, "Objetivo": None, "Cantidad": None, "RiesgoUSD": None,
        "Inversion": None, "StopPct": None, "ObjPct": None, "Puntaje": None,
        "Comision": None, "ComisionPct": None,
    }
    if estado in ("verde", "entrar"):
        p = plan_operacion(e, aj, capital, tipo == "Cripto")
        if p:
            dec = 4 if u["Close"] < 1 else 2
            fila.update({"Stop": round(p["stop"], dec), "Objetivo": round(p["objetivo"], dec),
                         "Cantidad": p["cantidad"], "RiesgoUSD": round(p["riesgo_usd"], 1),
                         "Inversion": round(p["inversion"], 0), "StopPct": round(p["stop_pct"], 1),
                         "ObjPct": round(p["obj_pct"], 1), "Comision": round(p["comision"], 2),
                         "ComisionPct": round(p["comision_pct"], 2) if p["comision_pct"] else None})
    return fila


def puntaje(f, mercado):
    p = 0.0
    p += 20 if f["SobreEMA50"] else 0
    p += 10 if f["Tendencia"] == "alcista" else 0
    v = f["VolRel"] or 0
    p += 15 if v >= 1.5 else 7 if v >= 1.0 else 0
    p += 25 * f["AcW"] / f["AcN"] if f["AcN"] >= 3 else 10
    clima = mercado["cripto"] if f["Tipo"] == "Cripto" else mercado["estado"]
    p += {"verde": 15, "alcista": 15, "amarillo": 7, "mixta": 7}.get(clima, 0)
    if f["BalanceDias"] is not None and 0 <= f["BalanceDias"] <= DIAS_BALANCE:
        p -= 15
    else:
        p += 5
    p += 10 if f["Gana"] else 5 if f["Gana"] is None else 0
    return int(max(0, min(100, round(p))))


# =============================== HISTORIAL ===============================
HIST_COLS = ["fecha_senal", "ticker", "nombre", "tipo", "precio", "stop", "objetivo",
             "puntaje", "r5", "r10", "r20", "resultado"]


def actualizar_historial(filas, datos):
    if os.path.exists(HIST_ARCH):
        hist = pd.read_csv(HIST_ARCH, dtype={"fecha_senal": str, "ticker": str})
    else:
        hist = pd.DataFrame(columns=HIST_COLS)
    hist = hist.astype({"r5": float, "r10": float, "r20": float}, errors="ignore")
    claves = set(zip(hist["fecha_senal"].astype(str), hist["ticker"].astype(str)))
    nuevas = []
    for f in filas:
        if f["e"] != "verde" or not f["FechaSenal"] or (f["FechaSenal"], f["Yahoo"]) in claves:
            continue
        precio = f["Cierre"]
        df = datos.get(f["Yahoo"])
        if df is not None:
            ts = pd.Timestamp(f["FechaSenal"])
            if ts in df.index:
                precio = float(df.loc[ts, "Close"])
        nuevas.append({"fecha_senal": f["FechaSenal"], "ticker": f["Yahoo"], "nombre": f["Ticker"],
                       "tipo": f["Tipo"], "precio": precio, "stop": f["Stop"],
                       "objetivo": f["Objetivo"], "puntaje": f["Puntaje"],
                       "r5": np.nan, "r10": np.nan, "r20": np.nan, "resultado": "abierta"})
    if nuevas:
        nuevas = pd.DataFrame(nuevas, columns=HIST_COLS)
        hist = nuevas if not len(hist) else pd.concat([hist, nuevas], ignore_index=True)

    for i, h in hist.iterrows():
        df = datos.get(str(h["ticker"]))
        if df is None:
            continue
        ts = pd.Timestamp(str(h["fecha_senal"]))
        if ts not in df.index:
            continue
        pos = int(df.index.get_loc(ts))
        c = df["Close"].values
        base = float(h["precio"])
        for n in (5, 10, 20):
            if pos + n < len(c):
                hist.at[i, f"r{n}"] = round((c[pos + n] / base - 1) * 100, 2)
        if h["resultado"] == "abierta" and not pd.isna(h["stop"]) and not pd.isna(h["objetivo"]):
            for j in range(pos + 1, len(df)):
                if df["Low"].iloc[j] <= float(h["stop"]):
                    hist.at[i, "resultado"] = "stop"
                    break
                if df["High"].iloc[j] >= float(h["objetivo"]):
                    hist.at[i, "resultado"] = "objetivo"
                    break
    hist = hist.reindex(columns=HIST_COLS)
    hist.to_csv(HIST_ARCH, index=False)
    return hist


def resumen_historial(hist=None):
    if hist is None:
        if not os.path.exists(HIST_ARCH):
            return {"total": 0}
        hist = pd.read_csv(HIST_ARCH, dtype={"fecha_senal": str})
    if not len(hist):
        return {"total": 0}
    r10 = pd.to_numeric(hist["r10"], errors="coerce").dropna()
    res = hist["resultado"].value_counts()
    alto = hist[pd.to_numeric(hist["puntaje"], errors="coerce") >= 70]
    r10_alto = pd.to_numeric(alto["r10"], errors="coerce").dropna()
    ultimas = hist.sort_values("fecha_senal", ascending=False).head(15)
    return {
        "total": int(len(hist)),
        "medidas10": int(len(r10)),
        "positivas10": int((r10 > 0).sum()),
        "prom10": round(float(r10.mean()), 2) if len(r10) else None,
        "medidas10_alto": int(len(r10_alto)),
        "positivas10_alto": int((r10_alto > 0).sum()),
        "objetivo": int(res.get("objetivo", 0)), "stop": int(res.get("stop", 0)),
        "abiertas": int(res.get("abierta", 0)),
        "ultimas": json.loads(ultimas.to_json(orient="records")),
    }


# =============================== DESCARGA ===============================
def extraer(data, t, unico):
    if isinstance(data.columns, pd.MultiIndex):
        if t in data.columns.get_level_values(0):
            df = data[t]
        elif unico:
            df = data.copy()
            df.columns = df.columns.get_level_values(-1)
        else:
            return None
    else:
        df = data
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
    return df.copy() if len(df) else None


def descargar(tickers, periodo="2y"):
    res, total = {}, len(tickers)
    for i in range(0, total, LOTE):
        grupo = tickers[i:i + LOTE]
        print(f"  Descargando precios {min(i + LOTE, total)}/{total}...", end="\r", flush=True)
        try:
            data = yf.download(grupo, period=periodo, interval="1d", group_by="ticker",
                               auto_adjust=True, progress=False, threads=True)
        except Exception as ex:
            print(f"\n  ⚠ Falló un lote: {ex}")
            continue
        for t in grupo:
            try:
                df = extraer(data, t, len(grupo) == 1)
                if df is not None:
                    idx = pd.to_datetime(df.index)
                    df.index = (idx.tz_localize(None) if idx.tz is not None else idx).normalize()
                    res[t] = df
            except Exception:
                pass
    print(" " * 50, end="\r")
    return res


# =============================== ESCANEO COMPLETO ===============================
def leer_cartera(aj):
    ib, err = (leer_ibkr(aj) if aj["usar_ibkr"] else (None, "IBKR desactivado en ajustes"))
    if ib:
        return ib, ib["posiciones"], None
    manual = []
    for item in aj["cartera_manual"]:
        if isinstance(item, dict):
            manual.append({"ticker": resolver_ticker(item["ticker"]),
                           "cantidad": item.get("cantidad"), "costo": item.get("costo")})
        else:
            manual.append({"ticker": resolver_ticker(item), "cantidad": None, "costo": None})
    return None, manual, err


def escanear(solo=None, tickers=None, dias=2, actualizar=False):
    """Corre todo. Devuelve un dict con señales, mercado, cartera, historial y metadatos."""
    inicio = time.time()
    aj = cargar_ajustes()
    ib, posiciones, ib_error = leer_cartera(aj)
    if ib and ib["capital"]:
        capital, fuente = ib["capital"], "IBKR"
    else:
        capital, fuente = float(aj["capital"]), "ajustes"

    if tickers:
        universo = [resolver_ticker(t) for t in tickers]
    else:
        universo = []
        if solo != "cripto":
            universo += universo_acciones(actualizar) + EXTRAS
        if solo != "acciones":
            universo += CRIPTOS
    universo = list(dict.fromkeys(universo))
    a_bajar = list(dict.fromkeys(universo + MERCADO + [p["ticker"] for p in posiciones]))

    print(f"Escaneando {len(universo)} activos (capital {fuente}: USD {capital:,.0f})...")
    datos = descargar(a_bajar)
    mercado = semaforo(datos)

    filas = []
    for t in universo:
        df = datos.get(t)
        if df is None:
            continue
        try:
            f = analizar(t, df, dias, aj, capital)
            if f:
                filas.append(f)
        except Exception:
            pass

    candidatas = [f["Yahoo"] for f in filas if f["e"] in ("verde", "entrar") and f["Tipo"] == "Acción"]
    infos = info_masiva(candidatas)
    hoy = dt.date.today()
    for f in filas:
        inf = infos.get(f["Yahoo"])
        if inf:
            f["Sector"], f["Mcap"], f["Gana"] = inf["sector"], fmt_mcap(inf["mcap"]), inf["gana"]
            if inf["balance"]:
                f["Balance"] = inf["balance"].strftime("%d/%m")
                f["BalanceDias"] = (inf["balance"] - hoy).days
        if f["e"] in ("verde", "entrar"):
            f["Puntaje"] = puntaje(f, mercado)

    orden = {"verde": 0, "entrar": 1, "roja": 2}
    filas.sort(key=lambda f: (orden[f["e"]], -(f["Puntaje"] or 0), f["K"]))

    hist = actualizar_historial(filas, datos)
    cartera = analizar_cartera(posiciones, datos)

    nombre = os.path.join(CARPETA, f"senales_{hoy:%Y-%m-%d}.csv")
    if filas:
        pd.DataFrame(filas).drop(columns=["Yahoo", "SobreEMA50", "FechaSenal", "e"], errors="ignore") \
            .to_csv(nombre, index=False, encoding="utf-8-sig", sep=";", decimal=",")

    return {
        "filas": filas, "total": len(universo), "csv": nombre, "mercado": mercado,
        "cartera": cartera, "cartera_fuente": "IBKR" if ib else ("manual" if aj["cartera_manual"] else None),
        "ibkr_error": ib_error, "capital": capital, "capital_fuente": fuente, "ajustes": aj,
        "historial": resumen_historial(hist), "segundos": time.time() - inicio,
        "fecha": dt.datetime.now().strftime("%d/%m/%Y %H:%M"),
    }


def solo_cartera():
    """Chequeo rápido de la cartera sin escanear todo el mercado."""
    aj = cargar_ajustes()
    ib, posiciones, err = leer_cartera(aj)
    datos = descargar(list(dict.fromkeys([p["ticker"] for p in posiciones] + MERCADO)), periodo="1y")
    return analizar_cartera(posiciones, datos), semaforo(datos), err


def ficha(texto):
    """Detalle de un solo activo (para /ver)."""
    t = resolver_ticker(texto)
    datos = descargar(list(dict.fromkeys([t] + MERCADO)))
    df = datos.get(t)
    if df is None or len(df) < 60:
        return None
    aj = cargar_ajustes()
    e = estocastico(df)
    u = e.iloc[-1]
    n, w, prom = acierto_historico(e)
    plan = plan_operacion(e, aj, float(aj["capital"]), t.endswith("-USD"))
    verdes, rojas = e.index[e["verde"]], e.index[e["roja"]]
    inf = info_empresa(t) if tipo_de(t) == "Acción" else {}
    return {"ticker": t, "nombre": nombre_visible(t), "tipo": tipo_de(t), "precio": float(u["Close"]),
            "K": float(u["K"]), "D": float(u["D"]), "subiendo": bool(u["K"] > e["K"].iloc[-2]),
            "tendencia": tendencia_de(u), "acierto": (n, w, prom), "plan": plan,
            "ult_verde": verdes[-1].strftime("%d/%m/%Y") if len(verdes) else None,
            "ult_roja": rojas[-1].strftime("%d/%m/%Y") if len(rojas) else None,
            "info": inf, "mercado": semaforo(datos)}


# =============================== REPORTE HTML ===============================
PLANTILLA = r"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Señales __FECHA__</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Sans+Condensed:wght@600;700&display=swap" rel="stylesheet">
<style>
:root{--fondo:#E9EDF1;--hoja:#FFF;--tinta:#1B2430;--gris:#5B6B7F;--linea:#D3DAE2;
--verde:#17864B;--verde-z:#DDF1E5;--ambar:#B57A06;--ambar-z:#FBEFD4;--rojo:#B93A27;--rojo-z:#F8E0DB}
*{box-sizing:border-box}
body{margin:0;background:var(--fondo);color:var(--tinta);font:15px/1.45 "IBM Plex Sans","Segoe UI",system-ui,sans-serif}
main{max-width:1240px;margin:0 auto;padding:28px 20px 64px}
h1{font:700 30px/1.1 "IBM Plex Sans Condensed","Segoe UI",sans-serif;margin:0}
h2{font:700 21px/1.2 "IBM Plex Sans Condensed","Segoe UI",sans-serif;margin:36px 0 12px}
.sub{color:var(--gris);margin:6px 0 22px}
.clima{background:var(--hoja);border-radius:10px;padding:16px 18px;display:flex;gap:22px;align-items:center;flex-wrap:wrap;border-left:8px solid var(--ambar)}
.clima.verde{border-left-color:var(--verde)}.clima.rojo{border-left-color:var(--rojo)}
.clima .msg{flex:1 1 260px;font-weight:500}
.ind{min-width:110px}.ind b{display:block;font-size:13px;color:var(--gris);font-weight:500}
.ind span{font-variant-numeric:tabular-nums;font-weight:600}
.up{color:var(--verde)}.down{color:var(--rojo)}
.cont{display:flex;gap:12px;flex-wrap:wrap;margin:18px 0}
.cont button{flex:1 1 200px;text-align:left;border:2px solid transparent;background:var(--hoja);border-radius:10px;padding:12px 16px;cursor:pointer;font:inherit;color:inherit;opacity:.55}
.cont button.on{opacity:1}
.cont button[data-e=verde].on{border-color:var(--verde)}.cont button[data-e=entrar].on{border-color:var(--ambar)}.cont button[data-e=roja].on{border-color:var(--rojo)}
.cont b{display:block;font:700 36px/1 "IBM Plex Sans Condensed","Segoe UI",sans-serif}
.cont button[data-e=verde] b{color:var(--verde)}.cont button[data-e=entrar] b{color:var(--ambar)}.cont button[data-e=roja] b{color:var(--rojo)}
.filtros{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:12px}
.chip{border:1px solid var(--linea);background:var(--hoja);border-radius:999px;padding:6px 14px;cursor:pointer;font:inherit;font-size:14px;color:var(--gris)}
.chip.on{background:var(--tinta);color:#fff;border-color:var(--tinta)}
input{margin-left:auto;border:1px solid var(--linea);border-radius:8px;padding:8px 12px;font:inherit;min-width:170px}
button:focus-visible,input:focus-visible,a:focus-visible{outline:2px solid #2F6FDB;outline-offset:2px}
.tabla{background:var(--hoja);border-radius:10px;overflow-x:auto}
table{width:100%;border-collapse:collapse;min-width:1150px}
th{font-weight:500;font-size:13px;color:var(--gris);text-align:left;padding:11px 14px;border-bottom:1px solid var(--linea);white-space:nowrap}
td{padding:11px 14px;border-bottom:1px solid var(--linea);vertical-align:top}
tr:last-child td{border-bottom:0}
.num{font-variant-numeric:tabular-nums}
a.tk{font-weight:600;color:var(--tinta);text-decoration:none;font-size:16px}
a.tk:hover{text-decoration:underline}
.meta{color:var(--gris);font-size:13px}
.est{white-space:nowrap;display:inline-block;border-radius:6px;padding:2px 9px;font-weight:500;font-size:13px}
.est.verde{background:var(--verde-z);color:var(--verde)}.est.entrar{background:var(--ambar-z);color:var(--ambar)}.est.roja{background:var(--rojo-z);color:var(--rojo)}
.pts{font:700 28px/1 "IBM Plex Sans Condensed","Segoe UI",sans-serif}
.pts.a{color:var(--verde)}.pts.m{color:var(--ambar)}.pts.b{color:var(--gris)}
.osc{position:relative;width:150px;height:20px;border-radius:4px;margin-top:2px;
background:linear-gradient(90deg,var(--verde-z) 0 20%,#F2F4F7 20% 80%,var(--rojo-z) 80% 100%)}
.osc i{position:absolute;top:50%;width:12px;height:12px;margin:-6px 0 0 -6px;border-radius:50%}
.osc .k{background:var(--tinta)}.osc .d{border:2px solid var(--rojo);background:#fff}
.plan{display:grid;grid-template-columns:auto auto;gap:1px 10px;font-size:13px;white-space:nowrap}
.plan span:nth-child(odd){color:var(--gris)}
.plan span.down{color:var(--rojo)}
.tag{display:inline-block;font-size:12px;border-radius:5px;padding:1px 7px;margin:3px 4px 0 0;background:#EEF1F4;color:var(--gris)}
.tag.mal{background:var(--rojo-z);color:var(--rojo)}.tag.bien{background:var(--verde-z);color:var(--verde)}
.vacio{padding:40px 16px;text-align:center;color:var(--gris)}
.cajas{display:flex;gap:12px;flex-wrap:wrap}
.caja{background:var(--hoja);border-radius:10px;padding:14px 18px;flex:1 1 200px}
.caja b{display:block;font:700 28px/1.1 "IBM Plex Sans Condensed","Segoe UI",sans-serif}
.punto{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px}
.punto.rojo{background:var(--rojo)}.punto.amarillo{background:var(--ambar)}.punto.verde{background:var(--verde)}.punto.gris{background:#AAB4C0}
.nota{color:var(--gris);font-size:13px;margin-top:10px;max-width:80ch}
</style></head><body><main>
<h1>Señales del estocástico</h1>
<p class="sub">__FECHA__ — __TOTAL__ activos en __MIN__ min. Capital para el cálculo: USD __CAPITAL__ (__FUENTE__), riesgo __RIESGO__% por operación.</p>

<div id="clima" class="clima"></div>

<div class="cont">
 <button data-e="verde" class="on"><b id="c-verde">0</b>Señal verde: cruce de compra en sobreventa</button>
 <button data-e="entrar" class="on"><b id="c-entrar">0</b>Por entrar: a punto de cruzar debajo de 20</button>
 <button data-e="roja"><b id="c-roja">0</b>Señal roja: cruce de venta en sobrecompra</button>
</div>
<div class="filtros">
 <button class="chip on" data-t="">Todo</button>
 <button class="chip" data-t="Acción">Acciones</button>
 <button class="chip" data-t="ETF">ETFs</button>
 <button class="chip" data-t="Cripto">Criptos</button>
 <button class="chip" data-f="alto">Puntaje 70+</button>
 <button class="chip" data-f="balance">Ocultar con balance cerca</button>
 <input id="q" placeholder="Buscar ticker" aria-label="Buscar ticker">
</div>
<div class="tabla"><table>
<thead><tr><th>Activo</th><th>Puntaje</th><th>Señal</th><th>Estocástico</th><th>Plan de operación</th><th>Acierto histórico</th><th>Alertas</th></tr></thead>
<tbody id="filas"></tbody></table></div>
<p class="nota">El puntaje suma tendencia (hasta 30), volumen del día de la señal (15), acierto histórico (25), clima de mercado (15), sin balance cerca (5) y empresa rentable (10). El stop sale de la volatilidad (ATR) y el mínimo reciente; el objetivo es __RR__ veces lo que arriesgás.</p>

<h2>Tu cartera</h2>
<div id="cartera"></div>

<h2>Cómo vienen funcionando las señales</h2>
<div id="hist"></div>
</main>
<script>
const R=__DATA__;
const D=R.filas, est=new Set(["verde","entrar"]);let tipo="",alto=false,sinBal=false,q="";
const nom={verde:"Señal verde",entrar:"Por entrar",roja:"Señal roja"};
const fx=(v,d=2)=>v==null?"–":Number(v).toLocaleString("es-AR",{minimumFractionDigits:d,maximumFractionDigits:d});
const dec=v=>v!=null&&Math.abs(v)<1?4:2;
const url=(t,tp)=>"https://www.tradingview.com/chart/?symbol="+(tp==="Cripto"?"BINANCE:"+t+"USDT":t.replace("-","."));
const pos=v=>Math.max(0,Math.min(100,v))+"%";
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

(function(){const m=R.mercado,c=document.getElementById("clima");c.classList.add(m.estado);
 let h=`<div class="msg">${esc(m.texto)}</div>`;
 for(const i of m.indices){
  if(i.nombre==="VIX")h+=`<div class="ind"><b>VIX (miedo)</b><span>${fx(i.precio)}</span> <span class="meta">${i.nivel}</span></div>`;
  else h+=`<div class="ind"><b>${i.nombre}</b><span>${fx(i.precio,i.nombre==="BTC"?0:2)}</span> <span class="${i.cambio>=0?"up":"down"}">${i.cambio>=0?"+":""}${fx(i.cambio)}%</span><div class="meta">tendencia ${i.tendencia}${i.roja?", roja reciente":""}</div></div>`;}
 c.innerHTML=h;})();

for(const e of ["verde","entrar","roja"])document.getElementById("c-"+e).textContent=D.filter(r=>r.e===e).length;
const balCerca=r=>r.BalanceDias!=null&&r.BalanceDias>=0&&r.BalanceDias<=7;

function alertas(r){let t=[];
 if(balCerca(r))t.push(`<span class="tag mal">Balance en ${r.BalanceDias} d (${r.Balance})</span>`);
 else if(r.Balance)t.push(`<span class="tag">Balance ${r.Balance}</span>`);
 if(r.VolRel!=null)t.push(`<span class="tag ${r.VolRel>=1.5?"bien":""}">Volumen ${fx(r.VolRel,1)}x</span>`);
 if(r.Gana===false)t.push(`<span class="tag mal">Empresa con pérdidas</span>`);
 if(r.Gana===true)t.push(`<span class="tag bien">Rentable</span>`);
 return t.join("")||'<span class="meta">–</span>';}

function plan(r){if(r.Stop==null)return '<span class="meta">–</span>';
 return `<div class="plan num"><span>Entrada</span><span>${fx(r.Cierre,dec(r.Cierre))}</span>
 <span>Stop</span><span class="down">${fx(r.Stop,dec(r.Stop))} (${fx(r.StopPct,1)}%)</span>
 <span>Objetivo</span><span class="up">${fx(r.Objetivo,dec(r.Objetivo))} (+${fx(r.ObjPct,1)}%)</span>
 <span>Cantidad</span><span>${r.Tipo==="Cripto"?fx(r.Cantidad,4):fx(r.Cantidad,Number.isInteger(r.Cantidad)?0:3)} (USD ${fx(r.Inversion,0)})</span>
 <span>Arriesgás</span><span>USD ${fx(r.RiesgoUSD,1)}</span>
 ${r.Comision?`<span>Comisión</span><span class="${r.ComisionPct>=1?"down":""}">~USD ${fx(r.Comision)} ida y vuelta (${fx(r.ComisionPct,1)}%)</span>`:""}</div>`;}

function pintar(){
 const f=D.filter(r=>est.has(r.e)&&(!tipo||r.Tipo===tipo)&&(!alto||(r.Puntaje||0)>=70)&&(!sinBal||!balCerca(r))&&r.Ticker.includes(q));
 const tb=document.getElementById("filas");
 if(!f.length){tb.innerHTML='<tr><td colspan="7" class="vacio">No hay activos con estos filtros. Activá otro estado arriba o quitá filtros.</td></tr>';return;}
 tb.innerHTML=f.map(r=>{const p=r.Puntaje;const cls=p==null?"b":p>=70?"a":p>=50?"m":"b";
 const ac=r.AcN?`<span class="num">${r.AcW} de ${r.AcN} subieron</span><div class="meta num">promedio ${r.AcProm>=0?"+":""}${fx(r.AcProm,1)}% a 10 días</div>`:'<span class="meta">Sin verdes previas</span>';
 return `<tr>
 <td><a class="tk" href="${url(r.Ticker,r.Tipo)}" target="_blank" rel="noopener">${esc(r.Ticker)}</a>
   <div class="meta">${esc(r.Sector||r.Tipo)}${r.Mcap?", "+r.Mcap:""}</div><div class="meta">tendencia ${r.Tendencia}</div></td>
 <td><span class="pts ${cls}">${p??"–"}</span></td>
 <td><span class="est ${r.e}">${nom[r.e]}</span><div class="meta">${esc(r.Detalle)}</div></td>
 <td><div class="osc"><i class="d" style="left:${pos(r.D)}"></i><i class="k" style="left:${pos(r.K)}"></i></div><div class="meta num">%K ${fx(r.K,1)}  %D ${fx(r.D,1)}</div></td>
 <td>${plan(r)}</td><td>${ac}</td><td>${alertas(r)}</td></tr>`}).join("");}

document.querySelectorAll(".cont button").forEach(b=>b.onclick=()=>{const e=b.dataset.e;est.has(e)?est.delete(e):est.add(e);b.classList.toggle("on");pintar();});
document.querySelectorAll(".chip[data-t]").forEach(b=>b.onclick=()=>{tipo=b.dataset.t;document.querySelectorAll(".chip[data-t]").forEach(x=>x.classList.toggle("on",x===b));pintar();});
document.querySelector('.chip[data-f=alto]').onclick=e=>{alto=!alto;e.target.classList.toggle("on");pintar();};
document.querySelector('.chip[data-f=balance]').onclick=e=>{sinBal=!sinBal;e.target.classList.toggle("on");pintar();};
document.getElementById("q").oninput=e=>{q=e.target.value.toUpperCase().trim();pintar();};
pintar();

(function(){const c=document.getElementById("cartera"),C=R.cartera;
 if(!C.length){c.innerHTML=`<div class="caja meta">${R.ibkr_error?"No se leyó la cartera: "+esc(R.ibkr_error)+". ":""}Para ver alertas sobre tus posiciones, abrí TWS con la API habilitada o cargá "cartera_manual" en ajustes_scanner.json.</div>`;return;}
 c.innerHTML=`<div class="tabla"><table style="min-width:760px"><thead><tr><th>Activo</th><th>Estado</th><th>Cantidad</th><th>Costo</th><th>Precio</th><th>Resultado</th><th>%K / %D</th></tr></thead><tbody>`+
 C.map(p=>`<tr><td><a class="tk" href="${url(p.Ticker,p.Tipo)}" target="_blank" rel="noopener">${esc(p.Ticker)}</a></td>
 <td><span class="punto ${p.nivel}"></span>${esc(p.Estado)}</td><td class="num">${p.Cantidad??"–"}</td>
 <td class="num">${fx(p.Costo,dec(p.Costo))}</td><td class="num">${fx(p.Precio,dec(p.Precio))}</td>
 <td class="num ${p.PnL>=0?"up":"down"}">${p.PnL!=null?(p.PnL>=0?"+":"")+fx(p.PnL,1)+"%":"–"}</td>
 <td class="num">${p.K!=null?fx(p.K,0)+" / "+fx(p.D,0):"–"}</td></tr>`).join("")+`</tbody></table></div>`;})();

(function(){const h=R.historial,c=document.getElementById("hist");
 if(!h.total){c.innerHTML='<div class="caja meta">Todavía no hay señales guardadas. Cada verde queda registrada y a los 10 días vas a ver acá si funcionó.</div>';return;}
 const pct=(a,b)=>b?Math.round(100*a/b)+"%":"–";
 c.innerHTML=`<div class="cajas">
 <div class="caja"><b>${h.total}</b>señales registradas</div>
 <div class="caja"><b>${pct(h.positivas10,h.medidas10)}</b>subieron a 10 días (${h.positivas10} de ${h.medidas10})</div>
 <div class="caja"><b>${h.prom10==null?"–":(h.prom10>=0?"+":"")+fx(h.prom10,1)+"%"}</b>retorno promedio a 10 días</div>
 <div class="caja"><b>${pct(h.positivas10_alto,h.medidas10_alto)}</b>acierto con puntaje 70+ (${h.medidas10_alto} medidas)</div>
 <div class="caja"><b>${h.objetivo} / ${h.stop}</b>tocaron objetivo / stop (${h.abiertas} abiertas)</div></div>
 <div class="tabla" style="margin-top:12px"><table style="min-width:700px"><thead><tr><th>Fecha</th><th>Activo</th><th>Puntaje</th><th>Precio</th><th>5 días</th><th>10 días</th><th>20 días</th><th>Resultado</th></tr></thead><tbody>`+
 h.ultimas.map(s=>{const r=v=>v==null?"–":`<span class="${v>=0?"up":"down"}">${v>=0?"+":""}${fx(v,1)}%</span>`;
 return `<tr><td class="num">${esc(s.fecha_senal)}</td><td>${esc(s.nombre)}</td><td>${s.puntaje??"–"}</td><td class="num">${fx(s.precio,dec(s.precio))}</td><td class="num">${r(s.r5)}</td><td class="num">${r(s.r10)}</td><td class="num">${r(s.r20)}</td><td>${esc(s.resultado)}</td></tr>`}).join("")+`</tbody></table></div>`;})();
</script></body></html>"""


def generar_html(r, abrir=True):
    datos = {k: r[k] for k in ("filas", "mercado", "cartera", "historial", "ibkr_error")}
    js = json.dumps(datos, ensure_ascii=False, default=str).replace("</", "<\\/").replace("NaN", "null")
    html = (PLANTILLA.replace("__DATA__", js)
            .replace("__FECHA__", r["fecha"]).replace("__TOTAL__", str(r["total"]))
            .replace("__MIN__", f"{r['segundos'] / 60:.1f}")
            .replace("__CAPITAL__", f"{r['capital']:,.0f}".replace(",", "."))
            .replace("__FUENTE__", "desde IBKR" if r["capital_fuente"] == "IBKR" else "de ajustes")
            .replace("__RIESGO__", str(r["ajustes"]["riesgo_pct"]))
            .replace("__RR__", str(r["ajustes"]["rr"])))
    ruta = Path(CARPETA) / "reporte_senales.html"
    ruta.write_text(html, encoding="utf-8")
    if abrir:
        webbrowser.open(ruta.as_uri())
    return ruta


# =============================== PRINCIPAL ===============================
def main():
    ap = argparse.ArgumentParser(description="Scanner Líneas Verdes v4")
    ap.add_argument("--tickers", nargs="+")
    ap.add_argument("--solo", choices=["cripto", "acciones"])
    ap.add_argument("--dias", type=int, default=2)
    ap.add_argument("--ver")
    ap.add_argument("--actualizar", action="store_true")
    a = ap.parse_args()

    if a.ver:
        f = ficha(a.ver)
        print(json.dumps(f, indent=2, ensure_ascii=False, default=str) if f else "Sin datos.")
        return

    r = escanear(a.solo, a.tickers, a.dias, a.actualizar)
    n = {e: sum(1 for f in r["filas"] if f["e"] == e) for e in ("verde", "entrar", "roja")}
    print(f"\nMercado: {r['mercado']['texto']}")
    print(f"🟢 {n['verde']} verdes | 🟡 {n['entrar']} por entrar | 🔴 {n['roja']} rojas "
          f"({r['segundos']:.0f} seg)")
    if r["ibkr_error"] and r["ajustes"]["usar_ibkr"]:
        print(f"IBKR: {r['ibkr_error']}. Uso el capital de ajustes_scanner.json.")
    ruta = generar_html(r)
    print(f"Reporte abierto en el navegador: {ruta}")


if __name__ == "__main__":
    main()
