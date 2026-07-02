@echo off
REM Speedrun fork: launch Anki WITHOUT rebuilding.
REM Use this after a Python-only edit (qt/aqt/*.py, pylib/anki/*.py).
REM It reuses the already-built Rust backend in out/ and imports your live
REM Python source directly (see tools/run.py: pylib/ and qt/ are on sys.path).
REM
REM You must run the normal `just run` at least once first, and again ANY time
REM you change Rust (rslib/pylib), .proto, .ftl, or TypeScript/Svelte.
pushd "%~dp0"

set PYTHONWARNINGS=default
set PYTHONPYCACHEPREFIX=out\pycache
set ANKIDEV=1
set QTWEBENGINE_REMOTE_DEBUGGING=8080
set QTWEBENGINE_CHROMIUM_FLAGS=--remote-allow-origins=https://chrome-devtools-frontend.appspot.com,http://localhost:8080
set ANKI_API_PORT=40000
set ANKI_API_HOST=127.0.0.1

@if not defined PYENV set PYENV=out\pyenv

%PYENV%\Scripts\python tools\run.py %* || exit /b 1
popd
