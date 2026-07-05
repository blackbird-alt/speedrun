# Deploy and open the FE study app

How to build, open, and package the FE Electrical and Computer fork. Commands
are written for **Windows / PowerShell** (the primary dev machine); the
cross-platform equivalents are noted where they differ.

All paths below are relative to the repo root: `C:\Users\ellie\speedrun\anki`.

---

## 0. What gets produced

| Artifact | What it is | How |
|---|---|---|
| `out/pyenv/` | Self-contained Python + built `anki`/`aqt` | `tools\ninja pylib qt` |
| Desktop app (dev) | The running fork | `tools\run.py` |
| `feprep/decks/fe-electrical.apkg` | The full styled deck (562 cards) | `feprep/build_apkg.py` |
| Desktop installer | Clean-machine `.msi` (`out\installer\dist\anki-26.05-win-x64.msi`) | `tools\build-installer.bat` |
| Phone APK | AnkiDroid sideload build (`Anki-Android/.../AnkiDroid-play-<abi>-debug.apk`) | `gradlew :AnkiDroid:assemblePlayDebug` (see §7) |

---

## 1. Prerequisites (one-time)

The build pulls its own pinned `uv`, `protoc`, and Node toolchain, so you only
need the system-level pieces:

- **Rust** — pinned by `rust-toolchain.toml` (1.92); `rustup` fetches it
  automatically on first build.
- **A C/C++ compiler** — Visual Studio Build Tools 2022 with the "Desktop
  development with C++" workload (provides `cl.exe`, needed by `rusqlite`).
- **Git**, and roughly 5 GB free for build outputs.

macOS/Linux: Xcode command-line tools / `build-essential` instead of MSVC.

Verify the compiler is discoverable (any of these prints a path):

```powershell
& "C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe" -latest -property installationPath
```

---

## 2. Build from source

From the repo root:

```powershell
# Builds the Rust engine + Python bridge, then the Qt/TS front-end.
.\tools\ninja pylib qt
```

First build is slow (Rust + downloading toolchains; ~5–10 min, the Qt/TS step
alone is ~400 s). Subsequent builds are incremental.

Cross-platform: `./tools/ninja pylib qt`.

To confirm the engine change compiles and its tests pass:

```powershell
cargo test -p anki scheduler::points_at_stake      # Rust unit tests
$env:PYTHONPATH="pylib;out/pylib"
& "out\pyenv\Scripts\python.exe" -m pytest pylib/tests/test_points_at_stake.py
```

---

## 3. Open (run) the desktop app

The dev launcher runs the built app. Use a throwaway profile with `-b` so it
never touches a real Anki install:

```powershell
$env:ANKIDEV = "1"
$base = "$env:TEMP\anki_fe_profile"          # any folder you like
New-Item -ItemType Directory -Force -Path $base | Out-Null
& "out\pyenv\Scripts\pythonw.exe" tools\run.py -b $base
```

- `pythonw.exe` = no console window. Use `python.exe` instead to watch logs.
- Drop `-b $base` to run against the default Anki profile.
- The app launches two processes (main + QtWebEngine helper) — both should stay
  alive; if only a brief flash occurs, run with `python.exe` to see the error.

Cross-platform: `./run` (builds if needed, then launches).

### Is it already running?

```powershell
Get-Process pythonw -ErrorAction SilentlyContinue
# find the profile a running instance uses:
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" | Select-Object CommandLine
```

Close it before importing into its collection (see §5) — the collection is a
single-writer SQLite file:

```powershell
Get-Process pythonw | Stop-Process -Force
```

---

## 4. Package the deck (`.apkg`)

Requires a built pylib (§2). Builds the full deck with the styled "FE Prep"
note type:

```powershell
$env:PYTHONPATH = "pylib;out/pylib"
& "out\pyenv\Scripts\python.exe" feprep\build_apkg.py `
    --source decks\fe-problems.txt decks\fe-figures.txt decks\fe-seed-deck.txt `
    --out feprep\decks\fe-electrical.apkg --preview
```

- `--source` accepts one or more tab-separated source decks (default:
  `fe-problems.txt` + `fe-figures.txt` + `fe-seed-deck.txt`).
- `--preview` also writes `feprep/decks/preview.html` for eyeballing the card
  design in a browser.
- **Images**: any card that references an image (`<img src="name">` or the
  `[img:name]` shorthand) has that file pulled from `feprep/decks/media/` and
  bundled automatically. Media packaging turns on only when a referenced file is
  found, so text-only decks stay lean.

---

## 5. Load the deck into the app

