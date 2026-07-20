"""Semantic tests for the native Agent and Agent-hosted Web controller views."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, patch

import flet as ft
import pytest

from mod_debug_pilot.domain import AgentSettings, InstanceSnapshot, InstanceSpec, InstanceStatus
from mod_debug_pilot.infrastructure.agent_runtime import RemoteAgentRuntime
from mod_debug_pilot.infrastructure.profiles import (
    ImportedProfile,
    ProfileImportError,
    ProfileWorkspace,
    ThunderstoreMod,
    ThunderstoreProfileImporter,
)
from mod_debug_pilot.infrastructure.security import (
    AgentIdentity,
    AuthenticationError,
    AuthorizationStore,
    PairingBroker,
    create_ephemeral_controller_identity,
)
from mod_debug_pilot.ui.agent_app import AgentView, configure_agent_page
from mod_debug_pilot.ui.web_controller import (
    WebControllerContext,
    WebControllerView,
    configure_web_controller,
)
from tests.adapters.test_ui import FakePage


class RuntimeStub:
    """Record remote profile and instance operations."""

    def __init__(self, root: Path) -> None:
        """Create one running instance fixture."""
        self.root = root
        self.shutdown_count = 0
        self.installed: tuple[str, bytes] | None = None
        self.launched: list[InstanceSpec] = []
        self.stopped: list[str] = []
        self.snapshot = InstanceSnapshot(
            instance_id="instance",
            name="client-1",
            profile_id="debug-profile",
            status=InstanceStatus.RUNNING,
            pid=42,
            started_at="now",
        )

    async def install_profile(self, profile_id: str, bundle: bytes) -> str:
        """Record an immutable bundle upload."""
        self.installed = (profile_id, bundle)
        return profile_id

    async def launch(self, spec: InstanceSpec) -> InstanceSnapshot:
        """Record a launch."""
        self.launched.append(spec)
        return self.snapshot

    async def list_instances(self) -> tuple[InstanceSnapshot, ...]:
        """Return one tracked process."""
        return (self.snapshot,)

    async def stop(self, instance_id: str) -> InstanceSnapshot:
        """Record exact task termination."""
        self.stopped.append(instance_id)
        return self.snapshot

    async def capture(self, instance_id: str) -> Path:
        """Write a screenshot artifact."""
        path = self.root / instance_id / "capture.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")
        return path

    async def shutdown(self) -> None:
        """Record workstation restoration."""
        self.shutdown_count += 1


class ImporterStub:
    """Return one exact imported Thunderstore profile."""

    async def import_code(self, code: str) -> ImportedProfile:
        """Validate the UI passed the displayed code field."""
        assert code == "profile-code"
        return ImportedProfile(
            profile_name="Imported",
            mods=(ThunderstoreMod(dependency="A-B-1.0.0", enabled=True),),
            config_files=(("plugin.cfg", b"Enabled = true\n"),),
        )


class WorkspaceStub:
    """Create and edit a minimal browser draft."""

    async def materialize(
        self, imported: ImportedProfile, *, destination: Path, local_mod: Path
    ) -> None:
        """Materialize config and verify the temporary DLL exists."""
        assert imported.profile_name == "Imported"
        assert local_mod.read_bytes() == b"dll"
        config = destination / "BepInEx/config/plugin.cfg"
        config.parent.mkdir(parents=True)
        config.write_text("Enabled = true\n", encoding="utf-8")

    @staticmethod
    def config_files(profile: Path) -> tuple[Path, ...]:
        """Return the one editable config."""
        return (profile / "BepInEx/config/plugin.cfg",)

    @staticmethod
    def read_config(profile: Path, relative: str) -> str:
        """Read the selected config."""
        return (profile / "BepInEx/config" / relative).read_text(encoding="utf-8")

    @staticmethod
    def write_config(profile: Path, relative: str, content: str) -> None:
        """Write the selected config."""
        (profile / "BepInEx/config" / relative).write_text(content, encoding="utf-8")

    @staticmethod
    def create_bundle(
        profile: Path,
        *,
        profile_name: str,
        source_mods: Iterable[str],
        destination: Path,
    ) -> Mock:
        """Write one deterministic bundle and manifest stand-in."""
        assert profile.is_dir()
        assert profile_name == "debug-profile"
        assert tuple(source_mods) == ("A-B-1.0.0",)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"bundle")
        return Mock(files=(1, 2, 3))


def make_web_view(tmp_path: Path) -> tuple[WebControllerView, FakePage, PairingBroker, RuntimeStub]:
    """Compose a browser view with deterministic shared services."""
    page = FakePage()
    broker = PairingBroker(AuthorizationStore(tmp_path / "approved.json"))
    runtime = RuntimeStub(tmp_path / "artifacts")
    context = WebControllerContext(
        pairing=broker,
        importer=cast(ThunderstoreProfileImporter, ImporterStub()),
        workspace=cast(ProfileWorkspace, WorkspaceStub()),
        runtime=cast(RemoteAgentRuntime, runtime),
        data_root=tmp_path / "data",
    )
    return WebControllerView(cast(ft.Page, page), context=context), page, broker, runtime


def test_web_controller_pair_import_edit_install_launch_and_actions(tmp_path: Path) -> None:
    """One approved browser session exercises the entire controller workflow."""

    async def run() -> None:
        view, page, broker, runtime = make_web_view(tmp_path)
        assert view.build() is not None
        assert all(control.disabled for control in view._privileged_controls)  # noqa: SLF001
        code = broker.open()
        view.pairing_code.value = code
        await view._request_pairing_action()  # noqa: SLF001
        await view._check_approval_action()  # noqa: SLF001
        assert "waiting" in str(view.status.value)
        pending = broker.pending()[0]
        broker.decide(pending.request_id, approve=True)
        await view._check_approval_action()  # noqa: SLF001
        assert "Approved" in str(view.status.value)

        selected = SimpleNamespace(name="Local.dll", bytes=b"dll")
        view._picker.pick_files = AsyncMock(return_value=[selected])  # type: ignore[method-assign]  # noqa: SLF001
        await view._choose_mod(ft.Event("click", ft.OutlinedButton()))  # noqa: SLF001
        assert "Local.dll" in str(view.local_mod.value)
        view.profile_code.value = "profile-code"
        await view._import_profile_action()  # noqa: SLF001
        view.config_selector.value = "plugin.cfg"
        await view._load_config_action()  # noqa: SLF001
        view.config_editor.value = "Enabled = false\n"
        await view._save_config_action()  # noqa: SLF001
        assert "false" in str(view.config_editor.value)
        await view._install_profile_action()  # noqa: SLF001
        assert runtime.installed == ("debug-profile", b"bundle")

        await view._launch_action()  # noqa: SLF001
        assert runtime.launched[0].name == "client-1"
        assert view.instance_name.value == "client-2"
        assert view.debugger_port.value == "55556"
        assert len(view.instances.controls) == 1

        view._picker.save_file = AsyncMock()  # type: ignore[method-assign]  # noqa: SLF001
        await view._capture_handler("instance")()  # noqa: SLF001
        view._picker.save_file.assert_awaited_once()  # noqa: SLF001
        await view._stop_handler("instance")()  # noqa: SLF001
        assert runtime.stopped == ["instance"]
        assert page.update_count > 0

    asyncio.run(run())


def test_web_controller_rejection_selection_and_validation_branches(tmp_path: Path) -> None:
    """Unapproved, rejected, missing, invalid, oversized, and non-numbered paths are safe."""

    async def run() -> None:
        view, _page, broker, _ = make_web_view(tmp_path)
        await view._choose_mod(ft.Event("click", ft.OutlinedButton()))  # noqa: SLF001
        with pytest.raises(AuthenticationError, match="not approved"):
            view._require_approved()  # noqa: SLF001
        with pytest.raises(AuthenticationError, match="connection first"):
            await view._check_approval_action()  # noqa: SLF001
        code = broker.open()
        view.pairing_code.value = code
        await view._request_pairing_action()  # noqa: SLF001
        pending = broker.pending()[0]
        broker.decide(pending.request_id, approve=False)
        await view._check_approval_action()  # noqa: SLF001
        assert "rejected" in str(view.status.value)

        view._approved = True  # noqa: SLF001
        view._picker.pick_files = AsyncMock(return_value=[])  # type: ignore[method-assign]  # noqa: SLF001
        await view._choose_mod(ft.Event("click", ft.OutlinedButton()))  # noqa: SLF001
        unreadable = SimpleNamespace(name="bad.dll", bytes=None)
        view._picker.pick_files = AsyncMock(return_value=[unreadable])  # type: ignore[method-assign]  # noqa: SLF001
        await view._choose_mod(ft.Event("click", ft.OutlinedButton()))  # noqa: SLF001
        assert "could not be read" in str(view.status.value)

        for value in ("", "bad/name"):
            view.profile_id.value = value
            with pytest.raises(ProfileImportError, match="Profile ID"):
                view._validated_profile_id()  # noqa: SLF001
        view.profile_id.value = "debug-profile"
        with pytest.raises(ProfileImportError, match="DLL"):
            await view._import_profile_action()  # noqa: SLF001
        with pytest.raises(ProfileImportError, match="configuration"):
            view._selected_config()  # noqa: SLF001
        with pytest.raises(ProfileImportError, match="draft"):
            await view._install_profile_action()  # noqa: SLF001
        view._local_mod_bytes = b"dll"  # noqa: SLF001
        existing = tmp_path / "data/controller-drafts/debug-profile"
        existing.mkdir(parents=True)
        view.profile_code.value = "profile-code"
        with pytest.raises(ProfileImportError, match="already exists"):
            await view._import_profile_action()  # noqa: SLF001
        view.instance_name.value = "host"
        view._increment_instance_fields(  # noqa: SLF001
            InstanceSpec(name="host", profile_id="debug-profile", debugger_port=55555)
        )
        assert view.instance_name.value == "host"

        async def fail() -> None:
            raise OSError("visible")

        await view._perform(fail)  # noqa: SLF001
        assert view.status.value == "visible"

    asyncio.run(run())


def test_web_controller_event_wrappers_and_page_configuration(tmp_path: Path) -> None:
    """Flet event boundaries delegate and the hosted page receives its product theme."""

    async def run() -> None:
        view, _, _, _ = make_web_view(tmp_path)
        event = ft.Event("click", ft.Button())
        for method, action_name in (
            (view._request_pairing, "_request_pairing_action"),  # noqa: SLF001
            (view._check_approval, "_check_approval_action"),  # noqa: SLF001
            (view._import_profile, "_import_profile_action"),  # noqa: SLF001
            (view._load_config, "_load_config_action"),  # noqa: SLF001
            (view._save_config, "_save_config_action"),  # noqa: SLF001
            (view._install_profile, "_install_profile_action"),  # noqa: SLF001
            (view._launch, "_launch_action"),  # noqa: SLF001
            (view._refresh, "_refresh_action"),  # noqa: SLF001
        ):
            with patch.object(view, action_name, AsyncMock()):
                await method(event)  # type: ignore[arg-type]
        page = FakePage()
        await configure_web_controller(cast(ft.Page, page), context=view._context)  # noqa: SLF001
        assert page.title == "ModDebugPilot Controller"
        assert page.controls

    asyncio.run(run())


class ServiceStub:
    """Record listener start and stop."""

    def __init__(self, *, fail_start: bool = False) -> None:
        """Configure optional startup failure."""
        self.fail_start = fail_start
        self.started = False
        self.stopped = 0

    async def start(self, **_kwargs: object) -> None:
        """Start or fail."""
        if self.fail_start:
            raise OSError("listen failed")  # noqa: TRY003 - stable test fixture
        self.started = True

    async def stop(self) -> None:
        """Record stop."""
        self.stopped += 1


@contextmanager
def patched_agent_services(
    tmp_path: Path, runtime: RuntimeStub, api: ServiceStub, web: ServiceStub
) -> Iterator[None]:
    """Patch native Agent service composition for one lexical scope."""
    identity = AgentIdentity(
        certificate_path=tmp_path / "cert.pem",
        private_key_path=tmp_path / "key.pem",
        fingerprint="AA:" * 31 + "AA",
    )
    with (
        patch("mod_debug_pilot.ui.agent_app.create_agent_identity", return_value=identity),
        patch("mod_debug_pilot.ui.agent_app.load_agent_identity", return_value=identity),
        patch("mod_debug_pilot.ui.agent_app.server_ssl_context", return_value=Mock()),
        patch(
            "mod_debug_pilot.ui.agent_app.RemoteAgentRuntime.system_default", return_value=runtime
        ),
        patch("mod_debug_pilot.ui.agent_app.AgentApiServer", return_value=api),
        patch("mod_debug_pilot.ui.agent_app.FletWebHost", return_value=web),
    ):
        yield


def set_agent_fields(view: AgentView, tmp_path: Path) -> None:
    """Fill the native form with valid wire values."""
    values = {
        "agent_name": "Agent",
        "bind_host": "127.0.0.1",
        "api_port": "48950",
        "web_port": "48951",
        "game_executable": str(tmp_path / "Lethal Company.exe"),
        "data_root": str(tmp_path / "data"),
        "artifact_root": str(tmp_path / "artifacts"),
        "save_directory": str(tmp_path / "saves"),
    }
    for name, value in values.items():
        view.fields[name].value = value
    view.passphrase.value = "native agent passphrase"


def test_agent_view_start_pair_approve_kill_stop_and_close(tmp_path: Path) -> None:
    """The native owner explicitly starts, approves, kills, and restores services."""

    async def run() -> None:
        page = FakePage()
        runtime = RuntimeStub(tmp_path / "artifacts")
        api = ServiceStub()
        web = ServiceStub()
        view = AgentView(cast(ft.Page, page), application_data=tmp_path / "app")
        set_agent_fields(view, tmp_path)
        with patched_agent_services(tmp_path, runtime, api, web):
            await view._start_services()  # noqa: SLF001
            assert api.started
            assert web.started
            assert view.start_button.disabled
            with pytest.raises(OSError, match="already"):
                await view._start_services()  # noqa: SLF001
            await view._open_pairing(ft.Event("click", ft.Button()))  # noqa: SLF001
            assert "10 minutes" in str(view.pairing_code.value)
            identity = create_ephemeral_controller_identity("Browser")
            pairing = view._pairing  # noqa: SLF001
            assert pairing is not None
            request = pairing.request(
                code=str(view.pairing_code.value).split()[2],
                controller_id=identity.controller_id,
                controller_name=identity.name,
                public_key_b64=identity.public_key_b64,
            )
            await view._refresh_pending(ft.Event("click", ft.OutlinedButton()))  # noqa: SLF001
            assert view.pending.controls
            await view._decision_handler(request, approve=True)()  # noqa: SLF001
            assert "Approved" in str(view.status.value)
            await view._refresh_instances_action()  # noqa: SLF001
            await view._kill_handler("instance")(ft.Event("click", ft.Button()))  # noqa: SLF001
            assert runtime.stopped == ["instance"]
            await view.close()
            assert runtime.shutdown_count == 1
            assert view.stop_button.disabled

    asyncio.run(run())


def test_agent_view_service_failure_guards_defaults_and_perform(  # noqa: PLR0915
    tmp_path: Path,
) -> None:
    """Startup rollback, stopped guards, error display, and settings fallback work."""

    async def run() -> None:
        page = FakePage()
        runtime = RuntimeStub(tmp_path / "artifacts")
        api = ServiceStub(fail_start=True)
        web = ServiceStub()
        app = tmp_path / "app"
        view = AgentView(cast(ft.Page, page), application_data=app)
        set_agent_fields(view, tmp_path)
        await view._open_pairing(ft.Event("click", ft.Button()))  # noqa: SLF001
        await view._refresh_pending(ft.Event("click", ft.OutlinedButton()))  # noqa: SLF001
        await view._decision_handler(  # noqa: SLF001
            Mock(request_id="missing", controller_name="Browser"), approve=False
        )()
        with pytest.raises(OSError, match="first"):
            await view._refresh_instances_action()  # noqa: SLF001
        with patched_agent_services(tmp_path, runtime, api, web):
            with pytest.raises(OSError, match="listen"):
                await view._start_services()  # noqa: SLF001
            assert web.stopped == 1
            assert api.stopped == 1
            assert runtime.shutdown_count == 1

        with patch.object(view, "_start_services", AsyncMock()) as start:
            await view._start(ft.Event("click", ft.Button()))  # noqa: SLF001
            start.assert_awaited_once()
        with patch.object(view, "_refresh_instances_action", AsyncMock()) as refresh:
            await view._refresh_instances(  # noqa: SLF001
                ft.Event("click", ft.OutlinedButton())
            )
            refresh.assert_awaited_once()
        await view._kill_handler("missing")(ft.Event("click", ft.Button()))  # noqa: SLF001
        assert view.status.value == "Agent runtime is not running."

        async def fail() -> None:
            raise OSError("visible")

        await view._perform(fail)  # noqa: SLF001
        assert view.status.value == "visible"
        await view._stop(ft.Event("click", ft.Button()))  # noqa: SLF001

        app.mkdir(exist_ok=True)
        (app / "agent-settings.json").write_text("bad", encoding="utf-8")
        fallback = AgentView(cast(ft.Page, FakePage()), application_data=app)
        assert "agent-runtime" in str(fallback.fields["data_root"].value)
        (app / "agent-settings.json").write_text(
            json.dumps(
                AgentSettings(
                    agent_name="Saved",
                    game_executable="game.exe",
                    data_root="data",
                    artifact_root="artifacts",
                    save_directory="saves",
                ).to_mapping()
            ),
            encoding="utf-8",
        )
        loaded = AgentView(cast(ft.Page, FakePage()), application_data=app)
        assert loaded.fields["agent_name"].value == "Saved"

        identity_dir = app / "identity"
        identity_dir.mkdir()
        (identity_dir / "agent-cert.pem").write_text("certificate", encoding="utf-8")
        existing_api = ServiceStub()
        existing_web = ServiceStub()
        with (
            patched_agent_services(tmp_path, runtime, existing_api, existing_web),
            patch("mod_debug_pilot.ui.agent_app.load_agent_identity") as load,
        ):
            load.return_value = AgentIdentity(
                certificate_path=tmp_path / "cert",
                private_key_path=tmp_path / "key",
                fingerprint="AA:" * 31 + "AA",
            )
            set_agent_fields(loaded, tmp_path)
            await loaded._start_services()  # noqa: SLF001
            load.assert_called_once()
            await loaded.close()

    asyncio.run(run())


def test_agent_page_configuration_and_close_handlers(tmp_path: Path) -> None:
    """Native entry configuration mounts the Agent and owns both close signals."""

    async def run() -> None:
        page = FakePage()
        await configure_agent_page(cast(ft.Page, page), application_data=tmp_path)
        assert page.title == "ModDebugPilot Agent"
        assert page.controls
        for callback in (page.on_close, page.on_disconnect):
            resolved = cast(Callable[[ft.Event[ft.Page]], Any], callback)
            task = resolved(ft.Event("close", cast(ft.Page, page)))
            await cast(asyncio.Task[None], task)

    asyncio.run(run())
