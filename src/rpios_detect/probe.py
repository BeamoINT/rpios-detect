"""Shared discovery types."""

from __future__ import annotations

from dataclasses import dataclass, field

from rpios_detect.models import Bus, MediaKind, PartitionTable


@dataclass
class DiscoveredPartition:
    id: str
    device: str
    fstype: str
    label: str | None
    size_bytes: int | None
    mountpoint: str | None
    readable: bool = False


@dataclass
class DiscoveredDisk:
    device: str
    size_bytes: int | None
    partition_table: PartitionTable
    bus: Bus
    internal: bool
    removable: bool
    kind: MediaKind
    partitions: list[DiscoveredPartition] = field(default_factory=list)
    trailing_free_bytes: int | None = None
    live_system: bool = False
    warnings: list[str] = field(default_factory=list)
    already_mounted: set[str] = field(default_factory=set)
