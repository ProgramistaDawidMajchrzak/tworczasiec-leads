"""
Follow-up: wysyła drugi mail do osób które kliknęły 5 dni temu i nie dostały jeszcze follow-up.

Brevo API → kliknięcia sprzed 5 dni → sprawdź followup_log.json → wyślij → zapisz log.
"""
import base64
import csv
import json
import os
import sys
import time
import random
from datetime import date, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

BREVO_KEY       = os.getenv("BREVO_API_KEY", "")
SITE_URL        = os.getenv("SITE_URL", "https://www.tworczasiec.pl")
LEADS_CSV       = Path(__file__).parent / "leads.csv"
FOLLOWUP_LOG    = Path(__file__).parent / "followup_log.json"
TEMPLATE        = Path(__file__).parent / "template_followup.html"
BREVO_URL       = "https://api.brevo.com/v3/smtp/email"
SENDER          = {"name": "Dawid | Twórcza Sieć", "email": "biuro@tworczasiec.pl"}
SUBJECT         = "Widziałem że byłeś na stronie — masz pytania?"
DAYS_AFTER      = 5   # ile dni po kliknięciu wysłać follow-up


def load_followup_log() -> dict:
    if FOLLOWUP_LOG.exists():
        return json.loads(FOLLOWUP_LOG.read_text(encoding="utf-8"))
    return {}


def save_followup_log(log: dict):
    FOLLOWUP_LOG.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")


def load_leads_map() -> dict[str, dict]:
    """email → {firma, branża} z leads.csv"""
    result = {}
    if not LEADS_CSV.exists():
        return result
    with open(LEADS_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            e = row.get("email", "").strip().lower()
            if e:
                result[e] = {"firma": row.get("firma", ""), "branża": row.get("branża", "")}
    return result


def get_brevo_clicks(target_date: date) -> list[str]:
    """Zwraca listę emaili które kliknęły w link w danym dniu."""
    emails = []
    offset = 0
    date_str = target_date.isoformat()
    while True:
        try:
            r = httpx.get(
                "https://api.brevo.com/v3/smtp/statistics/events",
                headers={"api-key": BREVO_KEY},
                params={
                    "event":     "clicked",
                    "startDate": date_str,
                    "endDate":   date_str,
                    "limit":     500,
                    "offset":    offset,
                },
                timeout=20,
            )
            events = r.json().get("events", [])
            emails.extend(e.get("email", "").lower() for e in events if e.get("email"))
            if len(events) < 500:
                break
            offset += 500
        except Exception as ex:
            print(f"[Brevo error] {ex}")
            break
    return list(set(emails))


def tracking_url(email: str, branża: str) -> str:
    encoded = base64.urlsafe_b64encode(email.encode()).decode()
    return f"{SITE_URL.rstrip('/')}/api/track?e={encoded}&r={branża}"


def unsubscribe_url(email: str) -> str:
    encoded = base64.urlsafe_b64encode(email.encode()).decode()
    return f"{SITE_URL.rstrip('/')}/api/unsubscribe?e={encoded}"


def render(email: str, firma: str, branża: str) -> str:
    html = TEMPLATE.read_text(encoding="utf-8")
    html = html.replace("{{TRACKING_URL}}", tracking_url(email, branża))
    html = html.replace("{{UNSUBSCRIBE_URL}}", unsubscribe_url(email))
    html = html.replace("{{FIRMA}}", firma)
    return html


def send(to_email: str, to_name: str, html: str) -> bool:
    payload = {
        "sender":      SENDER,
        "to":          [{"email": to_email, "name": to_name}],
        "subject":     SUBJECT,
        "htmlContent": html,
    }
    try:
        r = httpx.post(
            BREVO_URL,
            json=payload,
            headers={"api-key": BREVO_KEY, "Content-Type": "application/json"},
            timeout=15,
        )
        if r.status_code in (200, 201):
            return True
        print(f"  Brevo {r.status_code}: {r.text[:150]}")
        return False
    except Exception as e:
        print(f"  Wyjątek: {e}")
        return False


def main():
    target_date = date.today() - timedelta(days=DAYS_AFTER)
    print(f"Follow-up: szukam kliknięć z {target_date}...")

    clicked_emails = get_brevo_clicks(target_date)
    print(f"Kliknięcia: {len(clicked_emails)}")

    if not clicked_emails:
        print("Brak kliknięć — follow-up nie wysłany.")
        return

    followup_log  = load_followup_log()
    leads_map     = load_leads_map()
    log_lower     = {e.lower() for e in followup_log}

    to_send = [e for e in clicked_emails if e not in log_lower]
    print(f"Do wysłania (bez już wysłanych follow-up): {len(to_send)}")

    ok = 0
    for email in to_send:
        lead   = leads_map.get(email, {})
        firma  = lead.get("firma", "")
        branża = lead.get("branża", "")

        print(f"[{ok+1}] {firma or email} <{email}>")
        html = render(email, firma, branża)

        if send(email, firma, html):
            followup_log[email] = str(date.today())
            save_followup_log(followup_log)
            ok += 1
            time.sleep(random.uniform(6, 14))

    print(f"\nFollow-up wysłany: {ok}")


if __name__ == "__main__":
    main()
