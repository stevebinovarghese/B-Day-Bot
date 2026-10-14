"""
Birthday reminder bot.

Runs twice a day (see bday.yml):
  - 11:55 PM IST -> "tomorrow is X's birthday" (arrives around midnight)
  - 10:00 AM IST -> "did you wish X?"

It reads your friends' birthdays from a Google Sheet (published as CSV)
and sends you a WhatsApp message using Meta's WhatsApp Cloud API.
"""

import csv
import io
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

IST = timezone(timedelta(hours=5, minutes=30))

# ---- Settings (come from GitHub "secrets", you never type them in code) ----
TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID", "")
MY_NUMBER = os.environ.get("MY_NUMBER", "")  # with country code, no + or spaces, e.g. 919876543210
SHEET_CSV_URL = os.environ.get("SHEET_CSV_URL", "")
SHEET_CSV_FILE = os.environ.get("SHEET_CSV_FILE", "")  # only used for local testing

# Names of the four message templates you create in Meta (see SETUP.md)
TEMPLATES = {
    ("birthday", "night"): os.environ.get("TEMPLATE_NIGHT", "bday_midnight"),
    ("birthday", "morning"): os.environ.get("TEMPLATE_MORNING", "bday_morning_check"),
    ("anniversary", "night"): os.environ.get("TEMPLATE_ANNIV_NIGHT", "anniv_midnight"),
    ("anniversary", "morning"): os.environ.get("TEMPLATE_ANNIV_MORNING", "anniv_morning_check"),
}

# ---- Testing helpers (optional) ----
MODE_OVERRIDE = os.environ.get("MODE", "").strip().lower()      # "night" or "morning"
DATE_OVERRIDE = os.environ.get("DATE_OVERRIDE", "").strip()      # e.g. 25-12 (pretend today is this date)
DRY_RUN = os.environ.get("DRY_RUN", "").strip().lower() in ("1", "true", "yes")


def now_ist():
    return datetime.now(IST)


def pick_mode(now):
    """Night run (after 8 PM IST) looks at TOMORROW. Morning run looks at TODAY."""
    if MODE_OVERRIDE in ("night", "morning"):
        return MODE_OVERRIDE
    return "night" if now.hour >= 20 else "morning"


def parse_day_month(text):
    """Accepts 25-12, 25/12, 25-12-1998, 25.12 ... always DAY first, then MONTH."""
    cleaned = text.strip().replace("/", "-").replace(".", "-")
    parts = cleaned.split("-")
    if len(parts) < 2:
        raise ValueError(f"Cannot read date: {text!r}")
    return int(parts[0]), int(parts[1])


def target_date(now, mode):
    if DATE_OVERRIDE:
        day, month = parse_day_month(DATE_OVERRIDE)
        base = now.replace(month=month, day=day)
    else:
        base = now
    return base + timedelta(days=1) if mode == "night" else base


def is_leap(year):
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def load_rows():
    if SHEET_CSV_FILE:
        with open(SHEET_CSV_FILE, newline="", encoding="utf-8") as f:
            text = f.read()
    else:
        if not SHEET_CSV_URL:
            sys.exit("SHEET_CSV_URL is not set. See SETUP.md, step 2.")
        resp = requests.get(SHEET_CSV_URL, timeout=30)
        resp.raise_for_status()
        text = resp.content.decode("utf-8")
    return list(csv.reader(io.StringIO(text)))


def kind_of(row):
    """Third column says 'birthday' or 'anniversary'. Empty means birthday."""
    kind = row[2].strip().lower() if len(row) > 2 else ""
    return "anniversary" if kind.startswith("anniv") else "birthday"


def birthdays_on(date, rows):
    """Return (name, kind) for every entry whose date falls on `date`."""
    names = []
    for i, row in enumerate(rows):
        if len(row) < 2 or not row[0].strip() or not row[1].strip():
            continue
        if i == 0 and row[0].strip().lower() in ("name", "friend", "names"):
            continue  # header row
        try:
            day, month = parse_day_month(row[1])
        except ValueError:
            print(f"Skipping row {i + 1}, could not read the date: {row}")
            continue
        # Feb 29 birthdays are celebrated on Feb 28 in non-leap years
        if (month, day) == (2, 29) and not is_leap(date.year):
            day = 28
        if (date.month, date.day) == (month, day):
            names.append((row[0].strip(), kind_of(row)))
    return names


def send_template(template_name, friend_name):
    if DRY_RUN:
        print(f"[DRY RUN] would send template '{template_name}' with name '{friend_name}'")
        return
    missing = [n for n, v in [("WHATSAPP_TOKEN", TOKEN), ("PHONE_NUMBER_ID", PHONE_NUMBER_ID), ("MY_NUMBER", MY_NUMBER)] if not v]
    if missing:
        sys.exit(f"Missing settings: {', '.join(missing)}. See SETUP.md.")
    url = f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": MY_NUMBER,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": "en"},
            "components": [
                {"type": "body", "parameters": [{"type": "text", "text": friend_name}]}
            ],
        },
    }
    resp = requests.post(
        url,
        json=payload,
        headers={"Authorization": f"Bearer {TOKEN}"},
        timeout=30,
    )
    if resp.status_code >= 300:
        print(f"WhatsApp rejected the message for {friend_name}: {resp.status_code} {resp.text}")
        sys.exit(1)
    print(f"Sent '{template_name}' for {friend_name}")


def main():
    now = now_ist()
    mode = pick_mode(now)
    date = target_date(now, mode)
    print(f"Now (IST): {now:%Y-%m-%d %H:%M} | mode: {mode} | checking birthdays on {date:%d-%m}")

    entries = birthdays_on(date, load_rows())
    if not entries:
        print("Nothing on this date, nothing to send.")
        return

    for name, kind in entries:
        send_template(TEMPLATES[(kind, mode)], name)


if __name__ == "__main__":
    main()
