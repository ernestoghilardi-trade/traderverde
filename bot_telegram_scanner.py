#!/usr/bin/env python3
"""
BOT DE TELEGRAM — Scanner Líneas Verdes v4

Reporte diario automático (lun–vie completo, sáb–dom solo criptos) con:
  semáforo de mercado, señales con plan de operación (entrada/stop/objetivo/cantidad),
  puntaje, acierto histórico, alertas de balance, tu cartera y rendimiento del scanner.

Comandos (o usá los botones):
  /escanear          escaneo completo (~1.000 activos)
  /acciones          solo acciones y ETFs
  /cripto            solo criptos
  /ver IBRX          ficha completa de un activo con plan de operación
  /cartera           chequeo rápido de tus posiciones (sin escanear todo)
  /historial         cómo vienen funcionando las señales
  /capital 15000     capital para calcular cantidades (si no conecta a IBKR)
  /riesgo 1          % del capital que arriesgás por operación
  /hora 18:30        hora del reporte diario
  /pausa             pausar / reanudar el reporte diario
  /estado            ver configuración

Va en la misma carpeta que scanner_lineas_verdes.py. Configurá TOKEN y CHAT_ID abajo.
"""
import datetime as dt
import html
import json
import os
import time
import traceback

import scanner_lineas_verdes as sc
import requests

# ======================= CONFIGURACIÓN =======================
# En la nube se toman de los "secrets" de GitHub; en tu PC, pegalos acá.
TOKEN = os.environ.get("TELEGRAM_TOKEN") or "PEGAR_ACA_EL_TOKEN_DEL_BOT"
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID") or "PEGAR_ACA_TU_CHAT_ID"
USAR_TECLADO = True                # en la nube se apaga (no hay bot escuchando los botones)
HORA_REPORTE_DEFAULT = "18:30"     # hora de tu PC
MAX_VERDES = 10                    # cuántas verdes detallar en el mensaje
MAX_POR_ENTRAR = 10
# =============================================================

API = f"https://api.telegram.org/bot{TOKEN}"
ESTADO_ARCH = os.path.join(sc.CARPETA, "bot_estado.json")

TECLADO = {"keyboard": [
    [{"text": "📊 Escanear todo"}, {"text": "📈 Acciones"}],
    [{"text": "🪙 Cripto"}, {"text": "💼 Cartera"}],
    [{"text": "📜 Historial"}, {"text": "❓ Ayuda"}],
], "resize_keyboard": True}
BOTONES = {"📊 Escanear todo": "/escanear", "📈 Acciones": "/acciones", "🪙 Cripto": "/cripto",
           "💼 Cartera": "/cartera", "📜 Historial": "/historial", "❓ Ayuda": "/ayuda"}

AYUDA = ("<b>Comandos</b>\n"
         "/escanear: escaneo completo\n"
         "/acciones: solo acciones y ETFs\n"
         "/cripto: solo criptos\n"
         "/ver IBRX: ficha de un activo con plan de operación\n"
         "/cartera: chequeo rápido de tus posiciones\n"
         "/historial: cómo vienen funcionando las señales\n"
         "/capital 15000: capital para calcular cantidades\n"
         "/riesgo 1: % del capital que arriesgás por operación\n"
         "/hora 18:30: hora del reporte diario\n"
         "/pausa: pausar o reanudar el reporte diario\n"
         "/estado: ver configuración")


# ---------------- Utilidades ----------------
def n(v, d=2):
    """Número en formato argentino."""
    if v is None:
        return "–"
    s = f"{v:,.{d}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def cant_txt(c, tipo):
    if c is None:
        return "–"
    if tipo == "Cripto":
        return n(c, 4)
    return n(c, 0) if float(c).is_integer() else n(c, 3)


def dec(v):
    return 4 if v is not None and abs(v) < 1 else 2


def e(s):
    return html.escape(str(s or ""))


def link(ticker, tipo):
    sim = f"BINANCE:{ticker}USDT" if tipo == "Cripto" else ticker.replace("-", ".")
    return f'<a href="https://www.tradingview.com/chart/?symbol={sim}">{e(ticker)}</a>'


# ---------------- Estado del bot ----------------
def cargar_estado():
    base = {"hora": HORA_REPORTE_DEFAULT, "ultimo_reporte": "", "pausado": False, "offset": 0}
    try:
        with open(ESTADO_ARCH, encoding="utf-8") as f:
            base.update(json.load(f))
    except Exception:
        pass
    return base


def guardar_estado(est):
    with open(ESTADO_ARCH, "w", encoding="utf-8") as f:
        json.dump(est, f)


