# Detection rules

`rpios-detect` answers “is this Raspberry Pi OS?” with a **verdict + confidence + evidence**. Firmware files alone must never produce `raspberry_pi_os`.

Detection is a pure function: `detect(boot, root, layout) -> Detection`. Host probes (diskutil, lsblk, FAT-in-image) only build snapshots. Unit tests never talk to real hardware.

## Snapshots

- **Boot snapshot** — filenames, directories, and small text files from the FAT boot volume (`issue.txt`, `cmdline.txt`, `config.txt`, `meta-data`, …). Lookups are case-insensitive.
- **Root snapshot** — optional. Filled only when the Linux partition is readable (typically Linux hosts). macOS usually cannot read ext4; that is not a failure.
- **Partition layout** — table type, FAT+Linux pair, volume labels, trailing free space.

## Smoking guns (`certain` → `raspberry_pi_os`)

Official Raspberry Pi OS `issue.txt` on the boot partition, all three of:

1. `Raspberry Pi reference`
2. `Generated using pi-gen`
3. `https://github.com/RPi-Distro/pi-gen`

The matcher parses the image date, git commit, and `stageN`.

Alternatively, on a readable rootfs: `/etc/rpi-issue` **and** `/etc/apt/sources.list.d/raspi.list` pointing at Raspberry Pi’s apt repo.

## pi-gen stages → edition

| Stage | Edition |
|-------|---------|
| 2 | lite |
| 4 | desktop |
| 5 | full (desktop + recommended software) |
| other | unknown (stage number still reported) |

Older **Raspbian** branding in `issue.txt` is Raspberry Pi OS lineage; `os_name` reports `Raspbian` when that is what the files say.

## Supporting official markers (`high` without `issue.txt`)

Not enough alone; several together can yield `raspberry_pi_os` at confidence `high`:

- `meta-data` with `instance_id: rpios-image` (also accepts `rpios-image`)
- FAT label `bootfs` / `boot`
- Linux label `rootfs`
- `/usr/bin/raspi-config`
- `raspi.list` / Raspberry Pi apt repo
- `os-release` mentioning Raspberry Pi OS (modern images are often `ID=debian`)

## Firmware (Pi-bootable, many distros)

These raise “this can boot a Pi” and must **not** alone produce `raspberry_pi_os`:

- `bootcode.bin`, `start*.elf`, `fixup*.dat`
- `config.txt`, `cmdline.txt`
- `bcm27*.dtb`, `overlays/`
- `kernel.img` / `kernel7.img` / `kernel7l.img` / `kernel8.img` / `kernel_2712.img`
- `LICENCE.broadcom`

Four or more firmware hits with no official markers → `other_pi_os` at `low`, or `unknown`.

## Negative markers (`other_pi_os`)

Checked explicitly. Prefer these over a Raspberry Pi OS guess when they hit and the smoking-gun rules do not:

| Marker | Guess |
|--------|--------|
| `dietpi.txt` | DietPi |
| `SYSTEM` + `KERNEL` | LibreELEC |
| `vmlinuz` + ubuntu text / `ID=ubuntu` | Ubuntu for Raspberry Pi |
| `recovery.cmdline`, `os.json`, PINN/NOOBS files | PINN/NOOBS |
| Kali filenames / `ID=kali` | Kali |
| `os-release` ID fedora/arch/manjaro/alpine/… | that distro |
| cmdline squashfs / `boot=live` | live/appliance image |

PINN/NOOBS **plus** an official `issue.txt` is reported as Raspberry Pi OS with a warning that a multi-boot manager is present.

If a smoking gun and a strong foreign marker both fire, the verdict is `other_pi_os` (conservative).

## Extra facts

- `first_boot_resize_pending` when `cmdline.txt` contains `resize` **and** there is ≥ 1 GB trailing free space. Scanning only the boot directory cannot see trailing space; the tool warns and reports `false` unless you pass the whole disk.
- Likely boards from kernel / DTB names (`kernel_2712.img` / `bcm2712*` → Pi 5, …)
- Cloud-init files (`user-data`, `meta-data`, `network-config`) as a fact, not a verdict
- Warning when rootfs was unreadable

Debian codenames are never inferred from the image date. Without a readable `os-release` (or raspi.list suite), the hint is “Debian release unknown (image dated …)”.

## Adding a rule

Keep matchers in `evidence.py` small and I/O-free. Register on `RULES` with a stable `id`, `Weight`, `category` (`official_smoking`, `official_support`, `like`, `firmware`, `layout`, `negative`, `fact`), and optional `os_guess`. Add a test that would have failed before the rule existed.
