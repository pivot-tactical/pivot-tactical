# Releasing PIVOT & the auto-update chain

PIVOT updates itself on Windows through a **signed Inno Setup installer**
delivered by **WinSparkle** (the auto-update engine). This document covers the
one-off key setup and the per-release flow. Everything is permissively licensed
(WinSparkle and the installer tooling are MIT / BSD-class — see
`THIRD-PARTY-LICENSES.md`).

## How the update chain works

1. The release workflow builds the app (PyInstaller `--onedir`), drops
   `WinSparkle.dll` beside the binary, and wraps it in an Inno Setup installer
   (`PIVOT-Tactical-Setup.exe`). Asset names are version-agnostic so the
   `releases/latest/download/` URL is stable; the version lives in the tag, the
   release notes and the embedded build-info.
2. The installer is **signed** with the project's Ed25519 private key, and an
   `appcast.xml` feed is generated carrying that signature
   (`packaging/sign_appcast.py`).
3. Both are published to the GitHub Release. WinSparkle polls the stable URL
   `https://github.com/<owner>/<repo>/releases/latest/download/appcast.xml`.
4. In the running app, the instructor's **Settings → Updates** panel asks the
   server to check; on Windows it hands off to WinSparkle, which downloads the
   installer, **verifies the Ed25519 signature** against the public key embedded
   in the app, runs it (closing PIVOT, swapping the install on disk), and
   relaunches PIVOT. This is why a directly-run `.exe` could never self-update —
   the swap must happen while the app is stopped.

On Linux there is no WinSparkle; the equivalent chain is a **systemd service** +
a **SHA-256-verified download → staged swap on restart**:

1. The release tarball ships `install.sh`, `uninstall.sh` and
   `pivot-tactical.service` inside the bundle. `sudo ./install.sh` installs to
   `/opt/pivot-tactical`, runs it headless as the `pivot` user, and stores data
   under `/var/lib/pivot-tactical`.
2. In-app "Install" downloads the new tarball, verifies its SHA-256, extracts it
   to the versions store and writes a pending-update marker.
3. The service's `ExecStartPre=… --apply-staged` swaps the staged build into
   `/opt/pivot-tactical` **before** the new binary starts (a running executable's
   file can be replaced safely on Linux), retaining the old version for rollback.
   This mirrors WinSparkle's close → swap → relaunch on Windows. The swap applies
   on the next `systemctl restart pivot-tactical`.

## Headless presentation (tray / service)

The server has no desktop GUI — everyone uses a browser. To keep it out of the
way:

- **Windows:** the packaged app hides its console and shows a **system-tray
  icon** (`server/pivot/win_tray.py`, pure ctypes — no extra dependency). The
  tray menu offers Open PIVOT / Copy LAN address / Show log / Quit. Force the
  console with `--no-tray`; force the tray with `--tray`.
- **Linux:** it runs as a background **systemd service** (logs to the journal:
  `journalctl -u pivot-tactical -f`) — the idiomatic "tucked away headless"
  equivalent.

## One-off: mint the signing key

```bash
python packaging/sign_appcast.py keygen
```

This prints two base64 strings:

- **Private key** → store as the GitHub Actions secret `PIVOT_EDDSA_PRIVATE_KEY`
  (Settings → Secrets and variables → Actions). **Never commit it.**
- **Public key** → paste into `server/pivot/updates/signing.py` as
  `_EMBEDDED_PUBLIC_KEY` (or set `PIVOT_EDDSA_PUBLIC_KEY` at runtime for staging).

Until the public key is set, the Windows in-app updater reports itself as
unavailable and the app simply offers no auto-update (no breakage).

## Cutting a release

```bash
git tag v1.2.0
git push origin v1.2.0
```

The `Release` workflow then, per platform:

- builds the bundle and the `.zip` / `.tar.gz` + `.sha256` (unchanged);
- **(Windows)** adds `WinSparkle.dll`, builds the Inno Setup installer, and — if
  `PIVOT_EDDSA_PRIVATE_KEY` is configured — signs it and emits `appcast.xml`;
- publishes all assets to the GitHub Release.

A `workflow_dispatch` run builds and uploads artifacts without publishing, so you
can validate the installer without cutting a release. Without the secret, the
installer is still built; only the signature/appcast are skipped.

## Security: where the keys live (and what is *never* committed)

There are **two independent signatures**, and **no private key is ever stored in
the repository**. Private keys live only in GitHub Actions **encrypted secrets**
(injected as env vars at build time, masked in logs); only public material ships
in the app or the repo.

| Material | Purpose | Stored as | In the repo? |
| --- | --- | --- | --- |
| **Ed25519 private key** | Signs the appcast/installer the in-app updater trusts | Secret `PIVOT_EDDSA_PRIVATE_KEY` | **Never** |
| **Ed25519 public key** | Verifies that signature inside the running app | `server/pivot/updates/signing.py` (`_EMBEDDED_PUBLIC_KEY`) | Yes — public by design |
| **Authenticode cert (.pfx)** | OS-trust so SmartScreen/UAC don't warn | Secret `WINDOWS_CERTIFICATE` (base64) + `WINDOWS_CERTIFICATE_PASSWORD` | **Never** |

This is the standard model for CI signing: the secret store is the vault, the
build reads it at run time, and the repo only ever carries the *public* half. If
either secret is absent the workflow still succeeds — it just skips that
signature (the Ed25519 step is skipped; the installer is left unsigned). So the
project builds and runs for contributors without access to the keys, and only an
official release run (with the secrets configured) produces fully-signed assets.

To rotate a key: mint a new one, update the GitHub secret, and (for Ed25519)
update `_EMBEDDED_PUBLIC_KEY` and ship an app build carrying the new public key
before retiring the old key.

## One-off: configure Authenticode (optional, recommended for public distribution)

For internal/LAN use this is optional. To enable it, take your code-signing
`.pfx` and add two repository secrets:

```bash
# base64-encode the certificate for safe storage as a secret
base64 -w0 your-cert.pfx        # -> paste as WINDOWS_CERTIFICATE
```

- `WINDOWS_CERTIFICATE` — base64 of the `.pfx`
- `WINDOWS_CERTIFICATE_PASSWORD` — its export password

The release workflow then signs `PIVOT-Tactical-Setup.exe` with
`signtool` (SHA-256 + RFC-3161 timestamp) **before** computing the Ed25519
appcast signature, so both signatures are valid on the published file.

## Notes

- **Signing order matters:** Authenticode runs first (it rewrites the `.exe`),
  then the Ed25519 appcast signature is computed over the signed bytes.
- **AppId:** the GUID in `packaging/pivot.iss` is the upgrade identity — keep it
  constant forever so installers upgrade in place.
- **WinSparkle version** is pinned in `release.yml`; bump it there to update.
