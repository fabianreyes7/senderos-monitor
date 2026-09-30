#!/usr/bin/env python3
"""Monitor y auto-reserva - Senderos Acueducto de Bogotá.

Consulta el calendario, guarda histórico, avisa por ntfy.sh cuando cambia
algo en las franjas de interés y, si está habilitado en config.json,
intenta UNA reserva (en total, entre todos los sitios) cuando hay cupo.

  capacity = cupo total, count = inscritos  →  libres = capacity - count

Uso:
  python3 monitor.py              # una consulta (para cron)
  python3 monitor.py --loop 300   # cada 5 min
"""
import http.cookiejar
import json
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

URL = "https://acueducto.com.co/senderos/s/"
PAGE = "https://acueducto.com.co/senderos/"

BASE = Path(__file__).resolve().parent
CONFIG = json.loads((BASE / "config.json").read_text())
HISTORY = BASE / "history.jsonl"
STATE = BASE / "state.json"
BOOKING = BASE / "booking.json"  # intentos de reserva; "done" => no reintenta
MAX_REJECTED = 3

# {"17": [42, 44], "22": null}  (null = las primeras FIRST_N franjas de cada día)
SITES = CONFIG["sites"]
FIRST_N = 2
TARGET_DATES = set(CONFIG.get("dates") or [])  # vacío = cualquier fecha
NTFY_TOPIC = CONFIG.get("ntfy_topic")

HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/json; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://acueducto.com.co",
    "Referer": PAGE,
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36",
}

opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
)


def post(payload):
    req = urllib.request.Request(
        URL, data=json.dumps(payload).encode(), headers=HEADERS, method="POST"
    )
    with opener.open(req, timeout=20) as resp:
        return resp.status, resp.read().decode("utf-8", "replace")


def fetch_calendar(site):
    _, body = post({"action": "calendar", "site": site})
    return json.loads(body)


def snapshot(site, calendar):
    """{"17|03/10/2026|42": {...}} solo con las franjas vigiladas del sitio."""
    ids = SITES[site]
    snap = {}
    for day in calendar:
        schedules = day.get("schedules", [])
        watched = schedules[:FIRST_N] if ids is None else [s for s in schedules if s["id"] in ids]
        for s in watched:
            snap[f'{site}|{day["date"]}|{s["id"]}'] = {
                "site": site,
                "date": day["date"],
                "id": s["id"],
                "rank": ids.index(s["id"]) if ids else schedules.index(s),
                "label": day["label"],
                "schedule": s["schedule"],
                "capacity": s["capacity"],
                "count": s["count"],
                "free": s["capacity"] - s["count"],
            }
    return snap


def diff(old, new):
    msgs = []
    for key, cur in new.items():
        prev = old.get(key)
        where = f'Sitio {cur["site"]} · {cur["label"]} {cur["schedule"]}'
        if prev is None:
            msgs.append(f"NUEVA fecha: {where} — libres {cur['free']}/{cur['capacity']}")
        elif prev["free"] != cur["free"]:
            msgs.append(f"{where}: libres {prev['free']} → {cur['free']}")
    for key in old.keys() - new.keys():
        msgs.append(f"Desapareció: sitio {old[key]['site']} · {old[key]['label']} {old[key]['schedule']}")
    return msgs


def notify(text, priority="default"):
    print(text)
    if not NTFY_TOPIC:
        return
    req = urllib.request.Request(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=text.encode(),
        headers={"Title": "Senderos Acueducto", "Priority": priority, "Click": PAGE},
    )
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:
        print(f"[ntfy] error: {e}", file=sys.stderr)


def pick_slot(snap):
    """Franja con cupo suficiente: fecha más próxima, luego orden de sitios y de franjas."""
    needed = len(CONFIG["participants"])
    site_order = list(SITES)
    candidates = [
        s for s in snap.values()
        if s["free"] >= needed and (not TARGET_DATES or s["date"] in TARGET_DATES)
    ]
    candidates.sort(key=lambda s: (
        datetime.strptime(s["date"], "%d/%m/%Y"), site_order.index(s["site"]), s["rank"]
    ))
    return candidates[0] if candidates else None


