import json
from io import StringIO

from rpios_detect.cli import run
from rpios_detect.eject import EjectResult
from rpios_detect.models import (
    EXIT_NOT_RPIOS,
    EXIT_RPIOS,
    Bus,
    Confidence,
    Edition,
    MediaInfo,
    MediaKind,
    PartitionTable,
    TargetResult,
    Verdict,
)
from rpios_detect.probe import DiscoveredDisk, DiscoveredPartition
from rpios_detect.watch import (
    WatchConfig,
    classify_card,
    is_new_media,
    is_watch_candidate,
    looks_empty,
    looks_like_empty_slot,
    media_content_fingerprint,
    media_fingerprint,
    run_watch,
    wait_until_settled,
)
from rpios_detect.session import load_session


def _disk(
    device: str = "/dev/sdb",
    *,
    size: int = 8_000_000_000,
    parts: list[DiscoveredPartition] | None = None,
    internal: bool = False,
    live: bool = False,
    removable: bool = True,
) -> DiscoveredDisk:
    if parts is None:
        parts = []
    return DiscoveredDisk(
        device=device,
        size_bytes=size,
        partition_table=PartitionTable.UNKNOWN if not parts else PartitionTable.MBR,
        bus=Bus.USB,
        internal=internal,
        removable=removable,
        kind=MediaKind.REMOVABLE_DISK,
        partitions=parts,
        live_system=live,
    )


def _fat() -> DiscoveredPartition:
    return DiscoveredPartition(
        id="sdb1",
        device="/dev/sdb1",
        fstype="vfat",
        label="bootfs",
        size_bytes=536_870_912,
        mountpoint="/Volumes/bootfs",
    )


def _result(
    device: str = "/dev/sdb",
    *,
    verdict: Verdict = Verdict.NOT_PI,
    confidence: Confidence = Confidence.NONE,
    evidence: list | None = None,
    partitions: list | None = None,
    os_name: str | None = None,
) -> TargetResult:
    return TargetResult(
        target=device,
        media=MediaInfo(
            kind=MediaKind.REMOVABLE_DISK,
            size_bytes=8_000_000_000,
            partition_table=PartitionTable.MBR,
            bus=Bus.USB,
        ),
        partitions=partitions or [],
        verdict=verdict,
        confidence=confidence,
        edition=Edition.FULL if verdict == Verdict.RASPBERRY_PI_OS else Edition.UNKNOWN,
        image_date="2026-06-18" if verdict == Verdict.RASPBERRY_PI_OS else None,
        pi_gen_stage=5 if verdict == Verdict.RASPBERRY_PI_OS else None,
        pi_gen_commit=None,
        os_name=os_name or ("Raspberry Pi OS" if verdict == Verdict.RASPBERRY_PI_OS else None),
        os_version_hint=None,
        first_boot_resize_pending=False,
        likely_boards=["pi5"] if verdict == Verdict.RASPBERRY_PI_OS else [],
        other_os_guess=None,
        evidence=list(evidence or []),
        warnings=[],
    )


def test_internal_disk_is_not_a_watch_candidate() -> None:
    assert is_watch_candidate(_disk(internal=True)) is False
    assert is_watch_candidate(_disk(live=True)) is False
    assert is_watch_candidate(_disk()) is True


def test_empty_verdict_is_empty_kind() -> None:
    empty = _result()
    assert looks_empty(empty) is True
    assert classify_card(empty) == "empty"


def test_rpi_os_classifies_as_yes() -> None:
    r = _result(verdict=Verdict.RASPBERRY_PI_OS, confidence=Confidence.CERTAIN)
    assert classify_card(r) == "raspberry_pi_os"


def test_shrinking_partitions_is_not_a_new_card() -> None:
    present = _disk(parts=[_fat()])
    after_eject = _disk(parts=[])
    fp = media_fingerprint(present)
    assert is_new_media(fp, after_eject) is False


def test_larger_partition_list_is_a_new_card() -> None:
    empty = _disk(parts=[])
    loaded = _disk(parts=[_fat()])
    fp = media_fingerprint(empty)
    assert is_new_media(fp, loaded) is True


