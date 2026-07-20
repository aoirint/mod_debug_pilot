"""Tests for browser-session application workflows."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from pathlib import Path

import pytest

from mod_debug_pilot.application import BrowserContext, BrowserSession, PairingBroker, services
from mod_debug_pilot.domain import (
    BundleManifest,
    FileRecord,
    ImportedProfile,
    InstanceSnapshot,
    InstanceSpec,
    InstanceStatus,
    PairingError,
    ProfileError,
    ThunderstoreMod,
)


class ImporterStub:
    """Return one deterministic imported profile."""

    async def import_code(self, *, code: str) -> ImportedProfile:
        assert code == "profile-code"
        return ImportedProfile(
            profile_name="Imported",
            mods=(ThunderstoreMod(dependency="Author-Mod-1.2.3", enabled=True),),
            config_files=(("mod.cfg", b"Enabled = true\n"),),
        )


class WorkspaceStub:
    """Materialize enough state to exercise the application contract."""

    async def materialize(
        self,
        *,
        imported: ImportedProfile,
        destination: Path,
        local_mod_name: str,
        local_mod_bytes: bytes,
    ) -> None:
        assert imported.profile_name == "Imported"
        local = destination / "BepInEx" / "plugins" / local_mod_name
        config = destination / "BepInEx" / "config" / "mod.cfg"
        local.parent.mkdir(parents=True)
        config.parent.mkdir(parents=True)
        local.write_bytes(local_mod_bytes)
        config.write_text("Enabled = true\n", encoding="utf-8")

    def config_files(self, *, profile: Path) -> tuple[Path, ...]:
        return (profile / "BepInEx" / "config" / "mod.cfg",)

    def read_config(self, *, profile: Path, relative: str) -> str:
        return (profile / "BepInEx" / "config" / relative).read_text(encoding="utf-8")

    def write_config(self, *, profile: Path, relative: str, content: str) -> None:
        (profile / "BepInEx" / "config" / relative).write_text(content, encoding="utf-8")

    def create_bundle(
        self,
        *,
        profile: Path,
        profile_name: str,
        source_mods: Iterable[str],
        destination: Path,
    ) -> BundleManifest:
        assert profile.is_dir()
        assert tuple(source_mods) == ("Author-Mod-1.2.3",)
        destination.write_bytes(b"bundle")
        return BundleManifest(
            profile_name=profile_name,
            created_at="now",
            files=(FileRecord(path="a", size=1, sha256="a" * 64),),
        )


class RuntimeStub:
    """Record application operations without starting a process."""

    def __init__(self, *, root: Path) -> None:
        self.root = root
        self.installed: list[tuple[str, bytes]] = []
        self.launched: list[InstanceSpec] = []
        self.stopped: list[str] = []
        self.snapshot = InstanceSnapshot(
            instance_id="instance",
            name="client-1",
            profile_id="debug-profile",
            status=InstanceStatus.RUNNING,
            pid=12,
            started_at="now",
        )

    async def install_profile(self, *, profile_id: str, bundle: bytes) -> str:
        self.installed.append((profile_id, bundle))
        return profile_id

    async def launch(self, *, spec: InstanceSpec) -> InstanceSnapshot:
        self.launched.append(spec)
        return self.snapshot

    async def list_instances(self) -> tuple[InstanceSnapshot, ...]:
        return (self.snapshot,)

    async def stop(self, *, instance_id: str) -> InstanceSnapshot:
        self.stopped.append(instance_id)
        return self.snapshot

    async def capture(self, *, instance_id: str) -> Path:
        assert instance_id == "instance"
        path = self.root / "screen.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")
        return path

    async def shutdown(self) -> None:
        return None


def make_session(*, tmp_path: Path) -> tuple[BrowserSession, PairingBroker, RuntimeStub]:
    """Create one application session with local fakes."""
    broker = PairingBroker()
    runtime = RuntimeStub(root=tmp_path / "artifacts")
    session = BrowserSession(
        context=BrowserContext(
            pairing=broker,
            importer=ImporterStub(),
            workspace=WorkspaceStub(),
            runtime=runtime,
            data_root=tmp_path,
        )
    )
    return session, broker, runtime


def approve(*, session: BrowserSession, broker: PairingBroker) -> None:
    """Approve one browser session through the real broker."""
    code = broker.open()
    session.request_pairing(code=code, controller_name="Browser")
    request = broker.pending()[0]
    assert session.check_pairing() is None
    broker.decide(request_id=request.request_id, approve=True)
    assert session.check_pairing() is True
    assert session.approved


def test_browser_session_complete_workflow(*, tmp_path: Path) -> None:
    """Approved sessions prepare, edit, install, launch, capture, and stop."""
    session, broker, runtime = make_session(tmp_path=tmp_path)
    approve(session=session, broker=broker)

    async def scenario() -> None:
        draft = await session.prepare_profile(
            profile_id="debug-profile",
            profile_code="profile-code",
            local_mod_name="Local.dll",
            local_mod_bytes=b"dll",
        )
        assert draft.declared_mods == 1
        assert draft.config_files == ("mod.cfg",)
        assert session.read_config(relative="mod.cfg") == "Enabled = true\n"
        session.write_config(relative="mod.cfg", content="Enabled = false\n")
        assert session.read_config(relative="mod.cfg") == "Enabled = false\n"
        installed = await session.install_profile(profile_id="debug-profile")
        assert installed.name == "debug-profile"
        assert installed.file_count == 1
        spec = await session.launch(
            name="client-1",
            profile_id="debug-profile",
            debugger_port=55555,
        )
        assert spec.name == "client-1"
        assert await session.list_instances() == (runtime.snapshot,)
        await session.stop(instance_id="instance")
        artifact = await session.capture(instance_id="instance")
        assert artifact.name == "screen.png"
        assert artifact.content == b"png"
        await session.close()

    asyncio.run(scenario())
    assert runtime.installed == [("debug-profile", b"bundle")]
    assert runtime.stopped == ["instance"]


def test_browser_session_pairing_and_validation_failures(*, tmp_path: Path) -> None:
    """Authorization, draft, identifiers, DLLs, and artifact bounds fail closed."""
    session, broker, runtime = make_session(tmp_path=tmp_path)
    with pytest.raises(PairingError, match="first"):
        session.check_pairing()
    with pytest.raises(PairingError, match="not approved"):
        asyncio.run(session.list_instances())

    rejected_code = broker.open()
    session.request_pairing(code=rejected_code, controller_name="Browser")
    request = broker.pending()[0]
    broker.decide(request_id=request.request_id, approve=False)
    assert session.check_pairing() is False
    assert not session.approved

    approved, approved_broker, runtime = make_session(tmp_path=tmp_path / "approved")
    approve(session=approved, broker=approved_broker)
    for profile_id, name, content, maximum in (
        ("bad/path", "Local.dll", b"dll", 64 * 1024 * 1024),
        ("profile", "../Local.dll", b"dll", 64 * 1024 * 1024),
        ("profile", "Local.txt", b"dll", 64 * 1024 * 1024),
        ("profile", "Local.dll", b"", 64 * 1024 * 1024),
        ("profile", "Local.dll", b"xx", 1),
    ):
        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(services, "_MAX_LOCAL_MOD", maximum)
            operation = approved.prepare_profile(
                profile_id=profile_id,
                profile_code="profile-code",
                local_mod_name=name,
                local_mod_bytes=content,
            )
            with pytest.raises(ProfileError):
                asyncio.run(operation)

    asyncio.run(
        approved.prepare_profile(
            profile_id="profile",
            profile_code="profile-code",
            local_mod_name="Local.dll",
            local_mod_bytes=b"dll",
        )
    )
    with pytest.raises(ProfileError, match="existing"):
        asyncio.run(
            approved.prepare_profile(
                profile_id="profile-2",
                profile_code="profile-code",
                local_mod_name="Local.dll",
                local_mod_bytes=b"dll",
            )
        )
    asyncio.run(approved.close())
    with pytest.raises(ProfileError, match="draft"):
        approved.read_config(relative="mod.cfg")
    with pytest.raises(ProfileError, match="draft"):
        asyncio.run(approved.install_profile(profile_id="profile"))
    with pytest.raises(ProfileError):
        asyncio.run(approved.launch(name="client", profile_id="bad/path", debugger_port=55555))

    large = runtime.root / "screen.png"
    large.parent.mkdir(parents=True, exist_ok=True)
    large.write_bytes(b"x")

    async def large_capture(*, instance_id: str) -> Path:
        del instance_id
        return large

    runtime.capture = large_capture  # type: ignore[method-assign]
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(services, "_MAX_SCREENSHOT", 0)
        screenshot_operation = approved.capture(instance_id="instance")
        with pytest.raises(OSError, match="limit"):
            asyncio.run(screenshot_operation)
