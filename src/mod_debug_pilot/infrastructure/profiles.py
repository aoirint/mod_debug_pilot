"""Thunderstore profile import and deterministic controller-side bundle creation."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import re
import shutil
import stat
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path, PurePosixPath
from typing import Final, Protocol
from urllib.parse import urljoin, urlparse

import aiohttp
import yaml

from mod_debug_pilot.domain import BundleManifest, FileRecord

_PROFILE_CODE = re.compile(r"^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")
_VERSION_SUFFIX = re.compile(r"-(\d+)\.(\d+)\.(\d+)$")
_MAX_PROFILE_RESPONSE: Final = 32 * 1024 * 1024
_MAX_PACKAGE_RESPONSE: Final = 256 * 1024 * 1024
_MAX_EXPANDED_PROFILE: Final = 2 * 1024 * 1024 * 1024
_MAX_FILE_COUNT: Final = 20_000


class ProfileImportError(ValueError):
    """Reject unavailable, malformed, or unsafe Thunderstore profile data."""


class ByteFetcher(Protocol):
    """Fetch bounded bytes from one fixed-trust service."""

    async def get(self, url: str, *, maximum_bytes: int) -> bytes:
        """Fetch the URL after constraining redirects and response size."""
        ...


class ThunderstoreFetcher:
    """HTTPS client restricted to Thunderstore-owned hosts."""

    def __init__(self, *, timeout_seconds: float = 60.0) -> None:
        """Create a client with bounded total and socket timeouts."""
        self._timeout = aiohttp.ClientTimeout(
            total=timeout_seconds,
            connect=10,
            sock_connect=10,
            sock_read=30,
        )

    async def get(self, url: str, *, maximum_bytes: int) -> bytes:
        """Fetch after manually checking every redirect target."""
        current = url
        async with aiohttp.ClientSession(timeout=self._timeout, trust_env=False) as session:
            for _attempt in range(4):
                _validate_thunderstore_url(current)
                async with session.get(current, allow_redirects=False) as response:
                    if response.status in {301, 302, 303, 307, 308}:
                        location = response.headers.get("Location")
                        if not location:
                            raise ProfileImportError("Thunderstore redirect had no location.")
                        current = urljoin(current, location)
                        continue
                    if response.status != 200:
                        raise ProfileImportError(f"Thunderstore returned HTTP {response.status}.")
                    declared = response.content_length
                    if declared is not None and declared > maximum_bytes:
                        raise ProfileImportError("Thunderstore response is too large.")
                    chunks: list[bytes] = []
                    length = 0
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        length += len(chunk)
                        if length > maximum_bytes:
                            raise ProfileImportError("Thunderstore response is too large.")
                        chunks.append(chunk)
                    return b"".join(chunks)
        raise ProfileImportError("Thunderstore redirected too many times.")


@dataclass(frozen=True, slots=True, kw_only=True)
class ThunderstoreMod:
    """One exact dependency from an r2modman export."""

    dependency: str
    enabled: bool

    @property
    def version(self) -> str:
        """Return the exact semantic version suffix."""
        match = _VERSION_SUFFIX.search(self.dependency)
        if match is None:
            raise ProfileImportError("Thunderstore dependency version is invalid.")
        return ".".join(match.groups())

    @property
    def full_name(self) -> str:
        """Return namespace-package without the version suffix."""
        return _VERSION_SUFFIX.sub("", self.dependency)

    @property
    def namespace_and_name(self) -> tuple[str, str]:
        """Split a Thunderstore full name at its namespace boundary."""
        if "-" not in self.full_name:
            raise ProfileImportError("Thunderstore dependency name is invalid.")
        namespace, name = self.full_name.split("-", 1)
        if not namespace or not name:
            raise ProfileImportError("Thunderstore dependency name is invalid.")
        return namespace, name

    @property
    def download_url(self) -> str:
        """Return the canonical package download route."""
        namespace, name = self.namespace_and_name
        return f"https://thunderstore.io/package/download/{namespace}/{name}/{self.version}/"


@dataclass(frozen=True, slots=True, kw_only=True)
class ImportedProfile:
    """Parsed r2modman export before packages are installed."""

    profile_name: str
    mods: tuple[ThunderstoreMod, ...]
    config_files: tuple[tuple[str, bytes], ...]


class ThunderstoreProfileImporter:
    """Download and parse a Profile Code without executing package content."""

    def __init__(self, fetcher: ByteFetcher) -> None:
        """Create an importer with an explicit network boundary."""
        self._fetcher = fetcher

    async def import_code(self, code: str) -> ImportedProfile:
        """Resolve one UUID Profile Code and decode its safe `.r2z` payload."""
        normalized = code.strip()
        if _PROFILE_CODE.fullmatch(normalized) is None:
            raise ProfileImportError("Profile Code must be a UUID.")
        url = f"https://thunderstore.io/api/experimental/legacyprofile/get/{normalized}/"
        response = await self._fetcher.get(url, maximum_bytes=_MAX_PROFILE_RESPONSE)
        prefix = b"#r2modman"
        if not response.startswith(prefix):
            raise ProfileImportError("Profile Code payload has an unknown format.")
        try:
            archive = base64.b64decode(response[len(prefix) :].strip(), validate=True)
        except ValueError as error:
            raise ProfileImportError("Profile Code payload is not valid Base64.") from error
        return _parse_r2z(archive)


class ProfileWorkspace:
    """Create a disposable full BepInEx profile on the controller."""

    def __init__(self, fetcher: ByteFetcher) -> None:
        """Create a workspace builder with a bounded package fetcher."""
        self._fetcher = fetcher

    async def materialize(
        self,
        imported: ImportedProfile,
        *,
        destination: Path,
        local_mod: Path,
    ) -> None:
        """Install exact enabled packages, configs, and one local Debug DLL."""
        if destination.exists():
            raise ProfileImportError("Profile workspace already exists.")
        if not local_mod.is_file() or local_mod.suffix.casefold() != ".dll":
            raise ProfileImportError("Select a locally built DLL.")
        destination.mkdir(parents=True)
        try:
            for mod in imported.mods:
                if not mod.enabled:
                    continue
                package = await self._fetcher.get(
                    mod.download_url,
                    maximum_bytes=_MAX_PACKAGE_RESPONSE,
                )
                await asyncio.to_thread(
                    _install_package,
                    package,
                    destination=destination,
                    package_name=mod.full_name,
                )
            config_root = destination / "BepInEx" / "config"
            for relative, content in imported.config_files:
                target = _confined_target(config_root, relative=relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            local_dir = destination / "BepInEx" / "plugins" / "ModDebugPilotLocal"
            local_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local_mod, local_dir / local_mod.name)
            safety_dir = destination / "BepInEx" / "plugins" / "ModDebugPilotSafety"
            safety_dir.mkdir(parents=True, exist_ok=True)
            redirector = files("mod_debug_pilot").joinpath(
                "assets/ModDebugPilot.SaveRedirector.dll"
            )
            (safety_dir / "ModDebugPilot.SaveRedirector.dll").write_bytes(redirector.read_bytes())
            _validate_materialized_profile(destination)
        except BaseException:
            shutil.rmtree(destination, ignore_errors=True)
            raise

    @staticmethod
    def config_files(profile: Path) -> tuple[Path, ...]:
        """List editable text configuration files below `BepInEx/config`."""
        root = profile / "BepInEx" / "config"
        if not root.is_dir():
            return ()
        return tuple(
            sorted(
                (
                    path
                    for path in root.rglob("*")
                    if path.is_file() and path.suffix.casefold() in {".cfg", ".ini", ".json"}
                ),
                key=lambda path: path.as_posix().casefold(),
            )
        )

    @staticmethod
    def read_config(profile: Path, *, relative: str) -> str:
        """Read one bounded UTF-8 config file for editing."""
        target = _confined_target(profile / "BepInEx" / "config", relative=relative)
        if target.stat().st_size > 2 * 1024 * 1024:
            raise ProfileImportError("Configuration file is too large to edit.")
        return target.read_text(encoding="utf-8")

    @staticmethod
    def write_config(profile: Path, *, relative: str, content: str) -> None:
        """Atomically replace one existing UTF-8 config file."""
        encoded = content.encode("utf-8")
        if len(encoded) > 2 * 1024 * 1024:
            raise ProfileImportError("Configuration file is too large to edit.")
        target = _confined_target(profile / "BepInEx" / "config", relative=relative)
        if not target.is_file():
            raise ProfileImportError("Configuration file does not exist.")
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_bytes(encoded)
        temporary.replace(target)

    @staticmethod
    def create_bundle(
        profile: Path,
        *,
        profile_name: str,
        source_mods: Iterable[str],
        destination: Path,
    ) -> BundleManifest:
        """Create a manifest-first ZIP containing only regular profile files."""
        _validate_materialized_profile(profile)
        records: list[FileRecord] = []
        files = sorted(path for path in profile.rglob("*") if path.is_file())
        for path in files:
            relative = path.relative_to(profile).as_posix()
            size = path.stat().st_size
            records.append(FileRecord(path=relative, size=size, sha256=_sha256_file(path)))
        manifest = BundleManifest(
            profile_name=profile_name,
            created_at=datetime.now(UTC).isoformat(),
            source_mods=tuple(source_mods),
            files=tuple(records),
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "manifest.json",
                json.dumps(manifest.to_mapping(), sort_keys=True, separators=(",", ":")),
            )
            for path, record in zip(files, records, strict=True):
                archive.write(path, f"profile/{record.path}")
        temporary.replace(destination)
        return manifest


def extract_bundle(archive_bytes: bytes, *, destination: Path) -> BundleManifest:
    """Validate and extract one uploaded profile bundle into a new directory."""
    if destination.exists():
        raise ProfileImportError("Profile destination already exists.")
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            return _extract_open_bundle(archive, destination=destination)
    except zipfile.BadZipFile as error:
        raise ProfileImportError("Profile bundle ZIP is invalid.") from error


def _extract_open_bundle(archive: zipfile.ZipFile, *, destination: Path) -> BundleManifest:
    infos = archive.infolist()
    if len(infos) > _MAX_FILE_COUNT + 1:
        raise ProfileImportError("Profile bundle contains too many files.")
    try:
        manifest_bytes = archive.read("manifest.json")
        manifest = BundleManifest.from_mapping(json.loads(manifest_bytes))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ProfileImportError("Profile bundle manifest is invalid.") from error
    expected = {f"profile/{record.path}": record for record in manifest.files}
    actual_files = {info.filename: info for info in infos if info.filename != "manifest.json"}
    if set(actual_files) != set(expected):
        raise ProfileImportError("Profile bundle entries do not match its manifest.")
    if sum(record.size for record in manifest.files) > _MAX_EXPANDED_PROFILE:
        raise ProfileImportError("Expanded profile bundle is too large.")
    destination.mkdir(parents=True)
    try:
        for name, record in expected.items():
            info = actual_files[name]
            _validate_zip_info(info)
            if info.file_size != record.size:
                raise ProfileImportError("Profile bundle file size does not match.")
            content = archive.read(info)
            if hashlib.sha256(content).hexdigest() != record.sha256:
                raise ProfileImportError("Profile bundle file digest does not match.")
            target = _confined_target(destination, relative=record.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        _validate_materialized_profile(destination)
        return manifest
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def _parse_r2z(archive_bytes: bytes) -> ImportedProfile:
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            infos = archive.infolist()
            if len(infos) > _MAX_FILE_COUNT:
                raise ProfileImportError("Profile export contains too many files.")
            for info in infos:
                _validate_zip_info(info)
            raw_export = archive.read("export.r2x")
            parsed = yaml.safe_load(raw_export)
            if not isinstance(parsed, dict):
                raise ProfileImportError("Profile export metadata is invalid.")
            name = parsed.get("profileName")
            raw_mods = parsed.get("mods")
            if not isinstance(name, str) or not isinstance(raw_mods, list):
                raise ProfileImportError("Profile export metadata fields are invalid.")
            mods = tuple(_parse_export_mod(item) for item in raw_mods)
            configs: list[tuple[str, bytes]] = []
            for info in infos:
                path = PurePosixPath(info.filename)
                if len(path.parts) >= 2 and path.parts[0].casefold() == "config":
                    relative = PurePosixPath(*path.parts[1:]).as_posix()
                    configs.append((relative, archive.read(info)))
            return ImportedProfile(profile_name=name, mods=mods, config_files=tuple(configs))
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise ProfileImportError("Profile export archive is invalid.") from error


def _parse_export_mod(value: object) -> ThunderstoreMod:
    if not isinstance(value, dict):
        raise ProfileImportError("Profile mod entry is invalid.")
    name = value.get("name")
    version = value.get("version")
    enabled = value.get("enabled", True)
    if not isinstance(name, str) or not isinstance(version, dict) or not isinstance(enabled, bool):
        raise ProfileImportError("Profile mod fields are invalid.")
    parts = [version.get(key) for key in ("major", "minor", "patch")]
    if not all(isinstance(item, int) and item >= 0 for item in parts):
        raise ProfileImportError("Profile mod version is invalid.")
    return ThunderstoreMod(
        dependency=f"{name}-{parts[0]}.{parts[1]}.{parts[2]}",
        enabled=enabled,
    )


def _install_package(package: bytes, *, destination: Path, package_name: str) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            infos = archive.infolist()
            if len(infos) > _MAX_FILE_COUNT:
                raise ProfileImportError("Thunderstore package contains too many files.")
            for info in infos:
                _validate_zip_info(info)
                if info.is_dir():
                    continue
                relative = _package_target(info.filename, package_name=package_name)
                if relative is None:
                    continue
                target = _confined_target(destination, relative=relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(info))
    except zipfile.BadZipFile as error:
        raise ProfileImportError("Thunderstore package ZIP is invalid.") from error


def _package_target(name: str, *, package_name: str) -> str | None:
    path = PurePosixPath(name)
    parts = list(path.parts)
    if parts and parts[0].casefold() == "bepinexpack":
        parts.pop(0)
    if not parts:
        return None
    first = parts[0].casefold()
    if first == "bepinex":
        return PurePosixPath(*parts).as_posix()
    if first in {"winhttp.dll", "doorstop_config.ini", ".doorstop_version"}:
        return PurePosixPath(*parts).as_posix()
    if len(parts) == 1 and parts[0].casefold().endswith(".dll"):
        safe_package = re.sub(r"[^A-Za-z0-9_.-]", "_", package_name)
        return f"BepInEx/plugins/{safe_package}/{parts[0]}"
    return None


def _validate_zip_info(info: zipfile.ZipInfo) -> None:
    path = PurePosixPath(info.filename)
    unix_mode = info.external_attr >> 16
    unix_type = stat.S_IFMT(unix_mode) if info.create_system == 3 else 0
    if (
        path.is_absolute()
        or ".." in path.parts
        or "\\" in info.filename
        or (unix_type and not (stat.S_ISREG(unix_mode) or stat.S_ISDIR(unix_mode)))
    ):
        raise ProfileImportError("Archive contains an unsafe entry.")


def _confined_target(root: Path, *, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or "\\" in relative:
        raise ProfileImportError("Path escapes its profile root.")
    target = root.joinpath(*pure.parts).resolve()
    resolved_root = root.resolve()
    if target == resolved_root or resolved_root not in target.parents:
        raise ProfileImportError("Path escapes its profile root.")
    return target


def _validate_materialized_profile(profile: Path) -> None:
    required = (
        profile / "winhttp.dll",
        profile / "doorstop_config.ini",
        profile / "BepInEx" / "core" / "BepInEx.Preloader.dll",
    )
    if not all(path.is_file() for path in required):
        raise ProfileImportError("Profile does not contain a complete BepInEx 5 bootstrap.")


def _validate_thunderstore_url(url: str) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    trusted_host = host == "thunderstore.io" or host.endswith(".thunderstore.io")
    if parsed.scheme != "https" or not trusted_host:
        raise ProfileImportError("Thunderstore redirected outside its trusted HTTPS hosts.")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
