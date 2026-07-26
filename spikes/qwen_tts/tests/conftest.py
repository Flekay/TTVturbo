"""Shared pytest config for the Qwen3-TTS spike tests.

The no-model tests must NEVER trigger a model download. We therefore:

* keep ``spikes/qwen_tts`` importable as a package-less script directory;
* stub ``qwen_tts`` and ``torch``'s CUDA surface for the unit tests so that
  importing ``runtime`` / ``clone`` does not require the heavy stack.

The real end-to-end test is gated behind ``TTVTURBO_RUN_QWEN_TTS_E2E=1`` and
only then do we let the real packages load.
"""

from __future__ import annotations

import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SPIKE_DIR = os.path.dirname(_HERE)
if _SPIKE_DIR not in sys.path:
    sys.path.insert(0, _SPIKE_DIR)


E2E_ENV = "TTVTURBO_RUN_QWEN_TTS_E2E"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "e2e: real Qwen3-TTS model run (downloads weights)")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get(E2E_ENV) == "1":
        return
    skip_e2e = pytest.mark.skip(reason=f"set {E2E_ENV}=1 to run the real model e2e test")
    for item in items:
        if "e2e" in item.keywords:
            item.add_marker(skip_e2e)
