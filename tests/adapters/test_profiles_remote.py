"""Thunderstore import, package materialization, config editing, and bundle tests."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import stat
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest

from mod_debug_pilot.infrastructure.profiles import (
    ByteFetcher,
    ImportedProfile,
    ProfileImportError,
    ProfileWorkspace,
    ThunderstoreFetcher,
    ThunderstoreMod,
    ThunderstoreProfileImporter,
    _confined_target,
    _install_package,
    _package_target,
    extract_bundle,
)


class FakeContent:
    """Yield configured HTTP response chunks."""

    def __init__(self, chunks: list[bytes]) -> None:
        """Store chunks."""
        self.chunks = chunks

    async def iter_chunked(self, _size: int) -> AsyncIterator[bytes]:
        """Yield all chunks in order."""
        for chunk in self.chunks:
            yield chunk


class FakeResponse:
    """Minimal aiohttp response context."""

    def __init__(
        self,
        *,
        status: int = 200,
        chunks: list[bytes] | None = None,
        location: str | None = None,
        content_length: int | None = None,
    ) -> None:
        """Configure status, redirect, and content."""
        self.status = status
        self.headers = {} if location is None else {"Location": location}
        self.content_length = content_length
        self.content = FakeContent(chunks or [])

    async def __aenter__(self) -> FakeResponse:
        """Enter response context."""
        return self

    async def __aexit__(self, *_args: object) -> None:
        """Exit response context."""


class FakeSession:
    """Return responses in request order."""

    def __init__(self, responses: list[FakeResponse]) -> None:
        """Store responses."""
        self.responses = responses
        self.index = 0

    async def __aenter__(self) -> FakeSession:
        """Enter session context."""
        return self

    async def __aexit__(self, *_args: object) -> None:
        """Exit session context."""

    def get(self, _url: str, *, allow_redirects: bool) -> FakeResponse:
        """Return the next response with redirects disabled."""
        assert not allow_redirects
        response = self.responses[self.index]
        self.index += 1
        return response


class FetcherStub:
    """Return exact URL fixtures and record requested limits."""

    def __init__(self, responses: dict[str, bytes]) -> None:
        """Store immutable response fixtures."""
        self.responses = responses
        self.calls: list[tuple[str, int]] = []

    async def get(self, url: str, *, maximum_bytes: int) -> bytes:
        """Return one configured response."""
        self.calls.append((url, maximum_bytes))
        return self.responses[url]


def zip_bytes(entries: dict[str, bytes]) -> bytes:
    """Create a small in-memory regular-file ZIP."""
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return stream.getvalue()


def test_thunderstore_fetcher_success_redirect_and_failures() -> None:
    """Network fetches constrain scheme, host, redirects, status, and both size signals."""

    async def fetch(responses: list[FakeResponse], *, maximum: int = 8) -> bytes:
        with patch(
            "mod_debug_pilot.infrastructure.profiles.aiohttp.ClientSession",
            return_value=FakeSession(responses),
        ):
            return await ThunderstoreFetcher(timeout_seconds=1).get(
                "https://thunderstore.io/start", maximum_bytes=maximum
            )

    assert asyncio.run(fetch([FakeResponse(chunks=[b"a", b"b"])])) == b"ab"
    assert (
        asyncio.run(
            fetch(
                [
                    FakeResponse(status=302, location="https://cdn.thunderstore.io/file"),
                    FakeResponse(chunks=[b"ok"]),
                ]
            )
        )
        == b"ok"
    )
    for responses, message in (
        ([FakeResponse(status=302)], "no location"),
        ([FakeResponse(status=500)], "HTTP 500"),
        ([FakeResponse(content_length=9)], "too large"),
        ([FakeResponse(chunks=[b"123456789"])], "too large"),
        ([FakeResponse(status=302, location="http://evil.test")], "trusted HTTPS"),
        ([FakeResponse(status=302, location="https://evil.test")], "trusted HTTPS"),
        ([FakeResponse(status=302, location="https://thunderstore.io/x")] * 4, "too many"),
    ):
        with pytest.raises(ProfileImportError, match=message):
            asyncio.run(fetch(responses))


def test_byte_fetcher_protocol_body() -> None:
    """The protocol's default stub remains a no-op for structural subclasses."""
    assert (
        asyncio.run(
            ByteFetcher.get(
                cast(ByteFetcher, object()),
                "https://thunderstore.io",
                maximum_bytes=1,
            )
        )
        is None
    )


