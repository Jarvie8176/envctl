# Format and compatibility

| Item | Contract |
|---|---|
| Runtime | Python 3.10+ on Linux/macOS; POSIX fcntl locks; Windows unsupported |
| File selection | `--file` > `ENVCTL_FILE` > `~/cc/.env` |
| Encryption | Only filenames ending in `.enc` use SOPS |
| SOPS binary | `ENVCTL_SOPS_BIN` > PATH `sops` > historical `/opt/homebrew/bin/sops` fallback |
| Identity | `SOPS_AGE_KEY_FILE` > `~/.config/sops/age/keys.txt`; other SOPS environment settings inherited |
| Recipients | Adjacent `.sops.yaml` passed explicitly when present; otherwise SOPS config discovery/environment applies |
| Pairing | bakctl resolved next to the real envctl script, including through symlinks |
| Configuration | `~/.config/bakctl/config.yaml`; PyYAML optional for default operations, required for YAML config |
| Exit | 0 success/no-op; get missing or operational error 1; argparse usage error 2; exec replaces process and carries child status |

SOPS is external. Acceptance uses SOPS 3.13.1 and age 1.2.1; untested versions
are not guaranteed. age-keygen generates an identity; envctl invokes SOPS, not
age-keygen, during normal operations.

## Literal dotenv subset

- `KEY=value` lines; identifiers match `[A-Za-z_][A-Za-z0-9_]*`.
- Quotes are literal; `A="hello"` yields the quote characters. No interpolation,
  escape processing, `export` prefix, inline comment interpretation, or multiline
  values. `A=x # y` contains `x # y`. Do not treat arbitrary shell scripts as input.
- get/set/unset match the first exact `KEY=` prefix. Existing duplicate keys are
  preserved; list/export iterate every valid assignment and exec uses the last
  duplicate. Avoid duplicates. They are not normalized automatically.
- list/export/exec strip surrounding whitespace from the whole line before
  splitting on the first `=`; get returns the exact value after the matched `=`.
  Avoid leading/trailing whitespace when equivalent behavior is required.
- Comments, blank lines, and unrecognized lines remain during ordinary edits.
  Rewriting normalizes line endings to LF and adds a trailing newline. SOPS may
  normalize dotenv representation; candidates that do not round-trip are refused.
- stdin set strips one trailing LF. Embedded CR/LF or NUL is rejected. Setting the
  same first matching line is a no-op and produces no backup. Unsetting a missing
  key is a no-op. Empty encrypted documents use a comment marker.
- Compared with the pre-release implementation, invalid keys, multiline input,
  symlink/hardlink mutations, and competing writers now fail rather than risk
  injection or lost changes. There is no silent default-file migration.

## Backups

Snapshots use `FILE.bak.UNIX_TIMESTAMP`; ordering/retention follows the numeric
suffix, not copied mtime. Same-second snapshots advance the suffix. Default
retention: all within 24h, one/day within 7d, one/week within 14d, max 30; the newest
snapshot survives tiered pruning. `prune --keep 0` explicitly deletes all snapshots.
History's date column is source mtime; its first column is the restore timestamp.
Restore first saves `FILE.pre-restore.TIMESTAMP`, then replaces the target atomically.
Standalone bakctl operations need external serialization. Config is not schema
validated; use documented keys and sensible nonnegative values.
