from io import StringIO

from rpios_detect.ui import HOME, StationScreen, StationState, render_station, visible_len


def test_waiting_frame_is_stable_and_readable() -> None:
    frame = render_station(
        StationState(version="0.2.0", phase="waiting", color=False),
        width=72,
        height=24,
    )
    assert "rpiv" in frame
    assert "WAITING FOR A CARD" in frame
    assert "Insert a MicroSD" in frame or "MicroSD" in frame
    assert frame.count("WAITING FOR A CARD") == 1


def test_yes_verdict_frame_shows_raspberry_pi_os() -> None:
    frame = render_station(
        StationState(
            version="0.2.0",
            phase="verdict",
            color=False,
            kind="raspberry_pi_os",
            card_number=3,
            device="/dev/disk4",
            size="32.0 GB",
            headline="RASPBERRY PI OS",
            detail="/dev/disk4  32.0 GB",
            eject_note="Ejected. Pull the card, then insert the next one.",
            checked=3,
            yes=1,
            last_headline="RASPBERRY PI OS",
            last_device="/dev/disk4",
        ),
        width=72,
        height=24,
    )
    assert "RASPBERRY PI OS" in frame
    assert "YES" in frame
    assert "/dev/disk4" in frame
    assert "3 yes" in frame or "1 yes" in frame


def test_identical_frames_are_not_rewritten() -> None:
    buf = StringIO()
    screen = StationScreen(buf, interactive=False)
    frame = render_station(StationState(phase="waiting"), width=72, height=20)
    screen.paint(frame)
    screen.paint(frame)
    screen.paint(frame)
    assert buf.getvalue().count("WAITING FOR A CARD") == 1


def test_interactive_identical_frames_are_not_rewritten() -> None:
    buf = StringIO()
    screen = StationScreen(buf, interactive=True)
    screen.open()
    frame = render_station(StationState(phase="waiting"), width=72, height=20)
    screen.paint(frame)
    after_first = buf.getvalue().count(HOME)
    screen.paint(frame)
    screen.paint(frame)
    assert buf.getvalue().count(HOME) == after_first
    assert buf.getvalue().count("WAITING FOR A CARD") == 1


def test_ascii_box_when_unicode_disabled() -> None:
    frame = render_station(
        StationState(phase="waiting", unicode=False),
        width=72,
        height=20,
    )
    assert "+" in frame
    assert "┌" not in frame


def test_visible_len_ignores_ansi() -> None:
    assert visible_len("\033[32mYES\033[0m") == 3
