#!/usr/bin/env python3
"""
NZAA (Auckland Airport) arrival/landing confirmation watcher.

Uses airplanes.live's free public API (no key needed, and unlike OpenSky
it doesn't block cloud-hosted callers like GitHub Actions runners).
It also returns registration + aircraft type directly per aircraft, so
no separate metadata lookup call is needed.

Two independent detections happen each run:

1. CONFIRMED arrival - an aircraft counts as "arrived" if EITHER:
   - alt_baro reports "ground" (airplanes.live's on-ground indicator), OR
   - its barometric altitude is below LOW_ALTITUDE_FT (catches short
     final / just touched down, in case the ground flag lags).

2. POSSIBLE/unconfirmed inbound - for aircraft that are still airborne
   (not yet counted as arrived) but whose current heading points roughly
   at NZAA and whose ETA at current ground speed is under
   POSSIBLE_ETA_MAX_MINUTES. This is a geometry-based guess, not a real
   destination field - flights merely transiting the area, or actually
   headed to a nearby airport (Ardmore, Whenuapai, Hamilton), can
   occasionally match by coincidence. That's why it's always labeled
   "possible/unconfirmed" rather than treated as certain - use it as an
   early heads-up, with the CONFIRMED notification (or its absence) as
   the real answer once it either lands or doesn't.

Notification logic (who counts as "interesting" in the first place) is
shared with check_departures.py - see nzaa_common.py.
"""

import os
import json
import sys
import math
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

# NZAA (Auckland Airport) coordinates
NZAA_LAT = -37.008
NZAA_LON = 174.792

# Wide net so we catch aircraft still cruising well outside NZAA's
# immediate vicinity - needed for the "possible inbound" check below.
# airplanes.live's point-search caps out around 250nm, so this stays
# safely under that.
FETCH_RADIUS_NM = 220

LOW_ALTITUDE_FT = 500  # catches "on short final / just touched down"

# Possible/unconfirmed inbound detection settings.
POSSIBLE_ETA_MAX_MINUTES = 40
HEADING_TOLERANCE_DEG = 15  # how closely track must point at NZAA
MIN_GROUND_SPEED_KT = 50   # ignore near-stationary/taxiing-speed reports

# ADS-B emitter categories to actually consider. Excludes:
#   A1 - light GA (Cessnas etc.), B* - gliders/UAVs/balloons,
#   C* - surface vehicles (tugs, follow-me cars, tower ops vehicles - not aircraft at all)
# NOTE: A0 (no category info) is deliberately INCLUDED, not excluded -
# military aircraft frequently report A0 (or omit category entirely)
# instead of a proper class code, and those are specifically what you're
# after here. An aircraft with no category field at all also already
# passes through fine (see the check below), this just makes sure an
# aircraft that explicitly reports "A0" isn't wrongly treated the same
# as a ground vehicle or light GA plane.
RELEVANT_CATEGORIES = {"A0", "A2", "A3", "A4", "A5", "A6", "A7"}

# Known junk identifiers seen in practice (ground vehicles/tower ops that
# reported no ADS-B category at all, so the category filter above didn't
# catch them). Backup blocklist, checked on registration OR callsign.
JUNK_IDENTIFIERS = {"TWR", "GND", "FOLLOWME", "CAR"}

# Reject position data older than this for the POSSIBLE/unconfirmed
# check specifically - a stale or ghost position report (seen once, in
# testing, for an aircraft that was actually on the other side of the
# world) can otherwise produce a confident-looking but wrong "heading to
# NZAA" calculation. Confirmed on-ground detection doesn't use this,
# since it isn't as sensitive to a slightly-stale position.
MAX_POSITION_AGE_SECONDS = 60

# Optional: used to confirm a "possible" candidate's REAL destination via
# AirLabs' schedule data (same source as check_departures.py), instead of
# relying purely on the heading/speed guess. Shares AirLabs' 1,000
# req/month quota with the departures script - if that quota runs out,
# this just fails gracefully and falls back to the geometry-only guess.
AIRLABS_KEY = os.environ.get("AIRLABS_KEY", "")
AIRLABS_FLIGHTS_URL = "https://airlabs.co/api/v9/flights"

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"

# Custom notification icon (shown instead of ntfy's default logo).
NTFY_ICON_URL = "https://raw.githubusercontent.com/aarooshsondhi1-hue/nzaa-watch-8f3k2x./main/file_0000000034a07207b3999d5a6465d8bc.png"

DAILY_STATE_FILE = "seen.json"
POSSIBLE_STATE_FILE = "possible_seen.json"

