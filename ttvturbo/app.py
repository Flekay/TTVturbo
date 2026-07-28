"""TTVturbo local dashboard backend — production entry point.

This module is a thin shim that creates the FastAPI application via
:func:`app_factory.create_app` so ``uvicorn app:app`` and
``python app.py`` continue to work.  Importing this module has **no side
effects** — no directories are created, no services are constructed, no
jobs are recovered.  All of that happens inside the lifespan when the
server starts.

The :func:`find_executable` helper is re-exported as ``_find_executable``
for backward compatibility with ``media_processing`` modules that import
it lazily.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from ttvturbo.app_factory import ServiceContainer, ServiceOverrides, create_app, find_executable
from ttvturbo.settings import APP_NAME, APP_VERSION, BASE_DIR, DataPaths, Settings

# Re-export for backward compatibility (media_processing modules import this).
_find_executable = find_executable

# Production app — constructed with environment-derived settings.  No side
# effects happen here; the lifespan runs when uvicorn starts.
app = create_app()

# Convenience constants for code that still imports them from ``app``.
FRONTEND_DIST_DIR = Settings.from_env().frontend_dist
DATA_DIR = Settings.from_env().data_root
RECORDINGS_DIR = Settings.from_env().paths().recordings


def _build_frontend_if_needed() -> None:
    """Build the React frontend on startup if dist is missing or stale."""
    frontend_dir = BASE_DIR / "frontend"
    if not (frontend_dir / "package.json").is_file():
        return
    npm = shutil.which("npm")
    if npm is None:
        return
    index_html = Settings.from_env().frontend_dist / "index.html"
    src_dir = BASE_DIR / "frontend" / "src"
    if index_html.is_file() and src_dir.is_dir():
        built_mtime = index_html.stat().st_mtime
        stale = False
        for root, _dirs, files in os.walk(src_dir):
            for name in files:
                if name.endswith((".ts", ".tsx", ".js", ".jsx", ".css", ".html")):
                    p = Path(root) / name
                    try:
                        if p.stat().st_mtime > built_mtime:
                            stale = True
                            break
                    except OSError:
                        continue
            if stale:
                break
        if not stale:
            for cfg in ("package.json", "vite.config.ts", "vite.config.js", "tsconfig.json"):
                p = BASE_DIR / "frontend" / cfg
                if p.is_file():
                    try:
                        if p.stat().st_mtime > built_mtime:
                            stale = True
                            break
                    except OSError:
                        continue
        if not stale:
            return
    print("Building frontend (frontend/dist is missing or stale)...", file=sys.stderr)
    try:
        result = subprocess.run(
            [npm, "run", "build"],
            cwd=str(frontend_dir),
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        print(f"WARNING: frontend build failed to start: {exc}", file=sys.stderr)
        return
    if result.returncode != 0:
        print("WARNING: frontend build failed.", file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        return
    if (Settings.from_env().frontend_dist / "index.html").is_file():
        print("Frontend build complete.", file=sys.stderr)
    else:
        print("WARNING: frontend build reported success but dist/index.html is missing.", file=sys.stderr)


def _free_port_if_stale(port: int) -> None:
    """Kill any leftover process still bound to ``port`` before we bind."""
    try:
        import psutil
    except ImportError:
        return
    try:
        owning_pids = {
            c.pid for c in psutil.net_connections(kind="inet")
            if c.status == psutil.CONN_LISTEN and c.laddr.port == port and c.pid
        }
    except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
        return
    for pid in owning_pids:
        try:
            proc = psutil.Process(pid)
        except psutil.NoSuchProcess:
            continue
        try:
            cmdline = " ".join(proc.cmdline())
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            cmdline = ""
        if "app.py" not in cmdline:
            continue
        print(
            f"Killing stale process {pid} on port {port} "
            f"({proc.name()}: {cmdline or '<cmdline unavailable>'})",
            file=sys.stderr,
        )
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except psutil.TimeoutExpired:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except psutil.NoSuchProcess:
                pass
        except psutil.NoSuchProcess:
            pass


def main() -> None:
    import uvicorn

    settings = Settings.from_env()
    if find_executable("ffmpeg") is None:
        print("WARNING: ffmpeg not found on PATH. WAV conversion will fail.", file=sys.stderr)
    _build_frontend_if_needed()
    if not (settings.frontend_dist / "index.html").is_file():
        print(
            "WARNING: frontend/dist not built. Run `npm --prefix frontend run build`.",
            file=sys.stderr,
        )

    _free_port_if_stale(settings.port)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
