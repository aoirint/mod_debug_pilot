"""Native Agent GUI-script entry point."""

from __future__ import annotations

import flet as ft

from mod_debug_pilot.entrypoints.agent_flet_app import agent_app_main


def main() -> None:
    """Launch the native controlled-workstation application."""
    ft.run(agent_app_main)


if __name__ == "__main__":
    main()
