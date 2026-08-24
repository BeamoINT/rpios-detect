"""Stable JSON schema types for rpios-detect.

Field names are part of the public contract. Adding fields is allowed;
renaming or removing them is not.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


TOOL_NAME = "rpios-detect"
SCHEMA_VERSION = 1


class Verdict(StrEnum):
    RASPBERRY_PI_OS = "raspberry_pi_os"
    RASPBERRY_PI_OS_LIKE = "raspberry_pi_os_like"
    OTHER_PI_OS = "other_pi_os"
    NOT_PI = "not_pi"
    UNKNOWN = "unknown"


class Confidence(StrEnum):
    CERTAIN = "certain"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"


class MediaKind(StrEnum):
    REMOVABLE_DISK = "removable_disk"
    MOUNTED_VOLUME = "mounted_volume"
    DIRECTORY = "directory"
    IMAGE = "image"


class PartitionRole(StrEnum):
    BOOT = "boot"
    ROOT = "root"
    OTHER = "other"
    UNKNOWN = "unknown"


class PartitionTable(StrEnum):
    MBR = "mbr"
    GPT = "gpt"
    UNKNOWN = "unknown"


class Bus(StrEnum):
    USB = "usb"
    MMC = "mmc"
    UNKNOWN = "unknown"


class Edition(StrEnum):
    LITE = "lite"
    DESKTOP = "desktop"
    FULL = "full"
    UNKNOWN = "unknown"


class Weight(StrEnum):
    CERTAIN = "certain"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


CONFIDENCE_RANK = {
    Confidence.NONE: 0,
    Confidence.LOW: 1,
    Confidence.MEDIUM: 2,
    Confidence.HIGH: 3,
    Confidence.CERTAIN: 4,
}

# Exit codes (stable).
EXIT_RPIOS = 0
EXIT_NOT_RPIOS = 1
EXIT_NO_MEDIA = 2
EXIT_AMBIGUOUS = 3
EXIT_USAGE = 64
EXIT_INTERNAL = 70


@dataclass(frozen=True)
class HostInfo:
    os: str
    arch: str

    def to_dict(self) -> dict[str, Any]:
        return {"os": self.os, "arch": self.arch}


@dataclass(frozen=True)
class MediaInfo:
    kind: MediaKind
    size_bytes: int | None
    partition_table: PartitionTable
    bus: Bus

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "size_bytes": self.size_bytes,
            "partition_table": self.partition_table.value,
            "bus": self.bus.value,
        }


@dataclass(frozen=True)
class PartitionInfo:
    id: str
    type: str
    label: str | None
    size_bytes: int | None
    role: PartitionRole
    mountpoint: str | None
    readable: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "label": self.label,
            "size_bytes": self.size_bytes,
            "role": self.role.value,
            "mountpoint": self.mountpoint,
            "readable": self.readable,
        }


@dataclass(frozen=True)
class EvidenceItem:
    id: str
    weight: Weight
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "weight": self.weight.value, "detail": self.detail}


@dataclass
class TargetResult:
    target: str
    media: MediaInfo
    partitions: list[PartitionInfo]
    verdict: Verdict
    confidence: Confidence
    edition: Edition
    image_date: str | None
    pi_gen_stage: int | None
    pi_gen_commit: str | None
    os_name: str | None
    os_version_hint: str | None
    first_boot_resize_pending: bool
    likely_boards: list[str]
    other_os_guess: str | None
    evidence: list[EvidenceItem]
    warnings: list[str]
    live_system: bool = False
    cloud_init_present: bool = False
    rule_log: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "media": self.media.to_dict(),
            "partitions": [p.to_dict() for p in self.partitions],
            "verdict": self.verdict.value,
            "confidence": self.confidence.value,
            "edition": self.edition.value,
            "image_date": self.image_date,
            "pi_gen_stage": self.pi_gen_stage,
            "pi_gen_commit": self.pi_gen_commit,
            "os_name": self.os_name,
            "os_version_hint": self.os_version_hint,
            "first_boot_resize_pending": self.first_boot_resize_pending,
            "likely_boards": list(self.likely_boards),
            "other_os_guess": self.other_os_guess,
            "evidence": [e.to_dict() for e in self.evidence],
            "warnings": list(self.warnings),
            "live_system": self.live_system,
            "cloud_init_present": self.cloud_init_present,
        }


@dataclass
class ScanReport:
    tool: str = TOOL_NAME
    tool_version: str = "0.1.0"
    scanned_at: str = ""
    host: HostInfo = field(default_factory=lambda: HostInfo(os="unknown", arch="unknown"))
    results: list[TargetResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "tool_version": self.tool_version,
            "scanned_at": self.scanned_at,
            "host": self.host.to_dict(),
            "results": [r.to_dict() for r in self.results],
        }


RESULT_REQUIRED_KEYS = (
    "target",
    "media",
    "partitions",
    "verdict",
    "confidence",
    "edition",
    "image_date",
    "pi_gen_stage",
    "pi_gen_commit",
    "os_name",
    "os_version_hint",
    "first_boot_resize_pending",
    "likely_boards",
    "other_os_guess",
    "evidence",
    "warnings",
)

MEDIA_REQUIRED_KEYS = ("kind", "size_bytes", "partition_table", "bus")
PARTITION_REQUIRED_KEYS = (
    "id",
    "type",
    "label",
    "size_bytes",
    "role",
    "mountpoint",
    "readable",
)
EVIDENCE_REQUIRED_KEYS = ("id", "weight", "detail")
REPORT_REQUIRED_KEYS = ("tool", "tool_version", "scanned_at", "host", "results")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def host_info() -> HostInfo:
    import platform
    import sys

    plat = sys.platform
    if plat == "darwin":
        os_name = "darwin"
    elif plat.startswith("linux"):
        os_name = "linux"
    elif plat in ("win32", "cygwin"):
        os_name = "windows"
    else:
        os_name = plat
    return HostInfo(os=os_name, arch=platform.machine() or "unknown")


def as_plain(obj: Any) -> Any:
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if isinstance(obj, StrEnum):
        return obj.value
    return asdict(obj)