# ---------------- Telegram ----------------
def enviar(texto, teclado=True):
    """Manda texto; si supera el límite de Telegram lo parte por líneas."""
    bloques, actual = [], ""
    for linea in texto.split("\n"):
        if len(actual) + len(linea) + 1 > 3900:
            bloques.append(actual)
            actual = ""
        actual += linea + "\n"
    bloques.append(actual)
    for i, b in enumerate(bloques):
        datos = {"chat_id": CHAT_ID, "text": b.strip() or ".", "parse_mode": "HTML",
                 "disable_web_page_preview": True}
        if teclado and USAR_TECLADO and i == len(bloques) - 1:
            datos["reply_markup"] = json.dumps(TECLADO)
        try:
            r = requests.post(f"{API}/sendMessage", data=datos, timeout=30).json()
            if not r.get("ok"):
                print("Telegram rechazó el mensaje:", r.get("description"))
        except Exception as ex:
            print("Error enviando mensaje:", ex)


def enviar_archivo(ruta, titulo=""):
    try:
        with open(ruta, "rb") as f:
            requests.post(f"{API}/sendDocument", data={"chat_id": CHAT_ID, "caption": titulo},
                          files={"document": (os.path.basename(ruta), f)}, timeout=120)
    except Exception as ex:
        print("Error enviando archivo:", ex)


def leer_mensajes(offset):
    try:
        r = requests.get(f"{API}/getUpdates", params={"offset": offset, "timeout": 25}, timeout=35).json()
    except Exception:
        time.sleep(5)
        return [], offset
    if not r.get("ok"):
        if r.get("error_code") == 409:
            print("⚠ Otro programa está leyendo este mismo bot. Usá un bot distinto (ver instrucciones).")
        elif r.get("error_code") == 401:
            print("⚠ TOKEN inválido. Revisalo al principio del archivo.")
        time.sleep(10)
        return [], offset
    msgs = []
    for u in r.get("result", []):
        offset = u["update_id"] + 1
        m = u.get("message") or {}
        if str(m.get("chat", {}).get("id")) == str(CHAT_ID) and m.get("text"):
            msgs.append(m["text"].strip())
    return msgs, offset


# ---------------- Bloques del reporte ----------------
def texto_mercado(m):
    icono = {"verde": "🟢", "amarillo": "🟡", "rojo": "🔴"}[m["estado"]]
    partes = []
    for i in m["indices"]:
        if i["nombre"] == "VIX":
            partes.append(f"VIX {n(i['precio'])} ({i['nivel']})")
        else:
            signo = "+" if i["cambio"] >= 0 else ""
            partes.append(f"{i['nombre']} {n(i['precio'], 0 if i['nombre'] == 'BTC' else 2)} "
                          f"({signo}{n(i['cambio'])}%) {i['tendencia']}")
    return f"{icono} <b>{e(m['texto'])}</b>\n" + "\n".join(partes)


def texto_verde(f):
    tend = " ✅" if f["SobreEMA50"] else ""
    sector = f" · {e(f['Sector'])}" if f["Sector"] else f" · {f['Tipo']}"
    lineas = [f"<b>{f['Puntaje']} pts</b> · {link(f['Ticker'], f['Tipo'])}{sector}{tend}  <i>{e(f['Detalle'])}</i>"]
    if f["Stop"] is not None:
        cant = cant_txt(f["Cantidad"], f["Tipo"])
        lineas.append(f"Entrada {n(f['Cierre'], dec(f['Cierre']))} · Stop {n(f['Stop'], dec(f['Stop']))} "
                      f"({n(f['StopPct'], 1)}%) · Obj {n(f['Objetivo'], dec(f['Objetivo']))} (+{n(f['ObjPct'], 1)}%)")
        com = (f" · comisión ~USD {n(f['Comision'])} ({n(f['ComisionPct'], 1)}%)"
               if f.get("Comision") else "")
        lineas.append(f"Comprar {cant} (USD {n(f['Inversion'], 0)}) · arriesgás USD {n(f['RiesgoUSD'], 1)}{com}")
    extra = []
    if f["AcN"]:
        extra.append(f"Acierto {f['AcW']}/{f['AcN']} ({'+' if f['AcProm'] >= 0 else ''}{n(f['AcProm'], 1)}%)")
    if f["VolRel"]:
        extra.append(f"Vol {n(f['VolRel'], 1)}x")
    if f["BalanceDias"] is not None and 0 <= f["BalanceDias"] <= sc.DIAS_BALANCE:
        extra.append(f"⚠️ Balance en {f['BalanceDias']} d")
    if f["Gana"] is False:
        extra.append("⚠️ Empresa con pérdidas")
    if extra:
        lineas.append(" · ".join(extra))
    return "\n".join(lineas)


