# rpios-detect

Read-only detector for a single question: **does this MicroSD card (or boot volume / image) contain Raspberry Pi OS?**

Broadcom GPU firmware (`bootcode.bin`, `start.elf`, device trees, `config.txt`) means the card can boot a Raspberry Pi. Ubuntu, DietPi, Kali, LibreELEC, PINN/NOOBS, and custom images ship the same files. This tool looks for **official Raspberry Pi OS markers** — especially `issue.txt` produced by [pi-gen](https://github.com/RPi-Distro/pi-gen) — and reports a verdict, confidence, and the evidence that led there.

## Install

Python 3.11 or newer. No third-party runtime dependencies.

```bash
python3 -m pip install -e .
# or, isolated:
pipx install .
```

## Usage

```text
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
  "tool_version": "0.1.0",
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

## License

MIT. See [LICENSE](LICENSE).