def test_watch_once_scans_ejects_and_waits_for_removal() -> None:
    empty = _disk()
    frames = iter([[], [empty], [empty], []])
    inspected: list[str] = []
    ejected: list[str] = []

    def discover(**_kwargs: object) -> list[DiscoveredDisk]:
        try:
            return next(frames)
        except StopIteration:
            return []

    def inspect(disk: DiscoveredDisk, verbose: bool = False) -> TargetResult:
        inspected.append(disk.device)
        return _result(disk.device)

    def eject(disk: DiscoveredDisk, discover=None) -> EjectResult:
        ejected.append(disk.device)
        return EjectResult(ok=True)

    out = StringIO()
    cfg = WatchConfig(
        poll_interval=0,
        settle_seconds=0,
        mount_wait_seconds=0,
        eject=True,
        beep=False,
        json_lines=True,
        once=True,
        color=False,
    )
    code = run_watch(
        cfg,
        discover=discover,
        inspect=inspect,
        eject=eject,
        sleep=lambda _s: None,
        stdout=out,
        stderr=StringIO(),
    )
    assert inspected == ["/dev/sdb"]
    assert ejected == ["/dev/sdb"]
    assert code == EXIT_NOT_RPIOS
    events = [json.loads(line) for line in out.getvalue().splitlines() if line]
    names = [e["event"] for e in events]
    assert "inserted" in names
    assert "result" in names
    assert "removed" in names
    result_event = next(e for e in events if e["event"] == "result")
    assert result_event["kind"] == "empty"
    assert result_event["eject"] == "ok"


def test_watch_no_eject_skips_eject() -> None:
    card = _disk()
    frames = iter([[card], [card], []])
    ejected: list[str] = []

    def discover(**_kwargs: object) -> list[DiscoveredDisk]:
        try:
            return next(frames)
        except StopIteration:
            return []

    cfg = WatchConfig(
        poll_interval=0,
        settle_seconds=0,
        mount_wait_seconds=0,
        eject=False,
        once=True,
        json_lines=True,
        beep=False,
    )
    code = run_watch(
        cfg,
        discover=discover,
        inspect=lambda disk, verbose=False: _result(
            disk.device, verdict=Verdict.RASPBERRY_PI_OS, confidence=Confidence.CERTAIN
        ),
        eject=lambda disk, discover=None: ejected.append(disk.device) or EjectResult(ok=True),
        sleep=lambda _s: None,
        stdout=StringIO(),
        stderr=StringIO(),
    )
    assert ejected == []
    assert code == EXIT_RPIOS


def test_watch_ignores_internal_disks() -> None:
    internal = _disk(device="/dev/sda", internal=True)
    frames = iter([[internal], [internal], [internal]])
    inspected: list[str] = []
    polls = {"n": 0}

    def discover(**_kwargs: object) -> list[DiscoveredDisk]:
        polls["n"] += 1
        try:
            return next(frames)
        except StopIteration:
            return [internal]

    def should_stop() -> bool:
        return polls["n"] >= 3

    run_watch(
        WatchConfig(poll_interval=0, settle_seconds=0, mount_wait_seconds=0, json_lines=True),
        discover=discover,
        inspect=lambda disk, verbose=False: inspected.append(disk.device) or _result(disk.device),
        eject=lambda disk, discover=None: EjectResult(ok=True),
        sleep=lambda _s: None,
        should_stop=should_stop,
        stdout=StringIO(),
        stderr=StringIO(),
    )
    assert inspected == []


def test_watch_help_via_cli() -> None:
    assert run(["watch", "--help"]) == 0


class _TTY(StringIO):
    def isatty(self) -> bool:
        return True