def texto_entrar(f):
    tend = " ✅" if f["SobreEMA50"] else ""
    bal = " ⚠️ balance" if f["BalanceDias"] is not None and 0 <= f["BalanceDias"] <= sc.DIAS_BALANCE else ""
    return (f"<b>{f['Puntaje']}</b> · {link(f['Ticker'], f['Tipo'])}{tend} · "
            f"K {n(f['K'], 0)} / D {n(f['D'], 0)} · {e(f['Detalle'])}{bal}")


def texto_cartera(cartera, error=None, solo_alertas=False):
    if not cartera:
        motivo = f"No se leyó la cartera: {e(error)}." if error else "No hay posiciones cargadas."
        return f"💼 <b>Tu cartera</b>\n{motivo}"
    icono = {"rojo": "🔴", "amarillo": "🟡", "verde": "🟢", "gris": "⚪️"}
    filas = [p for p in cartera if p["nivel"] in ("rojo", "amarillo")] if solo_alertas else cartera
    lineas = ["💼 <b>Tu cartera</b>"]
    for p in filas:
        pnl = f" ({'+' if p['PnL'] >= 0 else ''}{n(p['PnL'], 1)}%)" if p.get("PnL") is not None else ""
        lineas.append(f"{icono[p['nivel']]} {link(p['Ticker'], p['Tipo'])}{pnl}: {e(p['Estado'])}")
    ok = len(cartera) - len(filas)
    if solo_alertas:
        lineas.append(f"🟢 {ok} posición(es) sin alertas" if ok else "")
        if not filas:
            lineas[0] += ": todo en orden"
    return "\n".join(l for l in lineas if l)


def texto_historial(h, detalle=False):
    if not h.get("total"):
        return "📜 <b>Rendimiento del scanner</b>\nTodavía no hay señales registradas. Cada verde se guarda y se mide a 5, 10 y 20 días."
    pct = lambda a, b: f"{round(100 * a / b)}%" if b else "–"
    lineas = ["📜 <b>Rendimiento del scanner</b>",
              f"{h['total']} señales registradas",
              f"A 10 días subieron {pct(h['positivas10'], h['medidas10'])} ({h['positivas10']} de {h['medidas10']})"
              + (f", promedio {'+' if h['prom10'] >= 0 else ''}{n(h['prom10'], 1)}%" if h["prom10"] is not None else ""),
              f"Con puntaje 70+: {pct(h['positivas10_alto'], h['medidas10_alto'])} ({h['medidas10_alto']} medidas)",
              f"Tocaron objetivo {h['objetivo']} · stop {h['stop']} · abiertas {h['abiertas']}"]
    if detalle and h.get("ultimas"):
        lineas.append("\n<b>Últimas señales</b>")
        for s in h["ultimas"][:12]:
            r10 = s.get("r10")
            r10 = f"{'+' if r10 >= 0 else ''}{n(r10, 1)}%" if r10 is not None else "–"
            lineas.append(f"{s['fecha_senal'][5:]} · {e(s['nombre'])} · {s.get('puntaje') or '–'} pts · "
                          f"10d {r10} · {s['resultado']}")
    return "\n".join(lineas)


