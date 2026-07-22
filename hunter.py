#!/usr/bin/env python3
"""
Flight Hunter v2 - Monitor de vuelos baratos (Google Flights via faster-flights)

Mejoras sobre v1:
  - Historial POR RUTA (no mezcla NRT-HKG con PEK-LAX en la misma linea base)
  - Reintentos con backoff + pausa entre consultas (evita rate-limit de Google)
  - Deteccion de fallo masivo (si >50% de consultas fallan, avisa)
  - Anti-spam de alertas (cooldown configurable)
  - Mediana en vez de promedio (robusta ante precios atipicos)
  - Guarda el TOP-3 de opciones, no solo la mas barata
  - Zona horaria de Panama en logs y timestamps
  - Validacion de config antes de gastar consultas
"""

import json
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from itertools import product
from pathlib import Path
from statistics import median

import requests

try:
    from fast_flights import (get_flights, create_query, FlightQuery,
                              Passengers, ShoppingOptions)
    HAS_SHOPPING = True
except ImportError:
    from fast_flights import get_flights, create_query, FlightQuery, Passengers
    HAS_SHOPPING = False

BASE = Path(__file__).parent
CONFIG = BASE / "config.json"
HISTORY = BASE / "history.json"

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")

PTY_TZ = timezone(timedelta(hours=-5))

STATS = {"ok": 0, "fail": 0}


def ahora():
    return datetime.now(PTY_TZ)


def log(msg):
    print(f"[{ahora():%Y-%m-%d %H:%M}] {msg}", flush=True)


def load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception as e:
            log(f"Aviso: {path.name} corrupto ({e}); se usa valor por defecto.")
            return default
    return default


