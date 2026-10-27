# Security model

- Trust the operating system, Python, SOPS, configured identity source, package,
  and the target directory. Use a private, owner-controlled directory.
- Installation also requires owner-controlled source and prefix directories.
  Manifest checks detect corruption; the installer itself is executable code
  from the trusted package. Path checks and the advisory installation lock do
  not defend against hostile concurrent directory changes by the same OS user.
  Normal pre-activation errors preserve the selected version. SIGKILL or power
  loss can leave staging files or partial links; no crash-durability guarantee
  is provided.
- `.enc` selects SOPS dotenv encryption. Other filenames are plaintext, including
  the preserved default `~/cc/.env`. Files and backups are not auto-migrated.
- Plaintext is passed through subprocess pipes and process memory by envctl.
  This does not prevent swap/core dumps, child-process writes, logs, terminal
  capture, or inspection by the same OS user or an administrator.
- `get`/`export` print values intentionally. `exec` passes every parsed variable
  to the child and inherits the caller environment. No credential broker,
  per-command allowlist, automatic redaction, or agent isolation is provided.
- Prefer stdin for `set`; positional values can appear in shell history/process
  inspection. Keys must be shell identifiers. Values must be single-line without
  NUL. Invalid legacy keys are ignored by list/export/exec.
- Before a changed value is written, backup must succeed. Encrypted candidates
  must decrypt under the currently configured identity and match their input.
  Wrong identities, inaccessible recipients, or SOPS errors leave the original
  carrier unchanged. A failed attempt may leave a ciphertext backup and may
  already have applied configured backup retention.
- Existing file modes are preserved, including permissive modes: inspect and
  correct them yourself. New target files and mutation locks are 0600. Snapshot
  modes follow their source. No automatic key creation or key distribution.
- envctl mutations take a nonblocking POSIX advisory lock and reject symlink and
  hardlink targets. Keep the sibling `.envctl.lock` file; deleting it while a
  process runs defeats coordination. Reads see the old or new file after rename.
- Only cooperating envctl writers at the same pathname are coordinated. Direct
  editors, standalone bakctl operations, aliases through different directory
  paths, network filesystems, and hostile directory changes are not supported
  concurrently. Serialize those externally. This is not a filesystem sandbox.
- Writes and restores use atomic replacement in the destination directory. Before
  replacement, ordinary exceptions clean the temporary file. SIGKILL can leave
  an orphan temp file (ciphertext for `.enc`). No fsync crash-durability promise;
  power loss and filesystem failures are outside the guarantee.
- bakctl snapshots `.enc` bytes without decrypting. Restoring a snapshot does not
  restore revoked service credentials or validate that its identity is still
  available. Pre-restore snapshots are retained separately and not auto-pruned.
- Error messages from envctl read/write paths do not include subprocess stderr,
  argv, or document values. Child programs and standalone bakctl diff/config
  output are explicit output surfaces, not redacted logs.

Tests use temporary identities and synthetic values, including wrong-key,
recipient-change, empty-document, backup/restore, lock, permissions, and failed
replacement cases. Never attach real dotenv files, private identities, or secret
values to public issues; report reproducible failures using synthetic data.
