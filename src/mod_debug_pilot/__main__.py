"""Console and GUI-script entry point."""

from __future__ import annotations

import flet as ft

from mod_debug_pilot.entrypoints.flet_app import app_main


def main() -> None:
    """Launch the native Flet desktop application."""
    ft.run(app_main)


if __name__ == "__main__":
    main()
