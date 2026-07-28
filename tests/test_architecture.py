"""Static architecture tests for TTVturbo.

These tests enforce the dependency-direction rules that keep the codebase
maintainable:

1. **No app imports in domain modules**: ``ttvturbo.vod_pipeline``,
   ``ttvturbo.voice_clone``, ``ttvturbo.voice_profiles``,
   ``ttvturbo.media_processing``, ``ttvturbo.library`` must never import
   from ``ttvturbo.app`` or ``ttvturbo.app_factory``.  The dependency
   direction is::

       app_factory → services → domain (storage, schemas)

   Reversing it creates a circular import and couples the domain to
   FastAPI.

2. **No env reads in services**: services must not call
   ``os.environ.get`` / ``os.getenv`` directly.  Configuration is
   injected via constructor parameters sourced from the central
   :class:`Settings` class.

3. **Tool resolution goes through** ``ttvturbo.system.executables``:
   domain modules must not call ``shutil.which`` for ffmpeg/ffprobe
   directly; they use the central ``find_executable`` helper.

The tests scan source files with :mod:`ast` so they are fast and do not
import the modules under test (which would mask circular-import errors).
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = REPO_ROOT / "ttvturbo"

# Modules that must never be imported by domain packages.
FORBIDDEN_IMPORTS = {
    "ttvturbo.app",
    "ttvturbo.app_factory",
}

# Domain packages that must not import the forbidden modules.
DOMAIN_PACKAGES = {
    "vod_pipeline",
    "voice_clone",
    "voice_profiles",
    "media_processing",
    "library",
    "system",
}

# Modules allowed to import app_factory (the top-level wiring layer).
ALLOWED_APP_FACTORY_IMPORTERS = {
    "ttvturbo/app.py",
    "ttvturbo/app_factory.py",
    "ttvturbo/vod_pipeline_api.py",
    "ttvturbo/voice_profiles_api.py",
    "ttvturbo/library_api.py",
    "ttvturbo/media_processing_api.py",
    "ttvturbo/asr_api.py",
    "ttvturbo/__init__.py",
    "ttvturbo/verify.py",
}


def _iter_python_files(package: str) -> list[Path]:
    pkg_dir = PACKAGE_ROOT / package
    if not pkg_dir.is_dir():
        return []
    return sorted(pkg_dir.rglob("*.py"))


def _imported_names(source: str) -> set[str]:
    """Return the set of module names imported by *source*."""
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
    return names


# ---------------------------------------------------------------------------
# No app/app_factory imports in domain modules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("package", sorted(DOMAIN_PACKAGES))
def test_domain_packages_do_not_import_app(package: str) -> None:
    files = _iter_python_files(package)
    assert files, f"no python files found in {package}"
    offenders: list[str] = []
    for f in files:
        source = f.read_text(encoding="utf-8")
        try:
            imports = _imported_names(source)
        except SyntaxError:
            continue
        bad = imports & FORBIDDEN_IMPORTS
        if bad:
            offenders.append(f"{f.relative_to(REPO_ROOT)}: imports {sorted(bad)}")
    assert not offenders, "domain modules must not import app/app_factory:\n" + "\n".join(offenders)


def test_no_app_factory_import_outside_allowed_files() -> None:
    """Only the API/wiring layer may import app_factory."""
    offenders: list[str] = []
    for f in sorted(PACKAGE_ROOT.rglob("*.py")):
        rel = f.relative_to(REPO_ROOT).as_posix()
        if rel in ALLOWED_APP_FACTORY_IMPORTERS:
            continue
        source = f.read_text(encoding="utf-8")
        try:
            imports = _imported_names(source)
        except SyntaxError:
            continue
        if "ttvturbo.app_factory" in imports or "ttvturbo.app" in imports:
            offenders.append(rel)
    assert not offenders, "unexpected app/app_factory importers:\n" + "\n".join(offenders)


# ---------------------------------------------------------------------------
# No direct env reads in services
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module_path",
    [
        "ttvturbo/vod_pipeline/service.py",
        "ttvturbo/voice_clone/service.py",
        "ttvturbo/voice_profiles/storage.py",
        "ttvturbo/media_processing/transcription.py",
        "ttvturbo/media_processing/audio_extraction.py",
        "ttvturbo/media_processing/pipeline.py",
    ],
)
def test_services_do_not_read_env_vars(module_path: str) -> None:
    """Services must not call os.environ.get / os.getenv directly."""
    f = REPO_ROOT / module_path
    source = f.read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            # os.environ.get / os.environ
            if node.value.id == "os" and isinstance(node.attr, str):
                if node.attr == "environ":
                    offenders.append(f"line {node.lineno}: os.environ")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            # os.getenv
            if (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id == "os"
                and node.func.attr == "getenv"
            ):
                offenders.append(f"line {node.lineno}: os.getenv(...)")
    assert not offenders, f"{module_path} reads env vars directly:\n" + "\n".join(offenders)


# ---------------------------------------------------------------------------
# Tool resolution goes through ttvturbo.system.executables
# ---------------------------------------------------------------------------


def test_find_executable_lives_in_system_executables() -> None:
    from ttvturbo.system.executables import find_executable

    assert callable(find_executable)


def test_app_factory_does_not_define_find_executable() -> None:
    """app_factory must not define find_executable locally.

    The function lives in ``ttvturbo.system.executables`` and is imported
    by the domain API modules that need it.  ``app_factory`` itself no
    longer needs to import it because the status/recordings routes were
    extracted into dedicated modules.
    """
    source = (REPO_ROOT / "ttvturbo" / "app_factory.py").read_text(encoding="utf-8")
    assert "def find_executable" not in source, "app_factory must not define find_executable"


def test_app_re_exports_find_executable_from_system() -> None:
    """app.py must source find_executable from system.executables."""
    source = (REPO_ROOT / "ttvturbo" / "app.py").read_text(encoding="utf-8")
    assert "from ttvturbo.system.executables import find_executable" in source


# ---------------------------------------------------------------------------
# Settings is the single source of truth
# ---------------------------------------------------------------------------


def test_settings_has_vod_pipeline_fields() -> None:
    from ttvturbo.settings import Settings

    s = Settings()
    assert hasattr(s, "vod_max_concurrent")
    assert hasattr(s, "vod_download_timeout_seconds")
    assert hasattr(s, "vod_sync_limit")


def test_settings_has_transcription_fields() -> None:
    from ttvturbo.settings import Settings

    s = Settings()
    assert hasattr(s, "transcription_model")
    assert hasattr(s, "transcription_device")
    assert hasattr(s, "transcription_compute_type")
    assert hasattr(s, "transcription_language")
    assert hasattr(s, "transcription_max_concurrent")


def test_settings_has_voice_clone_timeout() -> None:
    from ttvturbo.settings import Settings

    s = Settings()
    assert hasattr(s, "voice_clone_timeout_seconds")


def test_settings_has_tool_paths() -> None:
    from ttvturbo.settings import Settings

    s = Settings()
    assert hasattr(s, "ffmpeg_path")
    assert hasattr(s, "ffprobe_path")
    assert hasattr(s, "yt_dlp")
    assert hasattr(s, "worker_python")


# ---------------------------------------------------------------------------
# ExecutableResolver
# ---------------------------------------------------------------------------


def test_resolve_tools_returns_resolver() -> None:
    from ttvturbo.settings import Settings
    from ttvturbo.system.executables import ExecutableResolver, resolve_tools

    s = Settings()
    r = resolve_tools(s)
    assert isinstance(r, ExecutableResolver)
    assert r.python == s.worker_python


def test_executable_resolver_is_available() -> None:
    from ttvturbo.settings import Settings
    from ttvturbo.system.executables import resolve_tools

    r = resolve_tools(Settings())
    # is_available returns a bool for each tool.
    assert isinstance(r.is_available("ffmpeg"), bool)
    assert isinstance(r.is_available("ffprobe"), bool)
    assert isinstance(r.is_available("yt_dlp"), bool)


# ---------------------------------------------------------------------------
# No runtime pip install
# ---------------------------------------------------------------------------


def test_no_runtime_pip_install() -> None:
    """No module may run ``pip install`` at runtime.

    Runtime dependency installation is unsafe: it can break the running
    environment, race with other processes, and mask deployment mistakes.
    Dependencies must be installed declaratively via requirements files.
    """
    offenders: list[str] = []
    for f in sorted(PACKAGE_ROOT.rglob("*.py")):
        rel = f.relative_to(REPO_ROOT).as_posix()
        source = f.read_text(encoding="utf-8")
        # Look for actual subprocess calls that invoke pip install.
        # We check for the pattern of building pip args and running them.
        has_pip_args = "pip_args" in source
        has_subprocess_run = "subprocess.run(pip_args" in source
        has_pip_module = '"-m", "pip"' in source or "'-m', 'pip'" in source
        if has_pip_args and has_subprocess_run:
            offenders.append(rel)
        elif has_pip_module and "subprocess" in source:
            offenders.append(rel)
    assert not offenders, "runtime pip install found in:\n" + "\n".join(offenders)


def test_transcription_worker_does_not_install_deps() -> None:
    """The transcription worker must not install dependencies at runtime."""
    source = (REPO_ROOT / "ttvturbo" / "media_processing" / "transcription_worker.py").read_text(encoding="utf-8")
    # The _ensure_dependencies function must not contain subprocess pip calls.
    assert "subprocess.run(pip_args" not in source
    assert "pip_args" not in source
    # It should return a helpful error message instead.
    assert "requirements-gpu.txt" in source
