"""Picks the signal-processing backend.

* ``fx_native``: pedalboard + scipy (desktop builds, best quality),
* ``fx_numpy``: numpy only (Android, where pedalboard and scipy are not available).

Set ``EASYREMIX_DSP=numpy`` to force the numpy backend (used by the tests).
"""
from __future__ import annotations

import os

if os.environ.get("EASYREMIX_DSP", "").lower() == "numpy":
    from . import fx_numpy as fx
else:
    try:
        from . import fx_native as fx
    except ImportError:  # pragma: no cover - depends on the platform
        from . import fx_numpy as fx

NAME: str = fx.NAME
