"""Server settings."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

LOOPBACK_NAMES = ("127.0.0.1", "localhost", "[::1]")


def default_static_dir() -> Path | None:
    """The built web UI shipped inside the package (``src/reel/server/static``), if it has been built."""
    d = Path(__file__).resolve().parent / "static"
    return d if (d / "index.html").is_file() else None


def find_examples_dir() -> Path | None:
    """The repository's ``examples/`` folder (a source checkout), used to seed a new workspace."""
    for base in (Path(__file__).resolve().parents[3], Path.cwd()):
        d = base / "examples"
        if (d / "story_50s.json").is_file():
            return d
    return None


@dataclass(frozen=True)
class ServerConfig:
    workspace: Path
    host: str = "127.0.0.1"
    port: int = 8765
    token: str | None = None  # None switches authentication off (tests, `--no-token` development)
    static_dir: Path | None = field(default_factory=default_static_dir)
    examples_dir: Path | None = field(default_factory=find_examples_dir)
    extra_hosts: tuple[
        str, ...
    ] = ()  # additional accepted Host header values (tests: "testserver")
    allow_remote: bool = False

    @property
    def allowed_hosts(self) -> frozenset[str]:
        names = {f"{n}:{self.port}" for n in LOOPBACK_NAMES} | set(self.extra_hosts)
        if self.host not in LOOPBACK_NAMES and self.host != "::1":
            names.add(f"{self.host}:{self.port}")
        return frozenset(n.lower() for n in names)

    @property
    def origin_hosts(self) -> frozenset[str]:
        return self.allowed_hosts