def r2z_bytes() -> bytes:
    """Create a representative current r2modman export."""
    metadata = b"""profileName: Imported
mods:
  - name: BepInEx-BepInExPack
    version:
      major: 5
      minor: 4
      patch: 2100
    enabled: true
  - name: Author-DisabledMod
    version:
      major: 1
      minor: 2
      patch: 3
    enabled: false
"""
    return zip_bytes(
        {
            "export.r2x": metadata,
            "config/com.example.Plugin.cfg": b"[General]\nEnabled = true\n",
        }
    )


def bepinex_package() -> bytes:
    """Create the common BepInExPack wrapper layout plus ignored metadata."""
    return zip_bytes(
        {
            "BepInExPack/winhttp.dll": b"doorstop",
            "BepInExPack/doorstop_config.ini": b"[UnityDoorstop]\n",
            "BepInExPack/BepInEx/core/BepInEx.Preloader.dll": b"preloader",
            "BepInExPack/BepInEx/plugins/base.dll": b"base",
            "manifest.json": b"{}",
        }
    )


def test_thunderstore_mod_parsing_and_url() -> None:
    """Exact dependency strings map to the canonical download route."""
    mod = ThunderstoreMod(dependency="Author-Package-With-Hyphen-1.2.3", enabled=True)
    assert mod.version == "1.2.3"
    assert mod.full_name == "Author-Package-With-Hyphen"
    assert mod.namespace_and_name == ("Author", "Package-With-Hyphen")
    assert mod.download_url.endswith("/Author/Package-With-Hyphen/1.2.3/")
    for dependency in ("bad", "NoNamespace-1.2.3"):
        invalid = ThunderstoreMod(dependency=dependency, enabled=True)
        with pytest.raises(ProfileImportError):
            _ = invalid.download_url
    with pytest.raises(ProfileImportError, match="version"):
        _ = ThunderstoreMod(dependency="Author-Package", enabled=True).version
    for dependency in ("-Package-1.2.3", "Author--1.2.3"):
        with pytest.raises(ProfileImportError, match="name"):
            _ = ThunderstoreMod(dependency=dependency, enabled=True).namespace_and_name


def test_profile_code_import() -> None:
    """UUID lookup decodes `#r2modman` Base64 and preserves config bytes."""
    code = "018d41dd-8efb-da4d-7eb6-c4d123806d64"
    url = f"https://thunderstore.io/api/experimental/legacyprofile/get/{code}/"
    payload = b"#r2modman\n" + base64.b64encode(r2z_bytes())
    fetcher = FetcherStub({url: payload})

    imported = asyncio.run(ThunderstoreProfileImporter(fetcher).import_code(code))

    assert imported.profile_name == "Imported"
    assert [mod.enabled for mod in imported.mods] == [True, False]
    assert imported.config_files[0][0] == "com.example.Plugin.cfg"
    assert fetcher.calls[0][0] == url


@pytest.mark.parametrize(
    ("code", "payload"),
    [
        ("bad", b""),
        ("018d41dd-8efb-da4d-7eb6-c4d123806d64", b"unknown"),
        ("018d41dd-8efb-da4d-7eb6-c4d123806d64", b"#r2modman\n%%%"),
    ],
)
def test_profile_code_rejections(code: str, payload: bytes) -> None:
    """Malformed codes, prefixes, and Base64 never reach ZIP extraction."""
    url = f"https://thunderstore.io/api/experimental/legacyprofile/get/{code}/"
    with pytest.raises(ProfileImportError):
        asyncio.run(ThunderstoreProfileImporter(FetcherStub({url: payload})).import_code(code))


def test_workspace_materialize_edit_bundle_and_extract(tmp_path: Path) -> None:
    """Controller builds a complete profile and Agent re-verifies every file."""
    mod = ThunderstoreMod(dependency="BepInEx-BepInExPack-5.4.2100", enabled=True)
    disabled = ThunderstoreMod(dependency="Author-Disabled-1.0.0", enabled=False)
    fetcher = FetcherStub({mod.download_url: bepinex_package()})
    workspace = ProfileWorkspace(fetcher)
    imported = ImportedProfile(
        profile_name="Imported",
        mods=(mod, disabled),
        config_files=(("com.example.cfg", b"Enabled = true\n"),),
    )
    local_mod = tmp_path / "Local.dll"
    local_mod.write_bytes(b"local")
    profile = tmp_path / "draft"

    asyncio.run(workspace.materialize(imported, destination=profile, local_mod=local_mod))

    assert (profile / "winhttp.dll").read_bytes() == b"doorstop"
    assert (profile / "BepInEx/plugins/ModDebugPilotLocal/Local.dll").is_file()
    assert (
        profile / "BepInEx/plugins/ModDebugPilotSafety/ModDebugPilot.SaveRedirector.dll"
    ).is_file()
    assert len(fetcher.calls) == 1
    configs = workspace.config_files(profile)
    assert [path.name for path in configs] == ["com.example.cfg"]
    assert workspace.read_config(profile, "com.example.cfg") == "Enabled = true\n"
    workspace.write_config(profile, "com.example.cfg", "Enabled = false\n")
    assert workspace.read_config(profile, "com.example.cfg") == "Enabled = false\n"

    bundle = tmp_path / "profile.mdp-profile"
    manifest = workspace.create_bundle(
        profile,
        profile_name="debug-profile",
        source_mods=(mod.dependency,),
        destination=bundle,
    )
    extracted = tmp_path / "installed"
    parsed = extract_bundle(bundle.read_bytes(), destination=extracted)
    assert parsed == manifest
    assert (extracted / "BepInEx/config/com.example.cfg").read_text() == "Enabled = false\n"


