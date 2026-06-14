"""
Sender maili do leadów — Brevo API + śledzenie kliknięć przez Vercel.

Użycie:
  python leads/sender.py --branża finanse --limit 20 --dry-run
  python leads/sender.py --branża finanse --limit 20
  python leads/sender.py --branża finanse --limit 20 --tylko-z-emailem

Zmienne środowiskowe (.env):
  BREVO_API_KEY   — klucz Brevo
  SITE_URL        — np. https://rolki-dla-ksiegowych.vercel.app
"""
import argparse
import base64
import csv
import json
import os
import sys
import time
import random
from datetime import date
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

LEADS_CSV   = Path(__file__).parent / "leads.csv"
SENT_LOG    = Path(__file__).parent / "sent_log.json"
TEMPLATES   = {
    "finanse":       Path(__file__).parent / "template_finanse.html",
    "nieruchomosci": Path(__file__).parent / "template_nieruchomosci.html",
    "prawo":         Path(__file__).parent / "template_prawo.html",
    "ksiegowosc":    Path(__file__).parent / "template_ksiegowosc.html",
}
SUBJECTS = {
    "finanse":       "Rolki eksperckie dla doradcy finansowego — gotowe w 24h, bez nagrywania",
    "nieruchomosci": "Rolki eksperckie dla agencji nieruchomości — gotowe w 24h, bez nagrywania",
    "prawo":         "Rolki eksperckie dla kancelarii prawnej — gotowe w 24h, bez nagrywania",
    "ksiegowosc":    "Rolki eksperckie dla biura rachunkowego — gotowe w 24h, bez nagrywania",
}
BREVO_URL  = "https://api.brevo.com/v3/smtp/email"
SENDER     = {"name": "Dawid | Twórcza Sieć", "email": "biuro@tworczasiec.pl"}
PLACEHOLDER_EMAILS = {"twoj@email.pl", "email@example.com", "kontakt@firma.pl",
                      "biuro@firma.pl", "info@firma.pl", "twoj@adres.pl"}


def _load_sent() -> dict:
    if SENT_LOG.exists():
        return json.loads(SENT_LOG.read_text(encoding="utf-8"))
    return {}


def _save_sent(log: dict):
    SENT_LOG.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")


def _tracking_url(email: str, branża: str, site_url: str) -> str:
    encoded = base64.urlsafe_b64encode(email.encode()).decode()
    return f"{site_url.rstrip('/')}/api/track?e={encoded}&r={branża}"


def _unsubscribe_url(email: str, site_url: str) -> str:
    encoded = base64.urlsafe_b64encode(email.encode()).decode()
    return f"{site_url.rstrip('/')}/api/unsubscribe?e={encoded}"


def _render_template(template_path: Path, tracking_url: str, unsubscribe_url: str,
                     firma: str = "") -> str:
    html = template_path.read_text(encoding="utf-8")
    html = html.replace("{{TRACKING_URL}}", tracking_url)
    html = html.replace("{{UNSUBSCRIBE_URL}}", unsubscribe_url)
    html = html.replace("{{FIRMA}}", firma)
    return html


def send_email(to_email: str, to_name: str, subject: str, html: str,
               api_key: str, dry_run: bool = False) -> bool:
    if dry_run:
        print(f"  [DRY-RUN] -> {to_email}")
        return True

    payload = {
        "sender":      SENDER,
        "to":          [{"email": to_email, "name": to_name}],
        "subject":     subject,
        "htmlContent": html,
    }
    try:
        r = httpx.post(
            BREVO_URL,
            json=payload,
            headers={"api-key": api_key, "Content-Type": "application/json"},
            timeout=15,
        )
        if r.status_code in (200, 201):
            return True
        print(f"  Błąd Brevo ({r.status_code}): {r.text[:200]}")
        return False
    except Exception as e:
        print(f"  Wyjątek: {e}")
        return False


def load_leads(branża: str, tylko_z_emailem: bool) -> list[dict]:
    if not LEADS_CSV.exists():
        return []
    with open(LEADS_CSV, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    rows = [r for r in rows if r.get("branża") == branża]
    if tylko_z_emailem:
        rows = [r for r in rows if r.get("email", "").strip()]
    return rows


def mark_sent(leads_csv: Path, email: str):
    """Ustawia datę wysyłki w pliku CSV dla danego emaila."""
    rows = []
    with open(leads_csv, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            if row.get("email", "").strip().lower() == email.lower():
                row["wysłano"] = str(date.today())
            rows.append(row)

    with open(leads_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--branża",         default="finanse",
                        choices=list(SUBJECTS.keys()))
    parser.add_argument("--limit",          type=int, default=20,
                        help="Max maili na jedno uruchomienie")
    parser.add_argument("--dry-run",        action="store_true",
                        help="Nie wysyła — tylko pokazuje co by poszło")
    parser.add_argument("--tylko-z-emailem", action="store_true",
                        help="Pomija kontakty bez emaila")
    args = parser.parse_args()

    api_key  = os.getenv("BREVO_API_KEY", "")
    site_url = os.getenv("SITE_URL", "https://rolki-dla-ksiegowych.vercel.app")

    if not api_key and not args.dry_run:
        print("BŁĄD: brak BREVO_API_KEY w .env")
        return

    sent_log  = _load_sent()
    leads     = load_leads(args.branża, args.tylko_z_emailem)
    template  = TEMPLATES[args.branża]
    subject   = SUBJECTS[args.branża]

    # pomiń już wysłane, placeholder emaile i adresy z %20
    sent_log_lower = {e.lower() for e in sent_log}
    pending = [l for l in leads
               if l.get("email")
               and not l["email"].strip().startswith("%")
               and l["email"].strip().lower() not in PLACEHOLDER_EMAILS
               and l["email"].strip().lower() not in sent_log_lower
               and not l.get("wysłano")]

    print(f"Branża: {args.branża} | Czeka: {len(pending)} | Limit: {args.limit}")

    if not pending:
        print("Brak nowych leadów do wysyłki.")
        return

    ok = 0
    for lead in pending[:args.limit]:
        email = lead["email"].strip()
        firma = lead.get("firma", "")
        print(f"[{ok+1}] {firma} <{email}>")

        tracking_url    = _tracking_url(email, args.branża, site_url)
        unsubscribe_url = _unsubscribe_url(email, site_url)
        html            = _render_template(template, tracking_url, unsubscribe_url, firma)

        success = send_email(email, firma, subject, html, api_key, args.dry_run)

        if success:
            sent_log[email] = str(date.today())
            _save_sent(sent_log)
            if not args.dry_run:
                mark_sent(LEADS_CSV, email)
            ok += 1
            delay = random.uniform(8, 18)   # naturalne opóźnienie między mailami
            time.sleep(delay)

    print(f"\nWysłano: {ok} maili")
    if args.dry_run:
        print("(dry-run — żaden mail nie wyszedł)")


if __name__ == "__main__":
    main()
