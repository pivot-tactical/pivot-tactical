# PIVOT — Procedural Interactive Voice Operations Trainer (Tactical)

A self-hosted, **LAN-only** application that simulates VHF/UHF/HF radio voice
communications for training military and emergency-services personnel in voice
procedure, net discipline, prowords and tactical message formats (SITREP,
SALUTE, MEDEVAC, …) — **without real radio equipment or live spectrum**.

One person runs the **headless server** and acts as the **instructor**,
controlling the exercise from a **web browser** (protected by a password).
Trainees connect from any device with a browser over the local network — nothing
to install on either side. Radios tune freely across the whole HF/VHF/UHF range;
nets are *emergent* (anyone on the same frequency is on the same net, exactly
like real radios), and tuning upward audibly cleans up from noisy low HF to
near-clean UHF.

Licensed under **Apache-2.0**.

> **Status:** PIVOT is in active development. Prebuilt downloads are published on
> the [**Releases**](../../releases) page; until the first release is cut, that
> page may be empty.

---

## Quickstart

PIVOT is a single self-contained download — no installer, no dependencies, and
nothing for trainees to set up.

Downloads have stable, version-agnostic names, so the same link always fetches
the newest build (the version is in the release notes and shown in-app).

### Windows

1. Download the latest
   [`PIVOT-Tactical-win64.zip`](../../releases/latest/download/PIVOT-Tactical-win64.zip)
   (or the [`PIVOT-Tactical-Setup.exe`](../../releases/latest/download/PIVOT-Tactical-Setup.exe)
   installer for a Start-menu entry + automatic updates).
2. Unzip it anywhere (e.g. the desktop) and run **`PIVOT-Tactical.exe`**.

### Linux (x86_64)

Runs on common glibc-based distributions — Ubuntu 22.04+, Debian 12+, Fedora 36+,
Linux Mint 21+, Pop!_OS and similar.

1. Download the latest
   [`PIVOT-Tactical-linux-x86_64.tar.gz`](../../releases/latest/download/PIVOT-Tactical-linux-x86_64.tar.gz).
2. Extract and run it:

   ```bash
   tar -xzf PIVOT-Tactical-linux-x86_64.tar.gz
   ./PIVOT-Tactical/PIVOT-Tactical
   ```

   On a headless server (no desktop), start the server only with
   `./PIVOT-Tactical/PIVOT-Tactical --headless`.

### Then

* The server prints the **LAN address** (e.g. `https://192.168.1.20:8080`).
  Everyone — instructor and trainees — opens that address in a browser. The
  browser will warn that the connection isn't private — that's expected for a
  self-hosted address (PIVOT signs its own certificate, since there's no public
  CA for a LAN-only tool); choose **Advanced → Proceed** once. This is also what
  lets the browser grant microphone access — over a plain `http://` LAN address,
  Firefox (and, really, any browser) won't even prompt for it.
* Trainees enter a callsign. The instructor chooses **Log in as instructor** and
  enters the password (default `instructor` on first run — change it in Settings).

Your database, recordings and settings live in a data folder **next to** the
program and survive every update and rollback. To uninstall, just delete the
folder — no registry entries, no system services.

## Using PIVOT

**Instructor (browser, password-protected).** Start a session, then operate one
or more radios on chosen frequencies, key up with the **PTT** button (or the
spacebar), and toggle each radio between **Plain** and **Cypher**. A **running
log of events** shows every transmission live with its transcript and a play
button for the audio. Controls let you worsen or improve band conditions, inject
noise or jamming on a frequency or span, watch every connected terminal
(callsign, frequency, mode, status), kick a terminal, change settings, and change
the instructor password. Everything transmitted on the net is recorded and
transcribed.

**Trainees (browser).** Enter a callsign to join, tune the radio (type a
frequency or step up/down), choose Plain or Cypher, and **hold PTT / spacebar**
to transmit. A live ops-room clock shows the configured time zone. Trainees only
operate their own radio — no settings, no logs. Whether anyone hears you depends
on who else is tuned to your frequency and their crypto mode — just like a real
radio.

**After Action Review (AAR).** Open the AAR from the browser to replay a session:
a timeline of every transmission with timestamp, callsign, frequency, who copied
it, crypto mode and the transcribed text (low-confidence lines flagged amber).
Toggle **Clean/Dirty** to hear the raw voice or the radio-processed audio, and
**Plain/Cypher** to hear cypher transmissions as clear voice or as the encrypted
hash. Export a session as text, CSV, or a ZIP with all audio.

## Updating

PIVOT updates itself from this repository's GitHub Releases — an explicit,
out-of-band action that is **never** run during an exercise:

* **Check for updates** lists newer and older releases with their notes; update
  to the latest, update to a chosen release, or **downgrade** to an older one.
* **Roll back to previous** instantly swaps back to the last version you ran — no
  download, no internet — for fast recovery from a bad update.
* **Offline import** lets fully air-gapped sites apply a release package copied in
  on removable media. Every package is verified by SHA-256 before it is applied.
* **Channels.** Settings → Updates offers *Stable only* (default) or *Include
  prereleases*. Normal users stay on stable; testers can opt into the automatic
  `-dev.N` prerelease builds produced for each change (and turn on auto-update).

Updates only ever replace the program folder; your database and recordings live
outside it and are never touched.

## How it works

* **Per-listener audio.** A single transmission can sound different to different
  receivers on the same frequency — clear voice, an encrypted hash, or a garbled
  collision — so the server renders audio for each listener and delivers it over
  WebRTC straight to the browser.
* **Continuous band model.** One processing chain whose noise and fading vary
  continuously with frequency (worst at low HF, near-clean at UHF), plus a global
  atmospheric control and instructor jamming.
* **Per-radio crypto.** Plain/Cypher is a property of each radio, persists across
  retuning, and never auto-resets. A cypher-capable set decodes plain too; only a
  plain receiver hearing a cypher transmission gets the undecodable hash.

---

## Licensing & open-source compliance

PIVOT is released under the **Apache License 2.0** (see [`LICENSE`](LICENSE)). A
core project requirement is that the project licence does not contravene any
upstream dependency licence; the dependency licences ship with every download.

* [`NOTICE`](NOTICE) — attribution for Apache-2.0 and bundled components.
* [`THIRD-PARTY-LICENSES.md`](THIRD-PARTY-LICENSES.md) — full dependency licence
  inventory and the allow/deny policy (enforced in CI).
* [`REBUILD-LGPL.md`](REBUILD-LGPL.md) — how to substitute or rebuild the LGPL
  component, satisfying the relink obligation.

The server is headless (no Qt/PySide6). The only weak-copyleft component is
**libsndfile** (LGPL-2.1, via `soundfile`) — dynamically linked and replaceable.
No GPL/AGPL strong-copyleft library is linked into the distributed program.
PyInstaller (GPL with a linking exception) is a build tool only and is not
redistributed inside it. The audio engine is built entirely on numpy + scipy
(BSD), keeping the signal path fully permissive.
