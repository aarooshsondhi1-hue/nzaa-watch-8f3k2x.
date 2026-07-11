# NZAA Special Plane Watcher

Sends a push notification to your phone when a genuinely unexpected
aircraft is heading to or lands at Auckland Airport (NZAA) - while
staying quiet about the special-livery aircraft you already know and
expect to see regularly. Runs for free on GitHub Actions - no APK, no
server of your own required.

## How notification decisions work

Every aircraft is checked in this order:

1. **Registration is in `known_specials.json`** -> never notified.
   This is your "already know about it, don't bug me" list.
2. **Registration has never been seen before** (not yet in
   `seen_registrations.json`) -> notified once, as "first time seeing
   this aircraft". After this it's on record and won't fire again for
   that reason.
3. **Aircraft type isn't in `common_types.txt`** -> notified every time
   this happens, regardless of registration history. This is
   independent of step 2 - a common tail flying an uncommon type still
   flags.
4. Otherwise -> silent. An ordinary, already-seen, common-type visitor.

### The one thing this can't do

Flight-tracking data has no concept of "livery" - only registration and
aircraft type. So there's no way to automatically detect "this specific
tail just got repainted into something special" - only a human (spotter
forums, airline announcements, aviation news) can tell you that. Once
you learn of a new special livery, add its registration to
`known_specials.json` yourself (or leave it out if you *want* to keep
being notified whenever it flies in - see "Customizing" below).

## Two scripts, two purposes

| | `check_departures.py` (early warning) | `nzaa_watcher.py` (arrival) |
|---|---|---|
| Notifies when | The flight leaves its **origin** airport | The plane is **on the ground at NZAA** |
| Lead time | Roughly the flight's full duration | ~Zero - it's already there |
| Data source | aviationstack (free tier: 100 req/month) | OpenSky Network (free, no key needed) |
| Check frequency | 3x/day (fits the free quota) | Every 10 min |

Both use the exact same decision logic and share `common_types.txt`,
`known_specials.json`, and `seen_registrations.json`.

## Why not a real Android app / APK?

A custom APK would need to be built, signed, and you'd have to sideload
it (Android blocks unknown-source installs by default) or publish it to
the Play Store, which requires a developer account. This setup gets you
the same result - a phone notification - using two free pieces: **ntfy**
(push notifications) and **GitHub Actions** (the scheduled "server").

## Setup (about 15 minutes)

### 1. Get notifications on your phone (ntfy)
1. Install the **ntfy** app: Google Play or F-Droid (`io.heckel.ntfy`).
2. Subscribe to a topic - pick a random, hard-to-guess name (anyone who
   knows the topic name can see/send to it), e.g. `nzaa-watch-8f3k2x`.

### 2. Get a free aviationstack API key (for the departure/early-warning script)
1. Sign up at aviationstack.com/signup/free (no credit card).
2. Copy your **Access Key** from the dashboard.
3. Note: free plan = **100 requests/month**, which is why the departure
   check only runs 3x/day.

### 3. Put this project on GitHub
1. Create a new (private is fine) repository.
2. Upload all files in this folder to it.

### 4. Add secrets
Repo -> **Settings -> Secrets and variables -> Actions -> New repository secret**:
- `NTFY_TOPIC` - your ntfy topic name
- `AVIATIONSTACK_KEY` - your aviationstack access key

### 5. Turn it on
Both workflows run automatically once they're in the repo. Trigger a
one-off test any time: repo -> **Actions** tab -> pick the workflow ->
**Run workflow**.

## Customizing

- **`common_types.txt`** - ICAO type codes treated as routine NZAA
  traffic. Anything NOT in this list gets flagged as an uncommon type,
  every time it shows up. Seeded with common Air NZ + regular
  international widebodies (including A350, since Cathay Pacific flies
  these to Auckland regularly - only specific *tails* of theirs are
  special, not the type itself).
- **`known_specials.json`** - registrations to suppress (never notify).
  Seeded with a few confirmed Air NZ special liveries as of July 2026:
  - `ZK-NZE` - 787-9, all-black "Fern Mark" livery
  - `ZK-OYB` - A321neo, all-black "Black Beauty" Star Alliance livery
  - `ZK-OAB` - A320, "Black Beauty"/All Blacks livery
  - `ZK-OJH` - A320, Star Alliance livery

  This is not exhaustive and liveries get repainted - verify and expand
  it yourself using spotter sites (planespotters.net, flightradar24) or
  aviation news. **If you'd rather keep getting notified every time a
  known special (like a particular foreign carrier's anniversary-livery
  jet) visits, instead of going quiet after the first sighting, just
  leave that registration out of this file** - it'll notify once on
  first sighting and then go quiet, OR if its aircraft type is also
  uncommon for NZAA, it'll keep notifying on type grounds alone.
- **`seen_registrations.json`** - auto-generated and grows over time;
  don't edit by hand. This is what step 2 above checks.

## Limitations

**`check_departures.py`:**
- Coarse timing: only checks 3x/day by default (free-tier quota), so
  "departed" could be detected up to ~8 hours after it happened.
  Usually fine for long-haul, less useful for short domestic hops.
  Tighten it with a paid aviationstack plan + shorter cron interval.
- Aircraft registration/type isn't always populated on the free tier;
  flights missing it are skipped rather than guessed at.

**`nzaa_watcher.py`:**
- OpenSky coverage around Auckland depends on volunteer ADS-B
  receivers.
- "Arrival" = detected on the ground inside a box around NZAA - a
  proxy, since there's no direct "just landed" event.

## Files
- `check_departures.py` / `nzaa_watcher.py` - the two watcher scripts
- `nzaa_common.py` - shared notification decision logic
- `common_types.txt` - shared "routine aircraft type" allowlist
- `known_specials.json` - shared "known regular, suppress" list
- `seen_registrations.json` - auto-generated, persists forever
- `seen_departures.json` / `seen.json` - auto-generated, reset daily
- `.github/workflows/watch-departures.yml` / `watch.yml` - the schedules