def test_tty_holds_verdict_while_card_stays() -> None:
    """After a verdict the station must not flip back to WAITING every poll."""
    card = _disk()
    polls = {"n": 0}

    def discover(**_kwargs: object) -> list[DiscoveredDisk]:
        polls["n"] += 1
        if polls["n"] == 1:
            return []
        return [card]

    out = _TTY()
    run_watch(
        WatchConfig(
            poll_interval=0,
            settle_seconds=0,
            mount_wait_seconds=0,
            eject=True,
            beep=False,
            json_lines=False,
            once=False,
            color=False,
        ),
        discover=discover,
        inspect=lambda disk, verbose=False: _result(disk.device),
        eject=lambda disk, discover=None: EjectResult(ok=True),
        sleep=lambda _s: None,
        should_stop=lambda: polls["n"] >= 8,
        stdout=out,
        stderr=StringIO(),
    )
    text = out.getvalue()
    assert text.count("WAITING FOR A CARD") == 1
    assert "NOT RASPBERRY PI OS" in text
    assert "YES" not in text or "NO" in text


def test_tty_keeps_verdict_after_card_is_gone() -> None:
    """YES/NO stays up after eject and unplug; waiting must not come back."""
    card = _disk()
    polls = {"n": 0}

    def discover(**_kwargs: object) -> list[DiscoveredDisk]:
        polls["n"] += 1
        if polls["n"] == 1:
            return []
        if polls["n"] <= 4:
            return [card]
        return []

    out = _TTY()
    run_watch(
        WatchConfig(
            poll_interval=0,
            settle_seconds=0,
            mount_wait_seconds=0,
            eject=True,
            beep=False,
            json_lines=False,
            once=False,
            color=False,
        ),
        discover=discover,
        inspect=lambda disk, verbose=False: _result(
            disk.device,
            verdict=Verdict.RASPBERRY_PI_OS,
            confidence=Confidence.CERTAIN,
        ),
        eject=lambda disk, discover=None: EjectResult(ok=True),
        sleep=lambda _s: None,
        should_stop=lambda: polls["n"] >= 10,
        stdout=out,
        stderr=StringIO(),
    )
    text = out.getvalue()
    assert text.count("WAITING FOR A CARD") == 1
    assert "RASPBERRY PI OS" in text
    assert "YES" in text


def test_tty_replaces_verdict_when_next_card_is_inserted() -> None:
    first = _disk(device="/dev/sdb")
    second = _disk(device="/dev/sdc", size=16_000_000_000)
    polls = {"n": 0}

    def discover(**_kwargs: object) -> list[DiscoveredDisk]:
        polls["n"] += 1
        if polls["n"] == 1:
            return []
        if polls["n"] <= 3:
            return [first]
        if polls["n"] <= 6:
            return []
        return [second]

    def inspect(disk: DiscoveredDisk, verbose: bool = False) -> TargetResult:
        if disk.device == "/dev/sdb":
            return _result(
                disk.device,
                verdict=Verdict.RASPBERRY_PI_OS,
                confidence=Confidence.CERTAIN,
            )
        return _result(disk.device)

    out = _TTY()
    run_watch(
        WatchConfig(
            poll_interval=0,
            settle_seconds=0,
            mount_wait_seconds=0,
            eject=True,
            beep=False,
            json_lines=False,
            once=False,
            color=False,
        ),
        discover=discover,
        inspect=inspect,
        eject=lambda disk, discover=None: EjectResult(ok=True),
        sleep=lambda _s: None,
        should_stop=lambda: polls["n"] >= 12,
        stdout=out,
        stderr=StringIO(),
    )
    text = out.getvalue()
    assert text.count("WAITING FOR A CARD") == 1
    assert "YES" in text
    assert "RASPBERRY PI OS" in text
    assert "CARD DETECTED" in text or "SETTLE" in text or "SCANNING" in text
    assert "NO" in text
    assert "NOT RASPBERRY PI OS" in text


