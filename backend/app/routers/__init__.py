"""Routers package for the backend app."""

from . import addons, auth, logs, products, shipstation, shipping, skus, sync, settings
from .dashboard import router as dashboard

__all__ = [
    "addons",
    "auth",
    "logs",
    "products",
    "shipstation",
    "shipping",
    "skus",
    "sync",
    "settings",
    "dashboard",
]
