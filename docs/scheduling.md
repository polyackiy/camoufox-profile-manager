# Scheduling

The app can run two things against a profile on a schedule, from the
**Schedules** screen or the [REST API](api.md#schedules). The scheduler lives
inside the `camoufox-pm` process — no cron, no extra service — because that
process owns the browser sessions: a scheduled launch goes through the same
session manager as pressing *Open*, so the running-browsers list, the
close-on-window-close handling and the usage log all behave identically.

## What can be scheduled

- **Open the browser.** For account warming or any regular session. If the
  profile's browser is already running when the schedule fires, the run is
  recorded as *skipped* and nothing is touched. An optional *close after N
  minutes* ends the session by itself, so a 03:00 warming run does not leave a
  window open until someone notices.
- **Refresh the browser version.** A pinned fingerprint never ages on its own,
  so a long-lived profile keeps advertising the Firefox release it was created
  with. This moves the pin onto the browser currently installed — the same
  action as the *Update* button — changing **only** the version. The screen,
  GPU, cores, fonts and noise seeds stay. That is exactly what a real machine
  looks like after its browser updates, which real machines do on their own
  schedule; doing it automatically is the point.

## What deliberately cannot be scheduled

**Regenerating the hardware fingerprint.** The pinned machine exists so that a
profile is *the same computer* every session — that guarantee is the core of
this product, proven with real-browser tests (see
[profile-settings.md](profile-settings.md)). Rotating the hardware on a timer
would undo it: a site that has seen one GPU, one screen and one core count for
months would suddenly see another at 3am, which is precisely the signal a
long-lived account must never emit. "Automated fingerprint rotation" made
sense before pinning existed, when the fingerprint changed every launch anyway;
now it would be an automated way to look suspicious.

Regenerating stays available as a deliberate, manual action — *Regenerate
fingerprint* on the profile, which warns about what it costs.

## How schedules fire

- **Two expressions.** *Every N minutes*, or *daily at HH:MM* on chosen
  weekdays. Not cron: the two forms cover warming and maintenance, they can be
  displayed back unambiguously, and there is no syntax to get wrong.
- **Whose clock?** The server's — the machine running `camoufox-pm`. In the
  normal single-machine setup that is also your clock. If you run the server
  remotely, "09:00" is 09:00 *there*.
- **Missed runs are skipped, not replayed.** If the app was closed when a run
  was due, the gap is recorded in the run history as one *missed* entry and the
  schedule waits for its next occurrence. A warming launch is only meaningful
  at its time, and replaying a night's backlog would open every missed browser
  at once on startup.
- **Failures don't stop the scheduler.** Each run is recorded — *ok*,
  *skipped*, *error* or *missed*, with a message — and one failing schedule
  never blocks another. A schedule whose profile has been deleted disables
  itself. The last 20 runs are kept per schedule.