def test_empty_reader_slot_is_not_a_card() -> None:
    slot = _disk(size=0)
    card = _disk(size=8_000_000_000)
    assert looks_like_empty_slot(slot) is True
    assert is_watch_candidate(slot) is False
    assert looks_like_empty_slot(card) is False
    assert is_watch_candidate(card) is True


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_zero_size_slot_unblocks_the_next_empty_card() -> None:
    """USB readers often linger at 0 bytes after eject. That is not a new card."""
    card = _disk()
    slot = _disk(size=0)
    frames = iter([[card], [card], [card], [slot], [card], [card]])
    inspected: list[str] = []
    clock = _Clock()

    def discover(**_kwargs: object) -> list[DiscoveredDisk]:
        try:
            frame = next(frames)
        except StopIteration:
            return []
        # An empty slot means the card was pulled; jump past the remount cooldown.
        if frame and not frame[0].partitions and (frame[0].size_bytes or 0) < 1_048_576:
            clock.advance(4.0)
        return frame

    def inspect(disk: DiscoveredDisk, verbose: bool = False) -> TargetResult:
        inspected.append(disk.device)
        return _result(disk.device)

    run_watch(
        WatchConfig(
            poll_interval=0,
            settle_seconds=0,
            mount_wait_seconds=0,
            eject=True,
            beep=False,
            json_lines=True,
            once=False,
            color=False,
        ),
        discover=discover,
        inspect=inspect,
        eject=lambda disk, discover=None: EjectResult(ok=True),
        sleep=lambda _s: None,
        clock=clock,
        should_stop=lambda: len(inspected) >= 2,
        stdout=StringIO(),
        stderr=StringIO(),
    )
    assert inspected == ["/dev/sdb", "/dev/sdb"]

def test_os_like_classifies_as_unsure() -> None:
    r = _result(verdict=Verdict.RASPBERRY_PI_OS_LIKE, confidence=Confidence.MEDIUM)
    assert classify_card(r) == "unsure"


def test_apfs_and_huge_disks_are_not_watch_candidates() -> None:
    apfs = _disk(parts=[
        DiscoveredPartition(
            id="sdb1",
            device="/dev/sdb1",
            fstype="apfs",
            label="Macintosh HD",
            size_bytes=500_000_000_000,
            mountpoint=None,
        )
    ])
    huge = _disk(size=3 * 1024**4)
    assert is_watch_candidate(apfs) is False
    assert is_watch_candidate(huge) is False


def test_same_card_on_a_new_device_node_is_not_rescanned() -> None:
    first = _disk(device="/dev/disk4")
    remount = _disk(device="/dev/disk5")
    assert media_content_fingerprint(first) == media_content_fingerprint(remount)
    frames = iter([[first], [first], [remount], [remount], [remount]])
    inspected: list[str] = []
    polls = {"n": 0}

    def discover(**_kwargs: object) -> list[DiscoveredDisk]:
        polls["n"] += 1
        try:
            return next(frames)
        except StopIteration:
            return [remount]

    run_watch(
        WatchConfig(
            poll_interval=0,
            settle_seconds=0,
            mount_wait_seconds=0,
            eject=True,
            beep=False,
            json_lines=True,
            once=False,
            color=False,
        ),
        discover=discover,
        inspect=lambda disk, verbose=False: inspected.append(disk.device) or _result(disk.device),
        eject=lambda disk, discover=None: EjectResult(ok=True),
        sleep=lambda _s: None,
        should_stop=lambda: polls["n"] >= 6,
        stdout=StringIO(),
        stderr=StringIO(),
    )
    assert inspected == ["/dev/disk4"]


def test_settle_returns_none_when_stop_requested() -> None:
    disk = _disk()
    got = wait_until_settled(
        disk.device,
        WatchConfig(poll_interval=0, settle_seconds=30, mount_wait_seconds=0),
        discover=lambda **_: [disk],
        sleep=lambda _s: None,
        clock=lambda: 0,
        should_stop=lambda: True,
    )
    assert got is None


def test_keyboard_interrupt_does_not_use_once_exit_code() -> None:
    card = _disk()
    frames = iter([[card], [card]])

    def discover(**_kwargs: object) -> list[DiscoveredDisk]:
        try:
            return next(frames)
        except StopIteration as exc:
            raise KeyboardInterrupt from exc

    code = run_watch(
        WatchConfig(
            poll_interval=0,
            settle_seconds=0,
            mount_wait_seconds=0,
            eject=True,
            beep=False,
            json_lines=True,
            once=True,
            color=False,
        ),
        discover=discover,
        inspect=lambda disk, verbose=False: _result(disk.device),
        eject=lambda disk, discover=None: EjectResult(ok=True),
        sleep=lambda _s: None,
        stdout=StringIO(),
        stderr=StringIO(),
    )
    assert code == 0