def parse_save(status, body):
    """→ ("ok" | "rejected" | "unknown", mensaje).

    Éxito: [{"status":true,"message":"Se creó la reserva 859946 ...","reservationId":"859946"}]
    """
    try:
        data = json.loads(body)
        item = data[0] if isinstance(data, list) else data
        if item.get("status") is True and item.get("reservationId"):
            return "ok", item.get("message", "")
        if status == 200 and item.get("status") is False:
            return "rejected", item.get("message", body)
    except Exception:
        pass
    return "unknown", body


def try_booking(snap):
    if not CONFIG.get("auto_book"):
        return
    log = json.loads(BOOKING.read_text()) if BOOKING.exists() else {"done": False, "attempts": []}
    if log["done"]:
        return
    slot = pick_slot(snap)
    if not slot:
        return
    payload = {
        "action": "save",
        "email": CONFIG["email"],
        "date": slot["date"],
        "site": slot["site"],
        "schedule": str(slot["id"]),
        "participants": CONFIG["participants"],
    }
    try:
        opener.open(urllib.request.Request(PAGE, headers=HEADERS), timeout=20)  # cookie de sesión
        status, body = post(payload)
    except Exception as e:
        status, body = None, repr(e)
    result, message = parse_save(status, body)
    log["attempts"].append({
        "ts": datetime.now().isoformat(timespec="seconds"), "slot": slot,
        "http": status, "result": result, "response": body,
    })
    rejected = sum(a["result"] == "rejected" for a in log["attempts"])
    # Solo se reintenta si el servidor rechazó explícitamente (no se creó nada).
    # Ante éxito o resultado incierto se detiene para no duplicar reservas.
    log["done"] = result != "rejected" or rejected >= MAX_REJECTED
    BOOKING.write_text(json.dumps(log, ensure_ascii=False, indent=2))

    where = f"Sitio {slot['site']} · {slot['label']} {slot['schedule']}"
    if result == "ok":
        notify(f"✅ RESERVA HECHA: {where}\n{message}", priority="urgent")
    elif result == "rejected":
        extra = "" if not log["done"] else " (máx. intentos alcanzado, auto-reserva detenida)"
        notify(f"❌ Reserva rechazada: {where}\n{message}{extra}", priority="high")
    else:
        notify(
            f"⚠️ Resultado incierto: {where}\nHTTP {status}: {body[:300]}\n"
            "Auto-reserva detenida. Revisa tu correo / la web.",
            priority="urgent",
        )


def run_once():
    ts = datetime.now().isoformat(timespec="seconds")
    new = {}
    for i, site in enumerate(SITES):
        if i:
            time.sleep(2)  # sin ráfagas contra el servidor
        try:
            calendar = fetch_calendar(site)
        except Exception as e:
            print(f"{ts} error consultando sitio {site}: {e}", file=sys.stderr)
            return  # sin datos completos no se compara ni se reserva
        with HISTORY.open("a") as f:
            f.write(json.dumps({"ts": ts, "site": site, "data": calendar}, ensure_ascii=False) + "\n")
        new.update(snapshot(site, calendar))

    old = json.loads(STATE.read_text()) if STATE.exists() else None
    STATE.write_text(json.dumps(new, ensure_ascii=False, indent=2))

    if old is None:
        print(f"{ts} estado inicial guardado ({len(new)} franjas vigiladas)")
    else:
        msgs = diff(old, new)
        if msgs:
            notify("\n".join(msgs), priority="high")
        else:
            print(f"{ts} sin cambios")

    try_booking(new)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--loop":
        interval = max(60, int(sys.argv[2]))  # nunca menos de 1 min
        while True:
            run_once()
            time.sleep(interval)
    else:
        run_once()
