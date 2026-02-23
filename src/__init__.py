from __future__ import annotations

from typing import Any

__all__ = ["get_user"]


def get_user(*args: Any, **kwargs: Any):
    # Avoid importing login modules at package import time; env must be loaded first.
    from .login_logic import get_user as _get_user

    return _get_user(*args, **kwargs)
