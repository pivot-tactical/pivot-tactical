# Releasing PIVOT & the auto-update chain

PIVOT updates itself on Windows through a **signed Inno Setup installer**
delivered by **WinSparkle** (the auto-update engine). This document covers the
one-off key setup and the per-release flow. Everything is permissively licensed
(WinSparkle and the installer tooling are MIT / BSD-class — see
`THIRD-PARTY-LICENSES.md`).

## How the update chain works

1. The release workflow builds the app (PyInstaller `--onedir`), drops
   `WinSparkle.dll` beside the binary, and wraps it in an Inno Setup installer
   (`PIVOT-Tactical-Setup-vX.Y.Z.exe`).
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

On Linux (and source checkouts) there is no WinSparkle; the app falls back to a
SHA-256-verified download + staged swap.

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

## Notes

- **Code signing (Authenticode):** signing the installer with an OS-trusted
  certificate (so SmartScreen/UAC don't warn) is independent of the Ed25519
  update signature and can be added to the Inno step later. For internal/LAN use
  it is optional.
- **AppId:** the GUID in `packaging/pivot.iss` is the upgrade identity — keep it
  constant forever so installers upgrade in place.
- **WinSparkle version** is pinned in `release.yml`; bump it there to update.