def test_same_card_is_not_rescanned_after_a_brief_gap() -> None:
    """Eject often leaves a poll with no disk before the card remounts as disk5."""
    first = _disk(device="/dev/disk4")
    remount = _disk(device="/dev/disk5")
    frames = iter([[first], [first], [], [remount], [remount], [remount]])
    inspected: list[str] = []
    polls = {"n": 0}
    clock = _Clock()

    def discover(**_kwargs: object) -> list[DiscoveredDisk]:
        polls["n"] += 1
        try:
            return next(frames)
        except StopIteration:
            return [remount]

    run_watch(
        WatchConfig(
            poll_interval=0,
            settle_seconds=0,
            mount_wait_seconds=0,
            eject=True,
            beep=False,
            json_lines=True,
            once=False,
            color=False,
        ),
        discover=discover,
        inspect=lambda disk, verbose=False: inspected.append(disk.device) or _result(disk.device),
        eject=lambda disk, discover=None: EjectResult(ok=True),
        sleep=lambda _s: None,
        clock=clock,
        should_stop=lambda: polls["n"] >= 6,
        stdout=StringIO(),
        stderr=StringIO(),
    )
    assert inspected == ["/dev/disk4"]


def test_identical_card_after_a_long_gap_is_a_new_scan() -> None:
    first = _disk(device="/dev/disk4")
    second = _disk(device="/dev/disk5")
    frames = iter([[first], [first], [], [second], [second]])
    inspected: list[str] = []
    clock = _Clock()
    polls = {"n": 0}

    def discover(**_kwargs: object) -> list[DiscoveredDisk]:
        polls["n"] += 1
        try:
            frame = next(frames)
        except StopIteration:
            return []
        if not frame:
            clock.advance(4.0)
        return frame

    run_watch(
        WatchConfig(
            poll_interval=0,
            settle_seconds=0,
            mount_wait_seconds=0,
            eject=True,
            beep=False,
            json_lines=True,
            once=False,
            color=False,
        ),
        discover=discover,
        inspect=lambda disk, verbose=False: inspected.append(disk.device) or _result(disk.device),
        eject=lambda disk, discover=None: EjectResult(ok=True),
        sleep=lambda _s: None,
        clock=clock,
        should_stop=lambda: len(inspected) >= 2 or polls["n"] >= 8,
        stdout=StringIO(),
        stderr=StringIO(),
    )
    assert inspected == ["/dev/disk4", "/dev/disk5"]


def test_settle_timeout_returns_none() -> None:
    disk = _disk()
    ticks = {"n": 0}

    def clock() -> float:
        ticks["n"] += 1
        return 0.0 if ticks["n"] == 1 else 100.0

    got = wait_until_settled(
        disk.device,
        WatchConfig(poll_interval=0, settle_seconds=1.2, mount_wait_seconds=0),
        discover=lambda **_: [disk],
        sleep=lambda _s: None,
        clock=clock,
        should_stop=lambda: False,
    )
    assert got is None


def test_watch_persists_counts_and_last_verdict(tmp_path) -> None:
    session = tmp_path / "session.json"
    card = _disk()
    frames = iter([[card], [card], []])

    def discover(**_kwargs: object) -> list[DiscoveredDisk]:
        try:
            return next(frames)
        except StopIteration:
            return []

    run_watch(
        WatchConfig(
            poll_interval=0,
            settle_seconds=0,
            mount_wait_seconds=0,
            eject=True,
            beep=False,
            json_lines=True,
            once=True,
            persist=True,
            session_path=session,
        ),
        discover=discover,
        inspect=lambda disk, verbose=False: _result(
            disk.device,
            verdict=Verdict.RASPBERRY_PI_OS,
            confidence=Confidence.CERTAIN,
        ),
        eject=lambda disk, discover=None: EjectResult(ok=True),
        sleep=lambda _s: None,
        stdout=StringIO(),
        stderr=StringIO(),
    )
    loaded = load_session(session)
    assert loaded is not None
    assert loaded.checked == 1
    assert loaded.raspberry_pi_os == 1
    assert loaded.last is not None
    assert loaded.last["kind"] == "raspberry_pi_os"
    assert loaded.last["headline"] == "RASPBERRY PI OS"