# ---------------- Acciones ----------------
def correr_y_reportar(solo=None, titulo="Reporte"):
    etiqueta = {"cripto": "criptos", "acciones": "acciones y ETFs"}.get(solo, "~1.000 activos")
    enviar(f"⏳ Escaneando {etiqueta}… tarda unos minutos.", teclado=False)
    r = sc.escanear(solo=solo)
    ruta = sc.generar_html(r, abrir=False)

    verdes = [f for f in r["filas"] if f["e"] == "verde"]
    entrar = [f for f in r["filas"] if f["e"] == "entrar"]
    rojas = [f for f in r["filas"] if f["e"] == "roja"]

    cab = [f"📋 <b>{titulo} — {dt.datetime.now():%d/%m %H:%M}</b>",
           texto_mercado(r["mercado"]), "",
           f"{r['total']} activos en {n(r['segundos'] / 60, 1)} min · capital USD {n(r['capital'], 0)} "
           f"· riesgo {n(r['ajustes']['riesgo_pct'], 1)}%",
           f"🟢 {len(verdes)} verdes   🟡 {len(entrar)} por entrar   🔴 {len(rojas)} rojas"]
    enviar("\n".join(cab), teclado=False)

    if verdes:
        bloque = ["🟢 <b>SEÑALES VERDES</b> (ordenadas por puntaje)\n"]
        bloque += [texto_verde(f) + "\n" for f in verdes[:MAX_VERDES]]
        if len(verdes) > MAX_VERDES:
            bloque.append(f"<i>… y {len(verdes) - MAX_VERDES} más en el reporte adjunto</i>")
        enviar("\n".join(bloque), teclado=False)
    if entrar:
        bloque = ["🟡 <b>POR ENTRAR</b> (vigilar mañana)"]
        bloque += [texto_entrar(f) for f in entrar[:MAX_POR_ENTRAR]]
        if len(entrar) > MAX_POR_ENTRAR:
            bloque.append(f"<i>… y {len(entrar) - MAX_POR_ENTRAR} más en el reporte adjunto</i>")
        enviar("\n".join(bloque), teclado=False)
    if not verdes and not entrar:
        enviar("Hoy no hay señales de compra.", teclado=False)

    if r["cartera"]:
        enviar(texto_cartera(r["cartera"], r["ibkr_error"], solo_alertas=True), teclado=False)
    enviar(texto_historial(r["historial"]) +
           "\n\n✅ = precio sobre EMA50. Tocá el ticker para ver el gráfico.")
    enviar_archivo(str(ruta), "Reporte completo: abrilo en el navegador")


def ver_ticker(texto):
    enviar(f"🔎 Analizando {e(texto.upper())}…", teclado=False)
    f = sc.ficha(texto)
    if not f:
        enviar(f"No encontré datos para <b>{e(texto.upper())}</b>. Revisá el ticker (cripto: BTC, ETH…).")
        return
    zona = "sobreventa 🟢" if f["K"] < 20 else "sobrecompra 🔴" if f["K"] > 80 else "zona media"
    n_ac, w_ac, prom = f["acierto"]
    p = f["plan"]
    inf = f["info"] or {}
    lineas = [f"<b>{link(f['nombre'], f['tipo'])}</b>  {n(f['precio'], dec(f['precio']))}"
              + (f" · {e(inf.get('sector'))}" if inf.get("sector") else ""),
              f"%K {n(f['K'], 1)} / %D {n(f['D'], 1)}: {zona}, {'subiendo ↗️' if f['subiendo'] else 'bajando ↘️'}",
              f"Tendencia {f['tendencia']}",
              f"Última verde {f['ult_verde'] or '–'} · última roja {f['ult_roja'] or '–'}"]
    if n_ac:
        lineas.append(f"Acierto de la verde: {w_ac} de {n_ac} subieron a 10 días "
                      f"(promedio {'+' if prom >= 0 else ''}{n(prom, 1)}%)")
    if inf.get("balance"):
        dias = (inf["balance"] - dt.date.today()).days
        lineas.append(f"{'⚠️ ' if 0 <= dias <= sc.DIAS_BALANCE else ''}Próximo balance: "
                      f"{inf['balance']:%d/%m} ({dias} días)")
    if inf.get("gana") is not None:
        lineas.append("Empresa rentable" if inf["gana"] else "⚠️ Empresa con pérdidas")
    if p:
        cant = cant_txt(p["cantidad"], f["tipo"])
        lineas += ["", "<b>Plan si entrás hoy</b>",
                   f"Stop {n(p['stop'], dec(p['stop']))} ({n(p['stop_pct'], 1)}%) · "
                   f"Objetivo {n(p['objetivo'], dec(p['objetivo']))} (+{n(p['obj_pct'], 1)}%)",
                   f"Comprar {cant} (USD {n(p['inversion'], 0)}) · arriesgás USD {n(p['riesgo_usd'], 1)}"]
        if p.get("comision"):
            lineas.append(f"Comisión estimada ~USD {n(p['comision'])} ida y vuelta "
                          f"({n(p['comision_pct'], 1)}% del monto)")
    enviar("\n".join(lineas))


def ver_cartera():
    enviar("⏳ Revisando tu cartera…", teclado=False)
    cartera, mercado, err = sc.solo_cartera()
    enviar(texto_mercado(mercado) + "\n\n" + texto_cartera(cartera, err))