def save_json(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(path)


def send_telegram(text):
    if not TG_TOKEN or not TG_CHAT:
        log("Sin credenciales de Telegram; no se envia mensaje.")
        return False
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    for intento in range(3):
        try:
            r = requests.post(url, json={
                "chat_id": TG_CHAT, "text": text,
                "parse_mode": "HTML", "disable_web_page_preview": True,
            }, timeout=30)
            if r.status_code == 200:
                return True
            if r.status_code == 429:
                espera = r.json().get("parameters", {}).get("retry_after", 5)
                time.sleep(espera + 1)
                continue
            log(f"Telegram error {r.status_code}: {r.text[:200]}")
            return False
        except Exception as e:
            log(f"Fallo enviando Telegram (intento {intento+1}): {e}")
            time.sleep(2 ** intento)
    return False


def gflights_url(origin, dest, date):
    return (f"https://www.google.com/travel/flights?q=Flights%20{origin}%20to%20"
            f"{dest}%20on%20{date}%20oneway")


def date_range(center_str, flex):
    center = datetime.strptime(center_str, "%Y-%m-%d")
    return [(center + timedelta(days=d)).strftime("%Y-%m-%d")
            for d in range(-flex, flex + 1)]


def fechas_del_tramo(tramo, flex_default):
    if tramo.get("fechas"):
        return list(tramo["fechas"])
    rango = tramo.get("rango_fechas")
    if rango:
        d0 = datetime.strptime(rango["desde"], "%Y-%m-%d")
        d1 = datetime.strptime(rango["hasta"], "%Y-%m-%d")
        if d1 < d0:
            d0, d1 = d1, d0
        return [(d0 + timedelta(days=i)).strftime("%Y-%m-%d")
                for i in range((d1 - d0).days + 1)]
    return date_range(tramo["fecha_salida"], tramo.get("flex_days", flex_default))


def validar_config(cfg):
    errores = []
    hoy = ahora().date()
    if not cfg.get("tramos"):
        errores.append("No hay tramos definidos.")
    for t in cfg.get("tramos", []):
        n = t.get("nombre", "?")
        if not t.get("origen") or not t.get("destino"):
            errores.append(f"{n}: falta origen o destino.")
        try:
            fechas = fechas_del_tramo(t, cfg.get("flex_days", 3))
        except Exception as e:
            errores.append(f"{n}: fechas invalidas ({e})")
            continue
        if not fechas:
            errores.append(f"{n}: no genera ninguna fecha.")
        for f in fechas:
            d = datetime.strptime(f, "%Y-%m-%d").date()
            if d < hoy:
                errores.append(f"{n}: la fecha {f} ya paso.")
                break
            if (d - hoy).days > 330:
                errores.append(
                    f"{n}: {f} esta a mas de 330 dias; las aerolineas aun no "
                    "publican tarifas y no habra resultados utiles.")
                break
        for code in list(t.get("origen", [])) + list(t.get("destino", [])):
            if not (isinstance(code, str) and len(code) == 3 and code.isalpha()):
                errores.append(f"{n}: '{code}' no parece codigo IATA valido.")
    return errores


def search_one(origin, dest, date, cfg, reintentos=2):
    for intento in range(reintentos + 1):
        try:
            q = create_query(
                flights=[FlightQuery(date=date, from_airport=origin,
                                     to_airport=dest,
                                     max_stops=cfg.get("max_escalas"))],
                seat=cfg["clase"], trip="one-way",
                passengers=Passengers(adults=cfg["pasajeros_adultos"]),
                currency=cfg["moneda"], language="es",
            )
            if HAS_SHOPPING:
                res = get_flights(q, shopping=ShoppingOptions(
                    ranking_mode="best", result_sort="price"))
            else:
                res = get_flights(q)

            opciones = []
            for f in res:
                price = getattr(f, "price", None)
                if not isinstance(price, (int, float)) or price <= 0:
                    continue
                dur = sum(getattr(s, "duration", 0) or 0
                          for s in (getattr(f, "flights", []) or []))
                max_h = cfg.get("max_horas_viaje")
                if max_h and dur and dur > max_h * 60:
                    continue
                opciones.append({
                    "price": int(price),
                    "airlines": ", ".join(getattr(f, "airlines", []) or []),
                    "stops": max(0, len(getattr(f, "flights", []) or []) - 1),
                    "duracion_min": dur or None,
                    "origin": origin, "dest": dest, "date": date,
                })
            opciones.sort(key=lambda x: x["price"])
            STATS["ok"] += 1
            return opciones

        except Exception as e:
            msg = str(e)[:120]
            if intento < reintentos:
                time.sleep((2 ** intento) + random.uniform(0, 1.5))
                continue
            log(f"  ! {origin}->{dest} {date}: {msg}")
            STATS["fail"] += 1
            return []
    return []


def scan_tramo(tramo, cfg):
    dates = fechas_del_tramo(tramo, cfg.get("flex_days", 3))
    combos = list(product(tramo["origen"], tramo["destino"], dates))
    log(f"  Escaneando {len(combos)} combinaciones...")

    todas = []
    por_ruta = {}
    pausa = cfg.get("pausa_seg", 1.2)

    for i, (origin, dest, date) in enumerate(combos):
        ops = search_one(origin, dest, date, cfg)
        todas.extend(ops)
        if ops:
            ruta = f"{origin}-{dest}"
            if ruta not in por_ruta or ops[0]["price"] < por_ruta[ruta]["price"]:
                por_ruta[ruta] = ops[0]
        if i < len(combos) - 1:
            time.sleep(pausa + random.uniform(0, 0.6))

    todas.sort(key=lambda x: x["price"])
    return (todas[0] if todas else None), por_ruta, todas[:3]


def evaluar_alerta(tramo, mejor, entry, cfg):
    price = mejor["price"]
    umbral = tramo.get("umbral_precio")

    if umbral is not None and price <= umbral:
        return True, f"por debajo de tu umbral de {umbral} {cfg['moneda']}"

    ruta = f"{mejor['origin']}-{mejor['dest']}"
    hist = [p["price"] for p in entry.get("por_ruta", {}).get(ruta, [])]
    if len(hist) < cfg.get("min_lecturas_base", 4):
        return False, ""

    base = median(hist)
    minimo = min(hist)
    caida = (base - price) / base * 100

    if price < minimo and caida >= cfg.get("baja_pct_alerta", 12):
        return True, (f"{caida:.0f}% bajo la mediana de {ruta} "
                      f"({base:.0f} {cfg['moneda']}, minimo previo {minimo})")
    return False, ""


def puede_alertar(entry, mejor, cfg):
    horas = cfg.get("cooldown_alerta_horas", 12)
    ult = entry.get("ultima_alerta")
    if not ult:
        return True
    if mejor["price"] <= ult["price"] * 0.97:
        return True
    try:
        t = datetime.fromisoformat(ult["t"])
        if t.tzinfo is None:
            t = t.replace(tzinfo=PTY_TZ)
        return (ahora() - t) >= timedelta(hours=horas)
    except Exception:
        return True


def fmt_dur(minutos):
    if not minutos:
        return ""
    return f"{minutos // 60}h {minutos % 60:02d}m"


def main():
    cfg = load_json(CONFIG, None)
    if not cfg:
        log("ERROR: no se pudo leer config.json")
        sys.exit(1)

    errores = validar_config(cfg)
    if errores:
        log("ERRORES DE CONFIGURACION:\n" + "\n".join(f"  - {e}" for e in errores))
        send_telegram("\u26a0\ufe0f <b>Error de configuraci\u00f3n</b>\n\n"
                      + "\n".join(f"\u2022 {e}" for e in errores))
        sys.exit(1)

    history = load_json(HISTORY, {})
    ts = ahora().isoformat(timespec="minutes")
    resumen = []

    for tramo in cfg["tramos"]:
        nombre = tramo["nombre"]
        log(f"Tramo: {nombre}")
        mejor, por_ruta, top3 = scan_tramo(tramo, cfg)

        entry = history.setdefault(nombre, {"por_ruta": {}, "mejor_historico": None})
        entry.setdefault("por_ruta", {})

        if not mejor:
            log("  Sin resultados.")
            resumen.append(f"\u2022 {nombre}: <i>sin datos</i>")
            continue

        alertar, motivo = evaluar_alerta(tramo, mejor, entry, cfg)

        for ruta, op in por_ruta.items():
            serie = entry["por_ruta"].setdefault(ruta, [])
            serie.append({"t": ts, "price": op["price"], "date": op["date"]})
            entry["por_ruta"][ruta] = serie[-40:]

        mh = entry.get("mejor_historico")
        if mh is None or mejor["price"] < mh["price"]:
            entry["mejor_historico"] = {**mejor, "visto": ts}

        ruta_k = f"{mejor['origin']}-{mejor['dest']}"
        serie = [p["price"] for p in entry["por_ruta"].get(ruta_k, [])]
        base_txt = (f" | mediana {ruta_k}: {median(serie):.0f}, min {min(serie)}"
                    if len(serie) >= 2 else " | construyendo linea base")
        log(f"  Mejor: {mejor['price']} {cfg['moneda']} ({ruta_k} {mejor['date']}, "
            f"{mejor['stops']} esc, {fmt_dur(mejor['duracion_min'])}, "
            f"{mejor['airlines']}){base_txt}")

        resumen.append(f"\u2022 <b>{nombre}</b>: {mejor['price']} {cfg['moneda']} "
                       f"({ruta_k} {mejor['date']}, {mejor['stops']} esc)")

        if alertar and puede_alertar(entry, mejor, cfg):
            alt = ""
            if len(top3) > 1:
                alt = "\n\n<i>Otras opciones:</i>\n" + "\n".join(
                    f"  {o['price']} {cfg['moneda']} \u00b7 {o['origin']}\u2192{o['dest']} "
                    f"{o['date']} \u00b7 {o['stops']} esc" for o in top3[1:])
            msg = (f"\U0001F6A8 <b>PRECIO EXCEPCIONAL</b>\n\n"
                   f"<b>{nombre}</b>\n"
                   f"\U0001F4B0 <b>{mejor['price']} {cfg['moneda']}</b> \u2014 {motivo}\n"
                   f"\u2708\ufe0f {mejor['origin']} \u2192 {mejor['dest']} el {mejor['date']}\n"
                   f"\U0001F503 {mejor['stops']} escala(s) \u00b7 {fmt_dur(mejor['duracion_min'])}\n"
                   f"\U0001F3E2 {mejor['airlines']}{alt}\n\n"
                   f"<a href=\"{gflights_url(mejor['origin'], mejor['dest'], mejor['date'])}\">"
                   f"Abrir en Google Flights</a>")
            if send_telegram(msg):
                entry["ultima_alerta"] = {"t": ts, "price": mejor["price"]}
                log("  OK ALERTA enviada")
        elif alertar:
            log("  (alerta suprimida por cooldown)")

    save_json(HISTORY, history)

    total = STATS["ok"] + STATS["fail"]
    tasa = STATS["fail"] / total * 100 if total else 0
    log(f"Consultas: {STATS['ok']} ok, {STATS['fail']} fallidas ({tasa:.0f}%)")

    if total and tasa >= 50:
        send_telegram(
            f"\u26a0\ufe0f <b>El monitor est\u00e1 fallando</b>\n\n"
            f"{STATS['fail']} de {total} consultas fallaron ({tasa:.0f}%).\n"
            f"Google pudo cambiar su formato o estar bloqueando. "
            f"Revisa los logs y prueba actualizar:\n"
            f"<code>pip install -U faster-flights</code>")

    if os.environ.get("SEND_SUMMARY") == "1" and resumen:
        send_telegram(f"\U0001F4CA <b>Resumen diario</b> ({ahora():%d/%m %H:%M})\n\n"
                      + "\n".join(resumen))

    log("Listo.")


if __name__ == "__main__":
    main()
