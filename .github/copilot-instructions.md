# AI agent instructions — rpios-detect

Read-only CLI that answers whether a MicroSD card, mounted boot volume, directory, or `.img` contains **Raspberry Pi OS**. Firmware files are not enough.

## Safety

Never write to disks. Mounts must be read-only. Destructive argv (`dd`, `mkfs`, `diskutil erase*`, read-write `mount`) is refused in `safety.py` before exec. Do not weaken that guard. Do not treat internal system disks as SD cards unless the user passed that path explicitly.

## Layout

- `src/rpios_detect/detect.py` — pure evidence → verdict. No subprocess.
- `src/rpios_detect/evidence.py` — rule table. Add OS negative-markers here.
- `src/rpios_detect/snapshot.py` / `fs.py` — host-agnostic file view.
- `src/rpios_detect/probe_*.py` — macOS diskutil / Linux lsblk / Windows.
- `src/rpios_detect/image.py` / `fat.py` — read-only `.img` parse.
- `docs/DETECTION.md` — human rules. Keep it in sync with the matcher table.

Raspberry Pi OS boot filenames are `issue.txt`, `cmdline.txt`, `config.txt`, `bootcode.bin`, `start.elf`, `kernel8.img`, `kernel_2712.img`, `LICENCE.broadcom`. Do not “fix” those to Debian generic names.

## Gate

```bash
python3 -m pip install -e ".[dev]"
python3 -m pytest
```

No physical card in CI. GitHub Actions on this org is often billing-blocked; **local pytest is the real gate**.

Never call firmware-only media `raspberry_pi_os`. If unsure, return `unknown`.

Keep `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `GROK.md`, and `.github/copilot-instructions.md` byte-identical:

```bash
/Users/HP/dev/sync-ai-memory.sh --repo .
```
