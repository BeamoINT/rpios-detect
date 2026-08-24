# Security

`rpios-detect` is **read-only**. It must never write to, format, or erase storage.

## How to report a problem

If the tool can write to a disk, run a destructive `diskutil`/`dd`/`mkfs` command, or treat an internal system disk as removable media without an explicit path, **do not file a public issue with a full exploit write-up**.

Use [GitHub Security Advisories](https://github.com/BeamoINT/rpios-detect/security/advisories/new) on this repository.

Include:

- What you ran (argv, OS)
- What it wrote or which command it executed
- Version / git commit

Detection false positives/negatives (wrong verdict, not a write) can be public issues.

## Policy

Do not add write paths, auto-mount read-write, flashing, or “fix this card” features.
