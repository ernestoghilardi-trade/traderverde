#!/usr/bin/env python3
"""
Punto de entrada para correr el scanner en la nube (GitHub Actions) y mandar todo a Telegram.

  python scanner_nube.py --accion auto        lun–vie escaneo completo, sáb–dom criptos
  python scanner_nube.py --accion escanear | acciones | cripto | cartera | historial
  python scanner_nube.py --accion ver --ticker IBRX
"""
import argparse
import datetime as dt
import sys

import bot_telegram_scanner as bot
import scanner_lineas_verdes as sc

bot.USAR_TECLADO = False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--accion", default="auto",
                    choices=["auto", "escanear", "acciones", "cripto", "ver", "cartera", "historial"])
    ap.add_argument("--ticker", default="")
    a = ap.parse_args()

    if "PEGAR_ACA" in bot.TOKEN or "PEGAR_ACA" in str(bot.CHAT_ID):
        sys.exit("Faltan los secrets TELEGRAM_TOKEN y TELEGRAM_CHAT_ID en GitHub.")

    try:
        if a.accion == "auto":
            finde = dt.date.today().weekday() >= 5
            bot.correr_y_reportar("cripto" if finde else None,
                                  "Reporte diario: criptos" if finde else "Reporte diario")
        elif a.accion == "escanear":
            bot.correr_y_reportar(None, "Escaneo completo")
        elif a.accion == "acciones":
            bot.correr_y_reportar("acciones", "Acciones y ETFs")
        elif a.accion == "cripto":
            bot.correr_y_reportar("cripto", "Criptos")
        elif a.accion == "ver":
            if not a.ticker.strip():
                bot.enviar("Para 'ver' completá el campo ticker (ej: IBRX).")
            else:
                for t in a.ticker.replace(",", " ").split():
                    bot.ver_ticker(t)
        elif a.accion == "cartera":
            bot.ver_cartera()
        elif a.accion == "historial":
            bot.enviar(bot.texto_historial(sc.resumen_historial(), detalle=True))
    except Exception as ex:
        bot.enviar(f"⚠️ Falló el scanner en la nube: {bot.e(ex)}")
        raise


if __name__ == "__main__":
    main()