**Option A — in the app (simplest):** File → Import → select
`feprep\decks\fe-electrical.apkg` (or a `.txt` source directly).

**Option B — programmatic (app must be closed):** import straight into a
profile's collection:

```powershell
$env:PYTHONPATH = "pylib;out/pylib"
$col = "$env:TEMP\anki_fe_profile\User 1\collection.anki2"
& "out\pyenv\Scripts\python.exe" -c "import sys; from anki.collection import Collection, ImportAnkiPackageRequest, ImportAnkiPackageOptions; c=Collection(sys.argv[1]); c.import_anki_package(ImportAnkiPackageRequest(package_path='feprep/decks/fe-electrical.apkg', options=ImportAnkiPackageOptions())); print('cards:', c.card_count()); c.close()" "$col"
```

Re-importing a freshly built `.apkg` adds new notes (new GUIDs). To avoid
duplicates, import once, or remove prior copies first.

---

## 6. Build a desktop installer (deploy to a clean machine)

Produces a Windows installer that runs on a machine without the dev toolchain.
Two steps: **build** the app bundle, then **package** it into an `.msi`.

```powershell
# cargo must be on PATH, and the app must be CLOSED (it locks _rsbridge.pyd).
$env:PATH = "$env:USERPROFILE\.cargo\bin;$env:PATH"

# 1. Build the bundle (release Rust compile + Briefcase bundle). Slow first time.
.\tools\build-installer.bat                       # = RELEASE=2 + tools\ninja installer

# 2. Package the bundle into an .msi (WiX). RELEASE=2 => max compression (slow).
$env:PYTHONPATH = "pylib;out/pylib"; $env:RELEASE = "2"
& "out\pyenv\Scripts\python.exe" qt\tools\build_installer.py --version 26.05 package
```

Artifacts:

- `out\installer\dist\anki-26.05-win-x64.msi` — the installer for a clean machine.
- `out\installer\build\anki\windows\app\src\Anki.exe` — the same app as an
  unpacked, runnable bundle (zip and copy it if you don't want an installer).

Install the `.msi` on a clean machine and launch; the app opens into a working
review loop with AI off by default. AI is opt-in and stays inert until you add an
OpenAI key (Tools → "Set OpenAI key…", or inline in the "Generate cards (AI)"
dialog); studying and the three scores never call a model regardless.

> Gotchas learned building this: (1) `cargo` must be on PATH
> (`~\.cargo\bin`); (2) **close the running app first** — otherwise the build
> fails copying `_rsbridge.pyd` ("used by another process"); (3) with
> `RELEASE=2` the WiX MSI step is single-threaded LZX compression and can take
> ~30–40 min. Drop `RELEASE` (or set it to `0`) for a fast, larger, uncompressed
> `.msi` during iteration.

---

## 7. Phone (shared engine)

The phone runs on the **same Rust engine**, not a reimplementation. The Android
companion is a fork of **AnkiDroid** built against the shared `rslib`
(`Anki-Android/`), so the points-at-stake ordering and the three scores ship
automatically through the shared backend + protobuf. It adds the FE dashboard
(color-coded areas, the three scores), the in-app calculator + handbook, and the
key-gated AI generator/helper.

Build + install the signed sideload (debug) APK to a device/emulator:

```powershell
cd Anki-Android
.\gradlew.bat :AnkiDroid:assemblePlayDebug        # ~5-6 min
& "$env:LOCALAPPDATA\..\Android\Sdk\platform-tools\adb.exe" install -r `
    AnkiDroid\build\outputs\apk\play\debug\AnkiDroid-play-x86_64-debug.apk
```

Artifact: `Anki-Android/AnkiDroid/build/outputs/apk/play/debug/AnkiDroid-play-<abi>-debug.apk`
(x86_64 for the emulator; arm64-v8a for a physical phone). Load
`fe-electrical.apkg` (or sync from the desktop) and run a review session; the
three scores follow the same give-up rules as desktop. Two-way sync uses the
same AnkiWeb/sync-server path as upstream (see `docs/sync-conflict-rule.md`).

---

## Troubleshooting

- **App won't import / "database is locked"** — the app is still open. Close it
  (§3) and retry.
- **Only a brief window flash on launch** — relaunch with `python.exe` (not
  `pythonw.exe`) to see the traceback.
- **Stale lock after a force-kill** — Anki clears it on next launch; if not,
  delete the `.lock` file in the profile base.
- **`cl.exe` not found during build** — open a "x64 Native Tools" prompt, or
  ensure the VS Build Tools C++ workload is installed (§1).
