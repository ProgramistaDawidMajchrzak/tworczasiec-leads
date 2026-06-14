"""
Raport dzienny (każdy wieczór 20:00) i tygodniowy (niedziela 20:00).
Dane o wysyłce: sent_log.json + leads.csv
Dane o kliknięciach: Brevo Statistics API
"""
import argparse
import csv
import json
import os
from datetime import date, timedelta
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

BREVO_KEY    = os.getenv("BREVO_API_KEY", "")
TG_TOKEN     = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT      = os.getenv("TELEGRAM_CHAT_ID", "")
LEADS_CSV    = Path(__file__).parent / "leads.csv"
SENT_LOG     = Path(__file__).parent / "sent_log.json"


def tg(msg: str):
    if not TG_TOKEN or not TG_CHAT:
        print(msg)
        return
    try:
        httpx.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        print(f"[TG ERROR] {e}")


def email_to_branza() -> dict[str, str]:
    mapping = {}
    if not LEADS_CSV.exists():
        return mapping
    with open(LEADS_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            e = row.get("email", "").strip().lower()
            if e:
                mapping[e] = row.get("branża", "nieznana")
    return mapping


def sent_by_branza(start: date, end: date, branza_map: dict) -> dict[str, int]:
    if not SENT_LOG.exists():
        return {}
    log = json.loads(SENT_LOG.read_text(encoding="utf-8"))
    counts: dict[str, int] = {}
    for email, d_str in log.items():
        try:
            d = date.fromisoformat(d_str)
        except ValueError:
            continue
        if start <= d <= end:
            b = branza_map.get(email.lower(), "nieznana")
            counts[b] = counts.get(b, 0) + 1
    return counts


def brevo_clicks(start: date, end: date) -> list[dict]:
    """Pobiera kliknięcia z Brevo (paginacja)."""
    all_events: list[dict] = []
    offset = 0
    while True:
        try:
            r = httpx.get(
                "https://api.brevo.com/v3/smtp/statistics/events",
                headers={"api-key": BREVO_KEY},
                params={
                    "event":     "clicked",
                    "startDate": start.isoformat(),
                    "endDate":   end.isoformat(),
                    "limit":     500,
                    "offset":    offset,
                },
                timeout=20,
            )
            events = r.json().get("events", [])
            all_events.extend(events)
            if len(events) < 500:
                break
            offset += 500
        except Exception as e:
            print(f"[Brevo API error] {e}")
            break
    return all_events


def format_report(title: str, start: date, end: date) -> str:
    branza_map = email_to_branza()

    # Wysyłka
    sent = sent_by_branza(start, end, branza_map)
    total_sent = sum(sent.values())

    # Kliknięcia
    clicks = brevo_clicks(start, end)
    click_by_branza: dict[str, int] = {}
    for c in clicks:
        b = branza_map.get(c.get("email", "").lower(), "nieznana")
        click_by_branza[b] = click_by_branza.get(b, 0) + 1
    total_clicks = len(clicks)

    lines = [f"📊 <b>{title}</b>\n"]

    lines.append("📧 <b>Wysłane maile:</b>")
    BRANŻE_ORDER = ["ksiegowosc", "finanse", "prawo", "nieruchomosci", "nieznana"]
    for b in BRANŻE_ORDER:
        if b in sent:
            lines.append(f"  • {b}: {sent[b]}")
    lines.append(f"  <b>Razem: {total_sent}</b>\n")

    lines.append("🖱️ <b>Kliknięcia w link:</b>")
    for b in BRANŻE_ORDER:
        if b in click_by_branza:
            lines.append(f"  • {b}: {click_by_branza[b]}")
    if not click_by_branza:
        lines.append("  • brak kliknięć")
    lines.append(f"  <b>Razem: {total_clicks}</b>")

    if total_sent > 0 and total_clicks > 0:
        ctr = round(total_clicks / total_sent * 100, 1)
        lines.append(f"\n📈 CTR: <b>{ctr}%</b>")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--typ", choices=["dzienny", "tygodniowy", "auto"],
                        default="auto",
                        help="auto = dzienny pn-sb, tygodniowy w niedzielę")
    args = parser.parse_args()

    today = date.today()
    is_sunday = today.weekday() == 6

    if args.typ == "auto":
        typ = "tygodniowy" if is_sunday else "dzienny"
    else:
        typ = args.typ

    if typ == "dzienny":
        msg = format_report(
            f"Podsumowanie dnia {today}",
            start=today,
            end=today,
        )
    else:
        start = today - timedelta(days=6)
        msg = format_report(
            f"Raport tygodniowy {start} – {today}",
            start=start,
            end=today,
        )

    print(msg)
    tg(msg)


if __name__ == "__main__":
    main()
