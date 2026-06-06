# Releasing PIVOT & the auto-update chain

PIVOT updates itself through **one** mechanism on every platform and channel: the
app asks GitHub for releases, picks the newest one **on the selected channel that
is newer than what's running**, downloads its archive, **verifies it (SHA-256 +
Ed25519 signature)**, stages it, and applies the swap on the next restart. Stable
builds and dev prereleases are produced by the same workflow and update exactly
the same way — so you can switch channels on the fly (only ever *upgrading*).
Everything is permissively licensed (BSD/MIT-class — see `THIRD-PARTY-LICENSES.md`).

## How the update chain works

1. The release workflow builds the app (PyInstaller `--onedir`), packages a
   portable archive (`PIVOT-Tactical-win64.zip` / `…-linux-x86_64.tar.gz`), and
   on Windows also wraps it in an Inno Setup installer (`PIVOT-Tactical-Setup.exe`)
   for a Start-menu/uninstaller first-install. Asset names are **version-agnostic**
   so `releases/latest/download/<name>` is a stable URL; the version lives in the
   tag, the release notes and the embedded build-info.
2. Every published asset gets a **`.sha256`** (integrity) and a **`.sig`**
   (base64 Ed25519 signature) sidecar, signed with the project's private key
   (`packaging/sign_appcast.py sign`).
3. In the running app, **Settings → Updates** shows the channel-filtered list.
   Choosing **Download & install** (or auto-update) downloads the archive,
   verifies its SHA-256 **and** its Ed25519 signature against the public key
   embedded in the app, extracts it to the versions store, and writes a pending
   marker.
4. The staged build is swapped into place on the next **restart** — which the
   instructor can trigger from the browser (**Restart server / Restart now to
   apply**). The swap happens while the app is stopped, so a running executable
   replaces itself cleanly:
   - **Linux:** the systemd unit's `ExecStartPre=… --apply-staged` applies it and
     `Restart=always` brings the service back.
   - **Windows:** a detached relauncher (`--relaunch-after <pid>`) waits for the
     old process to exit, applies the swap, and starts the app again.
   The previous version is retained for rollback (§3.7.7).

This single path is why channel switching works: the choice of release is made
from the live GitHub list against the running version, so a dev tester on
`1.1.0-dev.5` only moves to stable once a stable ≥ `1.1.0` ships (a lower stable
is never offered — updates only go *up*).

## Downgrade & rollback (a bad update never blocks training)

Every applied update first **retains the previous install** under
`versions/<tag>` (newest few kept). Three ways back down:

1. **Instant rollback (Settings → Updates → Downgrade / recovery → Roll back):**
   stages the retained previous build with no re-download; applies on restart.
2. **Install any earlier version:** the same screen lists older releases and
   re-downloads + verifies the chosen one. Both warn that a downgrade may cross a
   DB schema change (back up data first).
3. **Out-of-band recovery (a bad update won't even start):** run
   `PIVOT-Tactical --rollback` (optionally `--rollback <tag>`) from the install
   folder, then start PIVOT normally. It swaps the retained version in place and
   exits.

## Headless presentation (tray / service)

The server has no desktop GUI — everyone uses a browser. To keep it out of the
way:

- **Windows:** the packaged app hides its console and shows a **system-tray
  icon** (`server/pivot/win_tray.py`, pure ctypes — no extra dependency). The
  tray menu offers Open PIVOT / Copy LAN address / Show log / Quit. Force the
  console with `--no-tray`; force the tray with `--tray`.
- **Linux:** it runs as a background **systemd service** (logs to the journal:
  `journalctl -u pivot-tactical -f`).

## One-off: mint the signing key

```bash
python packaging/sign_appcast.py keygen
```

This prints two base64 strings:

- **Private key** → store as the GitHub Actions secret `PIVOT_EDDSA_PRIVATE_KEY`
  (Settings → Secrets and variables → Actions). **Never commit it.**
- **Public key** → paste into `server/pivot/updates/signing.py` as
  `_EMBEDDED_PUBLIC_KEY` (or set `PIVOT_EDDSA_PUBLIC_KEY` at runtime for staging).

Until a public key is set the app falls back to SHA-256 integrity only; once
assets carry `.sig` sidecars the signature check is enforced.

## Cutting a release

```bash
git tag v1.2.0
git push origin v1.2.0
```

The `Release` workflow and the per-PR prerelease build both use the **same**
shared action (`.github/actions/build-pivot`), so they produce identical assets:

- the portable archive + `.sha256`;
- **(Windows)** the Inno Setup installer, Authenticode-signed if the cert secret
  is present;
- a `.sig` Ed25519 signature for every asset, if `PIVOT_EDDSA_PRIVATE_KEY` is set;
- published to the GitHub Release (tag) or as a `-dev.N` prerelease (PR).

A `workflow_dispatch` run builds and uploads artifacts without publishing. Without
the secrets the build still succeeds — the installer is left unsigned and the
`.sig` sidecars are skipped (the app then trusts SHA-256 alone).

## Security: where the keys live (and what is *never* committed)

There are **two independent signatures**, and **no private key is ever stored in
the repository**. Private keys live only in GitHub Actions **encrypted secrets**
(injected as env vars at build time, masked in logs); only public material ships
in the app or the repo.

| Material | Purpose | Stored as | In the repo? |
| --- | --- | --- | --- |
| **Ed25519 private key** | Signs every release asset the in-app updater trusts | Secret `PIVOT_EDDSA_PRIVATE_KEY` | **Never** |
| **Ed25519 public key** | Verifies that signature inside the running app | `server/pivot/updates/signing.py` (`_EMBEDDED_PUBLIC_KEY`) | Yes — public by design |
| **Authenticode cert (.pfx)** | OS-trust so SmartScreen/UAC don't warn on the installer | Secret `WINDOWS_CERTIFICATE` (base64) + `WINDOWS_CERTIFICATE_PASSWORD` | **Never** |

This is the standard model for CI signing: the secret store is the vault, the
build reads it at run time, and the repo only ever carries the *public* half. If
either secret is absent the workflow still succeeds — it just skips that
signature. So the project builds and runs for contributors without access to the
keys, and only an official run (with the secrets configured) produces fully-signed
assets.

To rotate the Ed25519 key: mint a new one, update the GitHub secret, update
`_EMBEDDED_PUBLIC_KEY`, and ship an app build carrying the new public key
*before* retiring the old key.

## One-off: configure Authenticode (optional, recommended for public distribution)

For internal/LAN use this is optional. To enable it, take your code-signing
`.pfx` and add two repository secrets:

```bash
# base64-encode the certificate for safe storage as a secret
base64 -w0 your-cert.pfx        # -> paste as WINDOWS_CERTIFICATE
```

- `WINDOWS_CERTIFICATE` — base64 of the `.pfx`
- `WINDOWS_CERTIFICATE_PASSWORD` — its export password

The build then signs `PIVOT-Tactical-Setup.exe` with `signtool` (SHA-256 +
RFC-3161 timestamp) **before** the Ed25519 `.sig` is computed, so the `.sig`
covers the Authenticode-signed bytes.

## Notes

- **Signing order matters:** Authenticode runs first (it rewrites the `.exe`),
  then the Ed25519 `.sig` is computed over the signed bytes.
- **AppId:** the GUID in `packaging/pivot.iss` is the upgrade identity — keep it
  constant forever so installers upgrade in place.