def test_workspace_rejections_and_cleanup(tmp_path: Path) -> None:
    """Bad destinations, DLLs, packages, profiles, and config paths fail closed."""
    workspace = ProfileWorkspace(FetcherStub({}))
    imported = ImportedProfile(profile_name="x", mods=(), config_files=())
    existing = tmp_path / "existing"
    existing.mkdir()
    dll = tmp_path / "mod.dll"
    dll.write_bytes(b"x")
    with pytest.raises(ProfileImportError):
        asyncio.run(workspace.materialize(imported, destination=existing, local_mod=dll))
    with pytest.raises(ProfileImportError):
        asyncio.run(
            workspace.materialize(
                imported,
                destination=tmp_path / "bad",
                local_mod=tmp_path / "not-dll.txt",
            )
        )
    destination = tmp_path / "incomplete"
    with pytest.raises(ProfileImportError):
        asyncio.run(workspace.materialize(imported, destination=destination, local_mod=dll))
    assert not destination.exists()
    assert workspace.config_files(tmp_path / "missing") == ()


def test_config_editor_rejections(tmp_path: Path) -> None:
    """Editor is UTF-8, existing-file-only, traversal-safe, and size-bounded."""
    profile = tmp_path / "profile"
    config = profile / "BepInEx/config/a.cfg"
    config.parent.mkdir(parents=True)
    config.write_text("x", encoding="utf-8")
    for relative in ("../escape.cfg", "C:\\escape.cfg"):
        with pytest.raises(ProfileImportError):
            ProfileWorkspace.read_config(profile, relative)
    with pytest.raises(ProfileImportError):
        ProfileWorkspace.write_config(profile, "missing.cfg", "x")
    with pytest.raises(ProfileImportError):
        ProfileWorkspace.write_config(profile, "a.cfg", "x" * (2 * 1024 * 1024 + 1))
    config.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    with pytest.raises(ProfileImportError):
        ProfileWorkspace.read_config(profile, "a.cfg")


def test_bundle_manifest_and_entry_rejections(tmp_path: Path) -> None:
    """Missing, extra, mismatched, duplicate, and unsafe bundle entries are rejected."""
    destination = tmp_path / "installed"
    destination.mkdir()
    with pytest.raises(ProfileImportError, match="already"):
        extract_bundle(b"x", destination=destination)
    destination.rmdir()
    with pytest.raises(ProfileImportError):
        extract_bundle(b"not zip", destination=destination)
    with pytest.raises(ProfileImportError):
        extract_bundle(zip_bytes({"x": b"x"}), destination=destination)

    manifest = {
        "schema_version": 1,
        "profile_name": "p",
        "created_at": "now",
        "source_mods": [],
        "files": [{"path": "x", "size": 1, "sha256": "a" * 64}],
    }
    for entries in (
        {"manifest.json": json.dumps(manifest).encode()},
        {"manifest.json": json.dumps(manifest).encode(), "profile/x": b"x", "profile/y": b"y"},
    ):
        with pytest.raises(ProfileImportError):
            extract_bundle(zip_bytes(entries), destination=destination)
    wrong = zip_bytes({"manifest.json": json.dumps(manifest).encode(), "profile/x": b"x"})
    with pytest.raises(ProfileImportError, match="digest"):
        extract_bundle(wrong, destination=destination)
    size_manifest = {**manifest, "files": [{"path": "x", "size": 2, "sha256": "a" * 64}]}
    with pytest.raises(ProfileImportError, match="size"):
        extract_bundle(
            zip_bytes({"manifest.json": json.dumps(size_manifest).encode(), "profile/x": b"x"}),
            destination=destination,
        )
    with (
        patch("mod_debug_pilot.infrastructure.profiles._MAX_FILE_COUNT", 0),
        pytest.raises(ProfileImportError, match="too many"),
    ):
        extract_bundle(
            zip_bytes(
                {
                    "manifest.json": json.dumps({**manifest, "files": []}).encode(),
                    "extra": b"x",
                }
            ),
            destination=destination,
        )
    with (
        patch("mod_debug_pilot.infrastructure.profiles._MAX_EXPANDED_PROFILE", 0),
        pytest.raises(ProfileImportError, match="too large"),
    ):
        extract_bundle(wrong, destination=destination)


