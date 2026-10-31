"""Physical ImageSrv server console (DCUI)."""

from .app import render_auth, render_main, render_menu, render_network, render_power

__all__ = [
    "render_auth",
    "render_main",
    "render_menu",
    "render_network",
    "render_power",
]
