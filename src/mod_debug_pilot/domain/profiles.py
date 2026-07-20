"""Framework-free Thunderstore profile values."""

from __future__ import annotations

import re
from dataclasses import dataclass

_VERSION_SUFFIX = re.compile(r"-(\d+)\.(\d+)\.(\d+)$")


class ProfileError(ValueError):
    """Reject unavailable, malformed, or unsafe profile data."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ThunderstoreMod:
    """One exact dependency from an r2modman export."""

    dependency: str
    enabled: bool

    @property
    def version(self) -> str:
        """Return the exact semantic-version suffix."""
        match = _VERSION_SUFFIX.search(self.dependency)
        if match is None:
            raise ProfileError("Thunderstore dependency version is invalid.")
        return ".".join(match.groups())

    @property
    def full_name(self) -> str:
        """Return namespace-package without the version suffix."""
        return _VERSION_SUFFIX.sub("", self.dependency)

    @property
    def namespace_and_name(self) -> tuple[str, str]:
        """Split a Thunderstore full name at its namespace boundary."""
        if "-" not in self.full_name:
            raise ProfileError("Thunderstore dependency name is invalid.")
        namespace, name = self.full_name.split("-", 1)
        if not namespace or not name:
            raise ProfileError("Thunderstore dependency name is invalid.")
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
