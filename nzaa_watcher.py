#!/usr/bin/env python3
"""
NZAA (Auckland Airport) arrival/landing confirmation watcher.

Polls OpenSky for aircraft on the ground inside a box around NZAA, and
applies the same notification logic as check_departures.py (see
nzaa_common.py / README):
  - Registration in known_specials.json -> never notified (already a
    known regular).
  - Registration never seen before -> notified once ("first time seeing
    this aircraft").
  - Otherwise, uncommon aircraft type -> notified every time.
  - Otherwise -> silent.
"""

import os
import json
import sys
from datetime import datetime, timezone
import requests

from nzaa_common import (
    load_common_types,
    load_known_specials,
    load_seen_registrations,
    save_seen_registrations,
    decide_notification,
)

LAMIN = -37.16
LAMAX = -36.86
LOMIN = 174.62
LOMAX = 174.96

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"

DAILY_STATE_FILE = "seen.json"

OPENSKY_STATES_URL = "https://opensky-network.org/api/states/all"
OPENSKY_METADATA_URL = "https://opensky-network.org/api/metadata/aircraft/icao/{icao24}"

IDX_ICAO24 = 0
IDX_CALLSIGN = 1
IDX_ON_GROUND = 8


def load_daily_state():
    if os.path.exists(DAILY_STATE_FILE):
        with open(DAILY_STATE_FILE) as f:
            return json.load(f)
    return {}


def save_daily_state(data):
    with open(DAILY_STATE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def fetch_states():
    params = {"lamin": LAMIN, "lamax": LAMAX, "lomin": LOMIN, "lomax": LOMAX}
    resp = requests.get(OPENSKY_STATES_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("states") or []


def fetch_metadata(icao24):
    try:
        resp = requests.get(OPENSKY_METADATA_URL.format(icao24=icao24), timeout=15)
        if resp.status_code == 200:
            return resp.json()
    except requests.RequestException:
        pass
    return {}


def send_notification(title, message):
    if not NTFY_TOPIC:
        print(f"[NO NTFY_TOPIC SET] Would have notified: {title} - {message}")
        return
    try:
        requests.post(
            NTFY_URL,
            data=message.encode("utf-8"),
            headers={"Title": title.encode("utf-8"), "Priority": "default", "Tags": "airplane"},
            timeout=15,
        )
    except requests.RequestException as e:
        print(f"Failed to send notification: {e}")


def main():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    daily_seen = load_daily_state()
    daily_seen = {today: daily_seen.get(today, [])}

    common_types = load_common_types()
    known_specials = load_known_specials()
    seen_registrations = load_seen_registrations()

    try:
        states = fetch_states()
    except requests.RequestException as e:
        print(f"Error fetching states from OpenSky: {e}")
        sys.exit(0)

    for s in states:
        icao24 = (s[IDX_ICAO24] or "").strip().lower()
        on_ground = s[IDX_ON_GROUND]
        if not icao24 or not on_ground:
            continue
        if icao24 in daily_seen[today]:
            continue

        callsign = (s[IDX_CALLSIGN] or "").strip() or "unknown callsign"
        meta = fetch_metadata(icao24)
        registration = meta.get("registration") or ""
        typecode = (meta.get("typecode") or "").upper()
        operator = meta.get("operator") or meta.get("owner") or "unknown operator"
        model = meta.get("model") or typecode or "unknown type"

        if registration:
            should_notify, reason = decide_notification(
                registration, typecode, common_types, known_specials, seen_registrations, today
            )
            if should_notify:
                title = f"New/unexpected plane at NZAA: {registration}"
                message = (
                    f"{operator} {model}\n"
                    f"Reg: {registration}  Callsign: {callsign}\n"
                    f"Why flagged: {reason}"
                )
                print(f"NOTIFY -> {title} | {message}")
                send_notification(title, message)

        daily_seen[today].append(icao24)

    save_daily_state(daily_seen)
    save_seen_registrations(seen_registrations)


if __name__ == "__main__":
    main()