def test_watch_resume_continues_counts(tmp_path) -> None:
    session = tmp_path / "session.json"
    first = _disk(device="/dev/sdb")
    second = _disk(device="/dev/sdc", size=16_000_000_000)

    def run_one(disk: DiscoveredDisk, *, resume: bool) -> None:
        frames = iter([[disk], [disk], []])

        def discover(**_kwargs: object) -> list[DiscoveredDisk]:
            try:
                return next(frames)
            except StopIteration:
                return []

        run_watch(
            WatchConfig(
                poll_interval=0,
                settle_seconds=0,
                mount_wait_seconds=0,
                eject=True,
                beep=False,
                json_lines=True,
                once=True,
                persist=True,
                resume=resume,
                session_path=session,
            ),
            discover=discover,
            inspect=lambda d, verbose=False: _result(d.device),
            eject=lambda d, discover=None: EjectResult(ok=True),
            sleep=lambda _s: None,
            stdout=StringIO(),
            stderr=StringIO(),
        )

    run_one(first, resume=False)
    run_one(second, resume=True)
    loaded = load_session(session)
    assert loaded is not None
    assert loaded.checked == 2
    assert loaded.not_raspberry_pi_os == 2


def test_new_session_without_cards_does_not_clobber_save(tmp_path) -> None:
    session = tmp_path / "session.json"
    from rpios_detect.session import save_session, snapshot_from_counts

    save_session(
        session,
        snapshot_from_counts(
            started_at="2026-08-24T00:00:00Z",
            checked=9,
            raspberry_pi_os=9,
            not_raspberry_pi_os=0,
            unsure=0,
            last={"kind": "raspberry_pi_os", "headline": "RASPBERRY PI OS", "card_number": 9},
            tool_version="0.2.0",
        ),
    )
    polls = {"n": 0}

    def should_stop() -> bool:
        polls["n"] += 1
        return polls["n"] >= 3

    run_watch(
        WatchConfig(
            poll_interval=0,
            settle_seconds=0,
            mount_wait_seconds=0,
            json_lines=True,
            persist=True,
            resume=False,
            session_path=session,
        ),
        discover=lambda **_: [],
        inspect=lambda disk, verbose=False: _result(disk.device),
        eject=lambda disk, discover=None: EjectResult(ok=True),
        sleep=lambda _s: None,
        should_stop=should_stop,
        stdout=StringIO(),
        stderr=StringIO(),
    )
    loaded = load_session(session)
    assert loaded is not None
    assert loaded.checked == 9
    assert loaded.raspberry_pi_os == 9


def test_watch_saves_session_on_interrupt(tmp_path) -> None:
    session = tmp_path / "session.json"
    card = _disk()
    frames = iter([[card], [card]])

    def discover(**_kwargs: object) -> list[DiscoveredDisk]:
        try:
            return next(frames)
        except StopIteration as exc:
            raise KeyboardInterrupt from exc

    code = run_watch(
        WatchConfig(
            poll_interval=0,
            settle_seconds=0,
            mount_wait_seconds=0,
            eject=True,
            beep=False,
            json_lines=False,
            once=False,
            persist=True,
            session_path=session,
        ),
        discover=discover,
        inspect=lambda disk, verbose=False: _result(
            disk.device,
            verdict=Verdict.RASPBERRY_PI_OS,
            confidence=Confidence.CERTAIN,
        ),
        eject=lambda disk, discover=None: EjectResult(ok=True),
        sleep=lambda _s: None,
        stdout=StringIO(),
        stderr=StringIO(),
    )
    assert code == 0
    loaded = load_session(session)
    assert loaded is not None
    assert loaded.checked == 1
    assert loaded.raspberry_pi_os == 1



