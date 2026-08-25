# rpios-detect

[![CI](https://github.com/BeamoINT/rpios-detect/actions/workflows/ci.yml/badge.svg)](https://github.com/BeamoINT/rpios-detect/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)

Read-only detector for a single question: **does this MicroSD card (or boot volume / image) contain Raspberry Pi OS?**

Broadcom GPU firmware (`bootcode.bin`, `start.elf`, device trees, `config.txt`) means the card can boot a Raspberry Pi. Ubuntu, DietPi, Kali, LibreELEC, PINN/NOOBS, and custom images ship the same files. This tool looks for **official Raspberry Pi OS markers** — especially `issue.txt` produced by [pi-gen](https://github.com/RPi-Distro/pi-gen) — and reports a verdict, confidence, and the evidence that led there.

## Install

Python 3.11 or newer. No third-party runtime dependencies. This installs two commands: **`rpiv`** (the card station) and **`rpios-detect`** (one-shot inspect).

One-liner (clone or not):

```bash
curl -fsSL https://raw.githubusercontent.com/BeamoINT/rpios-detect/main/install.sh | bash
rpiv
```

From a clone:

```bash
./install.sh
```

Or with pip / pipx:

```bash
python3 -m pip install git+https://github.com/BeamoINT/rpios-detect.git
# recommended isolated install:
pipx install git+https://github.com/BeamoINT/rpios-detect.git
```

`rpiv` with no arguments starts the insert → scan → eject station. Pass a path for a one-shot inspect (`rpiv /Volumes/bootfs`). `rpios-detect` still inspects currently connected removable disks. If `rpiv` is missing after install, add `~/.local/bin` to your PATH.

Never run `sudo rpiv`. It is a user-level command. sudo can make the launcher or Python unexecutable (`zsh: permission denied: rpiv`).

## Usage

```text
rpiv                          # insert → scan → eject station (new session)
rpiv --resume                 # continue the last session (counts + last verdict)
rpiv --status                 # print the saved session
rpiv --clear                  # delete the saved session
rpiv /Volumes/bootfs          # one-shot inspect
rpios-detect                  # inspect currently connected removable disks
rpios-detect /dev/disk4       # one device (macOS)
rpios-detect /dev/sda         # one device (Linux USB reader)
rpios-detect /dev/mmcblk0     # onboard SD (Linux / Pi)
rpios-detect E:               # Windows drive letter
rpios-detect /Volumes/bootfs  # already-mounted boot volume
rpios-detect ./fixtures/boot  # directory of boot files
rpios-detect image.img        # raw SD image, read-only
rpios-detect --json
rpios-detect --all            # every candidate, not just obvious removable media
rpios-detect --verbose        # print each evidence rule hit/miss
```

Human output is the default. `--json` prints the stable schema (field names will not be renamed).

## Watch mode (insert → scan → eject → repeat)

For a pile of MicroSD cards, start a station and leave it running until you press Ctrl+C:

```text
rpiv
# same:
rpios-detect watch
```

Insert a card. The tool waits until the reader’s partition list is stable (and, if a FAT boot volume exists, until the OS mounts it), identifies whether it is Raspberry Pi OS, then **ejects** it so you can pull it and put in the next one. Blank / unformatted cards are the common “no” case: they count as **not Raspberry Pi OS** and are ejected the same way. Raspberry Pi OS cards have many boot files; firmware-only cards are reported as not Raspberry Pi OS, not as a yes.

```text
rpios-detect watch                  # macOS, Linux, and Windows
rpios-detect watch --resume         # continue the last saved session
rpios-detect watch --status         # print the saved session
rpios-detect watch --clear          # delete the saved session
rpios-detect watch --no-eject       # identify only; you unmount/eject yourself
rpios-detect watch --once           # one card, then exit with the usual exit code
rpios-detect watch --json           # JSON lines: waiting/inserted/result/removed/stopped
rpios-detect watch --no-beep        # quiet (TTY beeps are on by default)
```

Eject is an unmount / OS eject, not a write. Internal disks and the machine’s live boot device are never ejected. After eject, cheap USB readers often linger as a 0-byte “slot”; that leftover is ignored so the next blank card is not mistaken for the previous one. If a reader never drops off the list, pull the card and wait for “Waiting for a card…” before inserting the next.

The station saves counts and the last verdict to a session file (macOS: `~/Library/Application Support/rpiv/session.json`; Linux: `~/.local/state/rpiv/session.json`; or `$RPIV_SESSION`). `rpiv` starts a new session; `rpiv --resume` continues the previous one. A new session does not overwrite the save until the first card is scanned.

On Linux, `udisksctl` is preferred (no root). The fallback is `umount` + `eject`. On Windows, unformatted cards may have no drive letter — the tool still reports the verdict and asks you to remove the card.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | At least one target is Raspberry Pi OS at confidence `high` or `certain` |
| 1 | Targets found, none are Raspberry Pi OS |
| 2 | No candidate media found |
| 3 | Ambiguous / low confidence only |
| 64 | Usage / invalid arguments |
| 70 | Unexpected internal error |

## Safety

Inspection is **read-only**. The process never calls `dd`, `mkfs`, `diskutil erase*`, or a read-write mount. If it must mount a volume, it mounts read-only and unmounts only what it mounted. Internal system disks are ignored unless you pass an explicit path (and even then, nothing is written).

macOS cannot read the ext4 root partition. That is expected. Official Raspberry Pi OS can be identified with **certainty** from `issue.txt` on the FAT boot partition alone.

If macOS denies access to `/Volumes/bootfs`, grant Full Disk Access to Terminal (or your IDE) under System Settings → Privacy & Security.

## JSON (abridged)

```json
{
  "tool": "rpios-detect",
  "tool_version": "0.2.0",
  "scanned_at": "2026-08-24T03:36:45Z",
  "host": { "os": "darwin", "arch": "arm64" },
  "results": [
    {
      "target": "/dev/disk4",
      "verdict": "raspberry_pi_os",
      "confidence": "certain",
      "edition": "full",
      "image_date": "2026-06-18",
      "pi_gen_stage": 5,
      "os_name": "Raspberry Pi OS",
      "first_boot_resize_pending": true,
      "likely_boards": ["pi3", "pi3_64", "pi4", "pi5"]
    }
  ]
}
```

`edition` comes from the pi-gen stage in `issue.txt`: stage2 → lite, stage4 → desktop, stage5 → full. Debian codenames are reported only when `/etc/os-release` (or `raspi.list`) is readable — never guessed from the image date.

## Tests

```bash
python3 -m pip install -e ".[dev]"
python3 -m pytest
```

The suite does not need a physical card. Fixtures cover official pi-gen images, firmware-only media, DietPi, Ubuntu, LibreELEC, empty FAT, a fake `.img`, and a real `diskutil list` transcript.

## Adding a negative OS marker

1. Add a matcher in `src/rpios_detect/evidence.py` that returns a detail string or `None`.
2. Register it on `RULES` with category `"negative"`, a `Weight`, and `os_guess=...`.
3. Add a fixture + test in `tests/test_detect.py` proving the verdict is **not** `raspberry_pi_os`.
4. Document the marker in [docs/DETECTION.md](docs/DETECTION.md).

Do not promote firmware-only evidence to Raspberry Pi OS. If the choice is a clever heuristic vs `unknown`, choose `unknown`.

## Detection rules

See [docs/DETECTION.md](docs/DETECTION.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security reports: [SECURITY.md](SECURITY.md).

## License

MIT. See [LICENSE](LICENSE).