def procesar(texto, est):
    texto = BOTONES.get(texto, texto)
    cmd, _, arg = texto.partition(" ")
    cmd, arg = cmd.lower().split("@")[0], arg.strip()
    aj = sc.cargar_ajustes()

    if cmd in ("/start", "/ayuda"):
        enviar(AYUDA)
    elif cmd == "/escanear":
        correr_y_reportar(None, "Escaneo completo")
    elif cmd == "/acciones":
        correr_y_reportar("acciones", "Acciones y ETFs")
    elif cmd == "/cripto":
        correr_y_reportar("cripto", "Criptos")
    elif cmd == "/ver":
        ver_ticker(arg) if arg else enviar("Escribí el ticker, por ejemplo: /ver IBRX")
    elif cmd == "/cartera":
        ver_cartera()
    elif cmd == "/historial":
        enviar(texto_historial(sc.resumen_historial(), detalle=True))
    elif cmd == "/capital":
        try:
            aj["capital"] = float(arg.replace(".", "").replace(",", "."))
            sc.guardar_ajustes(aj)
            enviar(f"💵 Capital: USD {n(aj['capital'], 0)}. "
                   "Si IBKR está conectado, se usa el valor real de la cuenta.")
        except ValueError:
            enviar("Formato: /capital 15000")
    elif cmd == "/riesgo":
        try:
            v = float(arg.replace(",", "."))
            if not 0.1 <= v <= 5:
                raise ValueError
            aj["riesgo_pct"] = v
            sc.guardar_ajustes(aj)
            enviar(f"🎯 Riesgo por operación: {n(v, 1)}% del capital.")
        except ValueError:
            enviar("Formato: /riesgo 1 (entre 0,1 y 5)")
    elif cmd == "/hora":
        try:
            est["hora"] = dt.datetime.strptime(arg, "%H:%M").strftime("%H:%M")
            guardar_estado(est)
            enviar(f"⏰ Reporte diario a las {est['hora']}.")
        except ValueError:
            enviar("Formato: /hora 18:30")
    elif cmd == "/pausa":
        est["pausado"] = not est["pausado"]
        guardar_estado(est)
        enviar("⏸ Reporte diario pausado." if est["pausado"] else "▶️ Reporte diario reanudado.")
    elif cmd == "/estado":
        enviar(f"⏰ Reporte diario: {est['hora']} ({'pausado' if est['pausado'] else 'activo'})\n"
               f"Último enviado: {est['ultimo_reporte'] or 'ninguno todavía'}\n"
               f"Lun–Vie completo · Sáb–Dom solo criptos\n"
               f"💵 Capital de ajustes: USD {n(aj['capital'], 0)} · IBKR {'activado' if aj['usar_ibkr'] else 'desactivado'} "
               f"(puerto {aj['ibkr_puerto']})\n"
               f"🎯 Riesgo {n(aj['riesgo_pct'], 1)}% · objetivo {n(aj['rr'], 1)}x el riesgo")
    else:
        enviar("No entendí.\n\n" + AYUDA)


def toca_reporte(est):
    if est["pausado"]:
        return False
    ahora = dt.datetime.now()
    return est["ultimo_reporte"] != ahora.strftime("%Y-%m-%d") and ahora.strftime("%H:%M") >= est["hora"]


# ---------------- Bucle principal ----------------
def main():
    if "PEGAR_ACA" in TOKEN or "PEGAR_ACA" in str(CHAT_ID):
        raise SystemExit("Falta configurar TOKEN y CHAT_ID al principio de bot_telegram_scanner.py")
    est = cargar_estado()
    if dt.datetime.now().strftime("%H:%M") >= est["hora"] and not est["ultimo_reporte"]:
        est["ultimo_reporte"] = dt.date.today().isoformat()   # primer arranque tarde: no dispara de golpe
    guardar_estado(est)

    print(f"Bot iniciado. Reporte diario a las {est['hora']}. Cerrá esta ventana para detenerlo.")
    enviar(f"🤖 Scanner conectado. Reporte diario a las {est['hora']}.\nUsá los botones o /ayuda.")

    while True:
        try:
            msgs, est["offset"] = leer_mensajes(est["offset"])
            guardar_estado(est)
            for m in msgs:
                print(f"[{dt.datetime.now():%H:%M}] {m}")
                procesar(m, est)
            if toca_reporte(est):
                finde = dt.date.today().weekday() >= 5
                est["ultimo_reporte"] = dt.date.today().isoformat()
                guardar_estado(est)
                correr_y_reportar("cripto" if finde else None,
                                  "Reporte diario: criptos" if finde else "Reporte diario")
        except KeyboardInterrupt:
            print("Bot detenido.")
            break
        except Exception as ex:
            traceback.print_exc()
            enviar(f"⚠️ Error en el scanner: {e(ex)}", teclado=False)
            time.sleep(30)


if __name__ == "__main__":
    main()
