"""
Dzienny runner: scrape nowych leadów + wyślij do 300 maili + powiadomienie Telegram.
Uruchamiany przez GitHub Actions codziennie o 9:00 PL.

Zmienne środowiskowe (GitHub Secrets):
  BREVO_API_KEY, GOOGLE_PLACES_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, SITE_URL
"""
import os
import subprocess
import sys
import httpx
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

BRANŻE = ["ksiegowosc", "finanse", "prawo", "nieruchomosci"]
LIMIT_DZIENNIE = 295  # margines bezpieczeństwa poniżej 300

TELEGRAM_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHATID = os.getenv("TELEGRAM_CHAT_ID", "")


def tg(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHATID:
        print(f"[TG] {msg}")
        return
    try:
        httpx.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHATID, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        print(f"[TG ERROR] {e}")


def run(cmd: list[str]) -> tuple[int, str]:
    """Uruchamia subprocess, zwraca (exit_code, stdout)."""
    result = subprocess.run(
        cmd, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        cwd=Path(__file__).parent.parent,
    )
    return result.returncode, result.stdout + result.stderr


def parse_sent(output: str) -> int:
    for line in output.splitlines():
        if line.startswith("Wysłano:"):
            try:
                return int(line.split(":")[1].split()[0])
            except (IndexError, ValueError):
                pass
    return 0


def parse_new_leads(output: str) -> int:
    total = 0
    for line in output.splitlines():
        if "Zapisano:" in line and "nowych leadów" in line:
            try:
                total += int(line.strip().split(":")[1].split()[0])
            except (IndexError, ValueError):
                pass
    return total


def main():
    today = date.today().isoformat()
    day_of_year = date.today().timetuple().tm_yday
    scrape_branża = BRANŻE[day_of_year % len(BRANŻE)]

    tg(f"🚀 <b>Start kampanii {today}</b>\n📍 Scraping: <b>{scrape_branża}</b>")

    # 1. Scrape nowych leadów dla dzisiejszej branży
    print(f"\n=== SCRAPING: {scrape_branża} ===")
    _, scrape_out = run([
        sys.executable, "leads/scraper_places.py",
        "--branża", scrape_branża,
        "--strony", "2",
    ])
    print(scrape_out)
    new_leads = parse_new_leads(scrape_out)

    # 2. Wyślij maile — rotacja przez wszystkie branże
    print("\n=== WYSYŁKA ===")
    total_sent = 0
    results: dict[str, int] = {}

    for branża in BRANŻE:
        remaining = LIMIT_DZIENNIE - total_sent
        if remaining <= 0:
            break

        _, send_out = run([
            sys.executable, "leads/sender.py",
            "--branża", branża,
            "--limit", str(min(remaining, 100)),
        ])
        print(send_out)
        sent = parse_sent(send_out)
        if sent:
            results[branża] = sent
            total_sent += sent

    # 3. Follow-up do osób które kliknęły 5 dni temu
    print("\n=== FOLLOW-UP ===")
    _, followup_out = run([sys.executable, "leads/followup.py"])
    print(followup_out)
    followup_sent = 0
    for line in followup_out.splitlines():
        if line.startswith("Follow-up wysłany:"):
            try:
                followup_sent = int(line.split(":")[1].strip())
            except (IndexError, ValueError):
                pass

    # 4. Telegram — podsumowanie
    lines = [f"✅ <b>Kampania {today} zakończona</b>\n"]
    for b, cnt in results.items():
        lines.append(f"  📧 {b}: {cnt} maili")
    if not results:
        lines.append("  ⚠️ Brak wysłanych maili (pusta kolejka?)")
    lines.append(f"\n<b>Łącznie: {total_sent}/{LIMIT_DZIENNIE} maili</b>")
    lines.append(f"🆕 Nowe leady: {new_leads}")
    if followup_sent:
        lines.append(f"🔁 Follow-up: {followup_sent}")

    tg("\n".join(lines))
    print("\n" + "\n".join(lines))


if __name__ == "__main__":
    main()
