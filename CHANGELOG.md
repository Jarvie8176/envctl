# Changelog

## Unreleased

- Exercise installed release archives in a non-root, network-disabled Docker
  container without PyYAML; include that E2E job in release acceptance.

- Add publication privacy gates for source, new commit metadata, complete secret
  history and built artifacts; require them before release upload.
- Remove private extraction provenance from current source and generated assets.
  Earlier public history and releases require separate cleanup.

## 0.1.0 — 2026-10-26

- Initial independent MIT release; envctl and bakctl distributed together.
- Preserve commands, historical default file, `.enc` selection, and backup layout.
- Add paired version output, immutable version-directory installation, rollback,
  deterministic release artifacts, manifest, checksums, and exact source commit.
- Validate encrypted round-trips before replacing carriers; protect against
  accidental recipient self-lockout, failed dependencies, and backup failures.
- Reject unsafe keys, multiline values, linked mutation targets, and competing
  envctl writers. Restore via atomic replacement with unique pre-restore copies.
- Document literal format, permissions, concurrency, plaintext output, and trust
  boundaries. Add real SOPS/age synthetic acceptance and Linux/macOS CI.