AIRPLANES_LIVE_URL = f"https://api.airplanes.live/v2/point/{NZAA_LAT}/{NZAA_LON}/{FETCH_RADIUS_NM}"


def load_state_file(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def save_state_file(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def fetch_aircraft():
    resp = requests.get(AIRPLANES_LIVE_URL, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("ac") or []


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
                "Priority": "default",
                "Tags": "airplane",
                "Icon": NTFY_ICON_URL,
            },
            timeout=15,
        )
    except requests.RequestException as e:
        print(f"Failed to send notification: {e}")


def lookup_real_destination(callsign):
    """
    Best-effort check of a flight's actual published destination via
    AirLabs, keyed by ICAO callsign (e.g. "ANZ424"). Returns:
      - "AKL" confirmed  -> dict with the flight's real data
      - a different arrival airport confirmed -> that IATA code (string)
      - nothing found / lookup failed -> None (caller should fall back
        to the geometry-only "possible" guess)
    Only called for candidates that already passed the geometry filter,
    to keep AirLabs usage low.
    """
    if not AIRLABS_KEY or not callsign:
        return None
    try:
        resp = requests.get(
            AIRLABS_FLIGHTS_URL,
            params={"api_key": AIRLABS_KEY, "flight_icao": callsign},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
        results = payload.get("response") if isinstance(payload, dict) else payload
        if not results:
            return None
        flight = results[0]
        arr_iata = (flight.get("arr_iata") or "").upper()
        if arr_iata == "AKL":
            return {"confirmed": True, "arr_iata": arr_iata, "flight": flight}
        elif arr_iata:
            return {"confirmed": False, "arr_iata": arr_iata, "flight": flight}
        return None
    except requests.RequestException:
        return None


def haversine_nm(lat1, lon1, lat2, lon2):
    r_nm = 3440.065
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r_nm * c


def bearing_deg(lat1, lon1, lat2, lon2):
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    x = math.sin(dlambda) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def heading_diff_deg(a, b):
    d = abs(a - b) % 360
    return min(d, 360 - d)


def main():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    daily_seen = load_state_file(DAILY_STATE_FILE)
    daily_seen = {today: daily_seen.get(today, [])}
    possible_seen = load_state_file(POSSIBLE_STATE_FILE)
    possible_seen = {today: possible_seen.get(today, [])}

    common_types = load_common_types()
    known_specials = load_known_specials()
    always_notify = load_always_notify()
    seen_registrations = load_seen_registrations()

    try:
        aircraft_list = fetch_aircraft()
    except requests.RequestException as e:
        print(f"Error fetching data from airplanes.live: {e}")
        sys.exit(0)

    print(f"[diagnostic] airplanes.live returned {len(aircraft_list)} aircraft total within {FETCH_RADIUS_NM}nm of NZAA this run.")

    ground_count = 0
    low_alt_count = 0
    checked_count = 0
    skipped_category_count = 0
    possible_checked_count = 0
    stale_position_count = 0

    for ac in aircraft_list:
        hex_id = (ac.get("hex") or "").strip().lower()
        alt_baro = ac.get("alt_baro")
        category = (ac.get("category") or "").strip().upper()

        is_on_ground = alt_baro == "ground"
        is_low_alt = isinstance(alt_baro, (int, float)) and alt_baro < LOW_ALTITUDE_FT
        is_confirmed_arrival = is_on_ground or is_low_alt

        if is_on_ground:
            ground_count += 1
        if is_low_alt:
            low_alt_count += 1

        if not hex_id:
            continue

        # Skip ground vehicles / light GA / gliders / unknowns for BOTH
        # detections - not what you're after, and this is what was
        # causing "TWR" and Cessna false positives before.
        if category and category not in RELEVANT_CATEGORIES:
            if is_confirmed_arrival:
                skipped_category_count += 1
            continue

        callsign = (ac.get("flight") or "").strip() or "unknown callsign"
        registration = (ac.get("r") or "").strip()
        typecode = (ac.get("t") or "").strip().upper()
        operator = ac.get("ownOp") or "unknown operator"
        model = ac.get("desc") or typecode or "unknown type"

        # Backup filter for ground vehicles that don't report a category
        # at all (so the category check above wouldn't catch them) -
        # this is specifically what caused the "TWR" false positives.
        if registration.upper() in JUNK_IDENTIFIERS or callsign.upper() in JUNK_IDENTIFIERS:
            continue

        # --- CONFIRMED arrival detection (unchanged behavior) ---
        if is_confirmed_arrival:
            if hex_id in daily_seen[today]:
                continue
            checked_count += 1
            # Registration may be blank (common for military aircraft,
            # which often withhold it deliberately) - decide_notification
            # still works with an empty registration, since the rare-type
            # check only depends on typecode. Only the ZK-suppression,
            # always-notify, and known-specials/first-seen checks need an
            # actual registration, and simply won't match an empty one.
            should_notify, reason = decide_notification(
                registration, typecode, common_types, known_specials, seen_registrations, today,
                always_notify=always_notify,
            )
            if should_notify:
                display_id = registration or f"hex {hex_id}"
                title = f"New/unexpected plane at NZAA: {display_id}"
                message = (
                    f"{operator} {model}\n"
                    f"Reg: {registration or '(not broadcast - possibly military)'}  Callsign: {callsign}\n"
                    f"Why flagged: {reason}"
                )
                print(f"NOTIFY -> {title} | {message}")
                send_notification(title, message)
            daily_seen[today].append(hex_id)
            continue

        # --- POSSIBLE/unconfirmed inbound detection ---
        lat, lon = ac.get("lat"), ac.get("lon")
        gs = ac.get("gs")
        track = ac.get("track")
        if lat is None or lon is None or gs is None or track is None:
            continue
        if gs < MIN_GROUND_SPEED_KT:
            continue
        if hex_id in possible_seen[today]:
            continue

        # Guard against stale/ghost position reports - airplanes.live has
        # been observed (in testing) to occasionally serve a position
        # that doesn't match the aircraft's actual real-world location.
        # "seen_pos" (seconds since the last position update) lets us
        # discard anything that isn't genuinely fresh. This can't catch
        # every bad record (a mislabeled but freshly-updated ghost would
        # still slip through), but it removes the most common case.
        seen_pos = ac.get("seen_pos")
        if seen_pos is not None and seen_pos > MAX_POSITION_AGE_SECONDS:
            stale_position_count += 1
            continue

        distance_nm = haversine_nm(lat, lon, NZAA_LAT, NZAA_LON)
        bearing_to_nzaa = bearing_deg(lat, lon, NZAA_LAT, NZAA_LON)
        diff = heading_diff_deg(track, bearing_to_nzaa)
        eta_minutes = (distance_nm / gs) * 60

        if diff > HEADING_TOLERANCE_DEG or eta_minutes > POSSIBLE_ETA_MAX_MINUTES:
            continue

        possible_checked_count += 1
        should_notify, reason = decide_notification(
            registration, typecode, common_types, known_specials, seen_registrations, today,
            always_notify=always_notify,
        )
        if should_notify:
            destination_check = lookup_real_destination(callsign)
            display_id = registration or f"hex {hex_id}"

            if destination_check is not None and destination_check["confirmed"] is False:
                # We now know for certain it's headed somewhere else -
                # this was a false positive from the geometry guess alone,
                # so don't notify at all.
                possible_seen[today].append(hex_id)
                continue

            if destination_check is not None and destination_check["confirmed"] is True:
                title = f"Heading to NZAA (confirmed via schedule): {display_id}"
                footer = "(Confirmed against a published flight schedule, not just a heading/speed guess.)"
            else:
                title = f"POSSIBLE/unconfirmed - {display_id} may be heading to NZAA"
                footer = (
                    "(No published schedule found for this flight - early positional "
                    "estimate based on live heading/speed only. Could be transiting the "
                    "area or headed to a nearby airport instead of NZAA.)"
                )

            message = (
                f"{operator} {model}\n"
                f"Reg: {registration or '(not broadcast - possibly military)'}  Callsign: {callsign}\n"
                f"~{distance_nm:.0f}nm out, ETA ~{eta_minutes:.0f} min if this heading holds\n"
                f"Why flagged: {reason}\n"
                f"{footer}"
            )
            print(f"POSSIBLE-NOTIFY -> {title} | {message}")
            send_notification(title, message)
        possible_seen[today].append(hex_id)

    print(f"[diagnostic] on_ground={ground_count}, low_altitude(<{LOW_ALTITUDE_FT}ft)={low_alt_count}, "
          f"skipped as ground-vehicle/light-GA={skipped_category_count}, rejected as stale position={stale_position_count}, "
          f"newly checked (confirmed)={checked_count}, newly checked (possible)={possible_checked_count}")

    save_state_file(DAILY_STATE_FILE, daily_seen)
    save_state_file(POSSIBLE_STATE_FILE, possible_seen)
    save_seen_registrations(seen_registrations)


if __name__ == "__main__":
    main()

