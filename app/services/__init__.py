"""Compatibility exports for the service layer.

New code should prefer the domain modules in this package. This module keeps
the historical ``from app.services import ...`` import path working while the
large legacy module is split incrementally.
"""

from app.services.legacy import *  # noqa: F401,F403