def test_r2z_metadata_rejections() -> None:
    """ZIP metadata and mod schemas remain bounded and typed."""
    bad_metadata = [
        b"[]",
        b"{}",
        b"profileName: 1\nmods: []\n",
        b"profileName: x\nmods:\n  - bad\n",
        b"profileName: x\nmods:\n  - name: A-B\n    version: []\n",
        b"profileName: x\nmods:\n  - name: A-B\n    version: {major: x, minor: 0, patch: 0}\n",
    ]
    code = "018d41dd-8efb-da4d-7eb6-c4d123806d64"

    async def run(metadata: bytes) -> None:
        payload = b"#r2modman\n" + base64.b64encode(zip_bytes({"export.r2x": metadata}))
        url = f"https://thunderstore.io/api/experimental/legacyprofile/get/{code}/"
        with pytest.raises(ProfileImportError):
            await ThunderstoreProfileImporter(FetcherStub({url: payload})).import_code(code)

    for metadata in bad_metadata:
        asyncio.run(run(metadata))


def test_zip_symlink_is_rejected(tmp_path: Path) -> None:
    """Unix link metadata cannot masquerade as a regular file."""
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        info = zipfile.ZipInfo("export.r2x")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, b"target")
    code = "018d41dd-8efb-da4d-7eb6-c4d123806d64"
    url = f"https://thunderstore.io/api/experimental/legacyprofile/get/{code}/"
    payload = b"#r2modman\n" + base64.b64encode(stream.getvalue())

    with pytest.raises(ProfileImportError, match="unsafe"):
        asyncio.run(ThunderstoreProfileImporter(FetcherStub({url: payload})).import_code(code))
    assert not tmp_path.joinpath("target").exists()


def test_package_layout_and_archive_edge_cases(tmp_path: Path) -> None:
    """Package mapping handles directories, root DLLs, ignored metadata, and malformed ZIPs."""
    destination = tmp_path / "profile"
    package = zip_bytes(
        {
            "BepInExPack/": b"",
            "BepInExPack/.doorstop_version": b"1",
            "Root.dll": b"root",
            "README.md": b"ignored",
        }
    )
    _install_package(package, destination=destination, package_name="Author Bad/Name")
    assert (destination / ".doorstop_version").is_file()
    assert (destination / "BepInEx/plugins/Author_Bad_Name/Root.dll").is_file()
    assert _package_target("BepInExPack", package_name="x") is None
    assert _package_target("docs/readme.md", package_name="x") is None
    with pytest.raises(ProfileImportError, match="ZIP"):
        _install_package(b"bad", destination=destination, package_name="x")
    with (
        patch("mod_debug_pilot.infrastructure.profiles._MAX_FILE_COUNT", 0),
        pytest.raises(ProfileImportError, match="too many"),
    ):
        _install_package(package, destination=destination, package_name="x")
    with pytest.raises(ProfileImportError):
        _confined_target(destination, "")


def test_r2z_archive_limit_and_missing_metadata() -> None:
    """Profile exports reject missing metadata and bounded file-count overflow."""
    code = "018d41dd-8efb-da4d-7eb6-c4d123806d64"
    url = f"https://thunderstore.io/api/experimental/legacyprofile/get/{code}/"

    def import_archive(archive: bytes) -> None:
        payload = b"#r2modman\n" + base64.b64encode(archive)
        asyncio.run(ThunderstoreProfileImporter(FetcherStub({url: payload})).import_code(code))

    with pytest.raises(ProfileImportError, match="invalid"):
        import_archive(zip_bytes({}))
    with (
        patch("mod_debug_pilot.infrastructure.profiles._MAX_FILE_COUNT", 0),
        pytest.raises(ProfileImportError, match="too many"),
    ):
        import_archive(zip_bytes({"export.r2x": b"profileName: x\nmods: []\n"}))
