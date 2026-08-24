# Contributing

Firmware on a Pi boot partition is not Raspberry Pi OS. Keep that distinction.

## Rules

- Keep detection I/O-free: matchers in `evidence.py`, verdicts in `detect.py`.
- A firmware-only fixture must not return `raspberry_pi_os`.
- If the honest answer is uncertain, return `unknown`.
- Never commit real SD images, secrets, or write-to-disk code.
- Watch-mode tests must mock discover/eject. Do not auto-eject a real inserted card in CI.
- `main` should stay releasable.

## Checks

```bash
python3 -m pip install -e ".[dev]"
python3 -m pytest
```

Open PRs into `main`. Add a test when you add a rule. Document new negative OS markers in [docs/DETECTION.md](docs/DETECTION.md).
