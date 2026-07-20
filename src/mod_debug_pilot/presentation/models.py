"""Immutable presentation state for both Flet surfaces."""

from __future__ import annotations

from dataclasses import dataclass

from mod_debug_pilot.domain import AgentSettings, InstanceSnapshot, PairingRequest


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentViewState:
    """Complete render state for the native Agent window."""

    settings: AgentSettings
    running: bool = False
    status: str = "Controller listener stopped."
    controller_url: str = "Controller URL: listener stopped"
    pairing_code: str = "Pairing code: closed"
    pending: tuple[PairingRequest, ...] = ()
    instances: tuple[InstanceSnapshot, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class BrowserViewState:
    """Complete render state for one browser Controller session."""

    approved: bool = False
    status: str = "Enter the Agent-displayed pairing code."
    config_files: tuple[str, ...] = ()
    config_content: str = ""
    instances: tuple[InstanceSnapshot, ...] = ()
