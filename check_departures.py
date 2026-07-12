#!/usr/bin/env python3
"""
NZAA-bound departure watcher (early warning).

Checks all flights currently heading TO NZAA (arr_iata=AKL) and notifies
you the moment one that matches your "special" criteria shows status
"active" (already left its origin airport) - giving you roughly the
flight's full duration as lead time.

Notification logic (see nzaa_common.py / README):
  - Registration in known_specials.json -> never notified (you already
    expect it - it's a known regular).
  - Registration never seen before -> notified once ("first time seeing
    this aircraft"), then goes quiet on future visits.
  - Otherwise, uncommon aircraft type -> notified every time (so add the
    type to common_types.txt once you decide it's routine for NZAA).
  - Otherwise -> silent.

Data source: aviationstack (https://aviationstack.com). Free plan = 100
requests/month, so this is designed to run only a few times a day (see
.github/workflows/watch-departures.yml), NOT continuously.
"""

import os
import json
from datetime import datetime, timezone
import requests

from nzaa_common import (
    load_common_types,
    load_known_specials,
    load_always_notify,
    load_seen_registrations,
    save_seen_registrations,
    decide_notification,
)

ACCESS_KEY = os.environ.get("AVIATIONSTACK_KEY", "")
API_URL = "https://api.aviationstack.com/v1/flights"

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"

DAILY_STATE_FILE = "seen_departures.json"


def load_daily_state():
    if os.path.exists(DAILY_STATE_FILE):
        with open(DAILY_STATE_FILE) as f:
            return json.load(f)
    return {}


def save_daily_state(data):
    with open(DAILY_STATE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def fetch_inbound_flights():
    if not ACCESS_KEY:
        print("ERROR: AVIATIONSTACK_KEY is not set. Skipping this run.")
        return []
    params = {"access_key": ACCESS_KEY, "arr_iata": "AKL", "limit": 100}
    resp = requests.get(API_URL, params=params, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    if "error" in payload:
        print(f"aviationstack API error: {payload['error']}")
        return []
    return payload.get("data", [])


def send_notification(title, message):
    if not NTFY_TOPIC:
        print(f"[NO NTFY_TOPIC SET] Would have notified: {title} - {message}")
        return
    try:
        requests.post(
            NTFY_URL,
            data=message.encode("utf-8"),
            headers={
                "Title": title.encode("utf-8"),
                "Priority": "high",
                "Tags": "airplane,departure",
            },
            timeout=15,
        )
    except requests.RequestException as e:
        print(f"Failed to send notification: {e}")


def main():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    daily_seen = load_daily_state()
    daily_seen = {today: daily_seen.get(today, [])}  # drop older days

    common_types = load_common_types()
    known_specials = load_known_specials()
    always_notify = load_always_notify()
    seen_registrations = load_seen_registrations()

    flights = fetch_inbound_flights()
    print(f"[diagnostic] aviationstack returned {len(flights)} total flights inbound to AKL this run.")

    active_count = 0
    no_registration_count = 0
    already_seen_count = 0
    checked_count = 0

    for f in flights:
        status = f.get("flight_status")
        flight_num = (f.get("flight") or {}).get("iata") or (f.get("flight") or {}).get("icao") or "unknown flight"

        if status != "active":
            continue
        active_count += 1

        if flight_num in daily_seen[today]:
            already_seen_count += 1
            continue

        aircraft = f.get("aircraft") or {}
        registration = aircraft.get("registration") or ""
        typecode = (aircraft.get("icao") or "").upper()

        dep = f.get("departure") or {}
        dep_airport = dep.get("airport") or dep.get("iata") or "unknown origin"
        dep_actual = dep.get("actual") or dep.get("estimated") or dep.get("scheduled")

        arr = f.get("arrival") or {}
        eta = arr.get("estimated") or arr.get("scheduled")

        airline = (f.get("airline") or {}).get("name") or "unknown operator"

        if registration:  # only judge flights where we actually know the tail
            checked_count += 1
            should_notify, reason = decide_notification(
                registration, typecode, common_types, known_specials, seen_registrations, today,
                always_notify=always_notify,
            )
            if should_notify:
                title = f"New/unexpected plane heading to NZAA: {registration}"
                message = (
                    f"{airline} {flight_num} ({typecode or 'type unknown'})\n"
                    f"Reg: {registration}\n"
                    f"From: {dep_airport}  Departed: {dep_actual}\n"
                    f"ETA Auckland: {eta}\n"
                    f"Why flagged: {reason}"
                )
                print(f"NOTIFY -> {title} | {message}")
                send_notification(title, message)
        else:
            no_registration_count += 1

        daily_seen[today].append(flight_num)

    print(f"[diagnostic] active(departed)={active_count}, already logged today={already_seen_count}, "
          f"missing registration data={no_registration_count}, newly judged this run={checked_count}")

    save_daily_state(daily_seen)
    save_seen_registrations(seen_registrations)


if __name__ == "__main__":
    main()
