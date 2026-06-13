"""
Scraper leadów — Google Places API (Text Search) + scraping emaili ze stron www.

Użycie:
  python leads/scraper_places.py --branża finanse --miasta "Warszawa,Kraków" --klucz AIza...
  python leads/scraper_places.py --branża ksiegowosc --strony 3

Klucz API możesz też ustawić w .env jako GOOGLE_PLACES_KEY=AIza...
"""
import argparse
import csv
import os
import re
import time
import random
from pathlib import Path
from urllib.parse import urlparse

import httpx
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

OUTPUT_CSV = Path(__file__).parent / "leads.csv"

PLACES_TEXT_SEARCH  = "https://maps.googleapis.com/maps/api/place/textsearch/json"
PLACES_DETAILS      = "https://maps.googleapis.com/maps/api/place/details/json"

# Frazy wyszukiwania per branża
SEARCH_QUERIES = {
    "finanse":        "doradca finansowy kredytowy",
    "nieruchomosci":  "agencja biuro nieruchomości",
    "prawo":          "kancelaria prawna adwokacka",
    "ksiegowosc":     "biuro rachunkowe księgowość",
}

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
SKIP_EMAIL_PARTS = ["example", "sentry", "noreply", "no-reply", ".png", ".jpg",
                    "schema", "wix", "wordpress", "jquery", "test@", "@2x", "google"]
PLACEHOLDER_EMAILS = {"twoj@email.pl", "email@example.com", "kontakt@firma.pl",
                      "biuro@firma.pl", "info@firma.pl", "twoj@adres.pl"}


def places_search(query: str, miasto: str, api_key: str,
                  page_token: str = None) -> dict:
    """Wywołuje Places API (legacy) Text Search, zwraca surowy JSON."""
    params = {
        "query":    f"{query} {miasto}",
        "language": "pl",
        "region":   "pl",
        "key":      api_key,
    }
    if page_token:
        params["pagetoken"] = page_token

    r = httpx.get(PLACES_TEXT_SEARCH, params=params, timeout=15)
    if r.status_code != 200:
        print(f"  Places API błąd {r.status_code}: {r.text[:200]}")
        return {}
    data = r.json()
    if data.get("status") not in ("OK", "ZERO_RESULTS"):
        print(f"  Places API status: {data.get('status')} — {data.get('error_message','')}")
        return {}
    # Normalizuj do wspólnego formatu (website pobieramy osobno przez Details)
    results = []
    for p in data.get("results", []):
        results.append({
            "displayName":        {"text": p.get("name", "")},
            "formattedAddress":   p.get("formatted_address", ""),
            "websiteUri":         "",          # uzupełniane przez place_details()
            "nationalPhoneNumber": p.get("formatted_phone_number", ""),
            "place_id":           p.get("place_id", ""),
        })
    return {"places": results, "nextPageToken": data.get("next_page_token")}


def place_details(place_id: str, api_key: str) -> str:
    """Pobiera website z Place Details API."""
    if not place_id:
        return ""
    r = httpx.get(PLACES_DETAILS, params={
        "place_id": place_id,
        "fields":   "website,formatted_phone_number",
        "key":      api_key,
    }, timeout=10)
    if r.status_code == 200:
        result = r.json().get("result", {})
        return result.get("website", "")
    return ""


def find_email_on_website(url: str) -> str:
    """Odwiedza stronę firmową i szuka emaila."""
    if not url:
        return ""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0",
        "Accept-Language": "pl-PL,pl;q=0.9",
    }
    for path in ["", "/kontakt", "/contact", "/o-nas", "/o-firmie"]:
        try:
            target = url.rstrip("/") + path
            r = requests.get(target, headers=headers, timeout=10, allow_redirects=True)
            emails = EMAIL_RE.findall(r.text)
            emails = [e.lower() for e in emails
                      if not any(x in e.lower() for x in SKIP_EMAIL_PARTS)
                      and e.lower() not in PLACEHOLDER_EMAILS]
            if emails:
                return emails[0]
        except Exception:
            pass
        time.sleep(0.3)
    return ""


def scrape_miasto(query: str, miasto: str, max_pages: int, api_key: str) -> list[dict]:
    results = []
    page_token = None

    for pg in range(max_pages):
        print(f"  Strona {pg + 1} (Places API)...")
        data = places_search(query, miasto, api_key, page_token)

        places = data.get("places", [])
        if not places:
            print("  Brak wyników.")
            break

        print(f"  Znaleziono {len(places)} firm, szukam emaili...")

        for place in places:
            name     = place.get("displayName", {}).get("text", "")
            address  = place.get("formattedAddress", "")
            phone    = place.get("nationalPhoneNumber", "")
            place_id = place.get("place_id", "")

            # Pobierz website z Place Details
            www = place_details(place_id, api_key)
            time.sleep(0.2)

            name_safe = name.encode("ascii", "replace").decode("ascii")
            print(f"  [{name_safe[:40]}] {www[:50] or 'brak www'}", end=" ... ", flush=True)
            email = find_email_on_website(www) if www else ""
            print(f"email: {email}" if email else "brak emaila")

            results.append({
                "firma":   name,
                "miasto":  miasto,
                "telefon": phone,
                "email":   email,
                "www":     www,
            })
            time.sleep(random.uniform(0.4, 0.9))

        page_token = data.get("nextPageToken")
        if not page_token:
            break
        time.sleep(2)

    return results


def load_existing_emails(csv_path: Path) -> set:
    if not csv_path.exists():
        return set()
    with open(csv_path, encoding="utf-8") as f:
        return {r.get("email", "").lower() for r in csv.DictReader(f) if r.get("email")}


def save_leads(leads: list[dict], branża: str, csv_path: Path) -> int:
    existing = load_existing_emails(csv_path)
    new = [l for l in leads if l.get("email") and l["email"].lower() not in existing]
    if not new:
        return 0

    fieldnames = ["firma", "miasto", "telefon", "email", "www", "branża", "wysłano"]
    write_header = not csv_path.exists()
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            w.writeheader()
        for lead in new:
            row = {k: lead.get(k, "") for k in fieldnames}
            row["branża"] = branża
            w.writerow(row)
    return len(new)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--branża",  default="ksiegowosc",
                        choices=list(SEARCH_QUERIES.keys()))
    parser.add_argument("--miasta",
                        default="Warszawa,Kraków,Wrocław,Gdańsk,Poznań,Łódź,Katowice,Lublin")
    parser.add_argument("--strony",  type=int, default=2,
                        help="Stron per miasto (20 firm/str)")
    parser.add_argument("--klucz",   default=None,
                        help="Google Places API key (lub ustaw GOOGLE_PLACES_KEY w .env)")
    args = parser.parse_args()

    api_key = args.klucz or os.getenv("GOOGLE_PLACES_KEY", "")
    if not api_key:
        print("BŁĄD: podaj --klucz AIza... lub ustaw GOOGLE_PLACES_KEY w leads/.env")
        return

    query  = SEARCH_QUERIES[args.branża]
    miasta = [m.strip() for m in args.miasta.split(",") if m.strip()]
    total  = 0

    for miasto in miasta:
        print(f"\n=== {miasto} [{args.branża}] ===")
        leads = scrape_miasto(query, miasto, args.strony, api_key)
        added = save_leads(leads, args.branża, OUTPUT_CSV)
        print(f"  Zapisano: {added} nowych leadów z emailem")
        total += added

    print(f"\nGotowe! Łącznie: {total} leadów")
    print(f"Plik: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
