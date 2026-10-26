# Architecture

Logical components remain in two executable Python files; this is not a package
or modular rewrite. Both commands are versioned and installed together.

```mermaid
flowchart LR
  caller[User or automation] --> cli[CLI and exit codes]
  subgraph envctl[bin/envctl]
    cli --> config[File and identity configuration]
    config --> document[Literal dotenv document]
    document --> storage[Atomic replacement]
    document --> output[Keys or explicit values]
    document --> runner[Environment and exec]
    cli --> backup[Backup adapter]
  end
  storage --> plain[(Plaintext file)]
  storage --> sops[SOPS subprocess pipes]
  sops <--> encrypted[(Encrypted dotenv)]
  identity[age identity and recipients] --> sops
  backup --> bakctl[bin/bakctl]
  bakctl --> snapshots[(Snapshots and restore)]
  runner --> child[Child sees all injected values]
```

| Component | Implementation | Contract |
|---|---|---|
| CLI | `main`, `cmd_*` | Commands, validation, exit codes; `--version` identifies paired version |
| Configuration | `ENVCTL_FILE`, `SOPS`, `AGE_KEY_FILE`, `_sops_config` | Explicit file, SOPS executable, identity, adjacent recipient config |
| Document | `_find`, `_kv`, set/unset | Literal lines; preserves unrelated lines; no shell evaluation |
| Storage | `_load`, `_write`, `_mutation_lock` | 0600 new files; existing modes retained; atomic replacement; cooperating writers serialized |
| SOPS adapter | `_sops_decrypt`, `_sops_encrypt_write` | Encrypt via pipe; decrypt candidate and compare before replacing original |
| Backup adapter | `_backup`, history/restore | Resolve real script path to same-directory bakctl; ciphertext snapshots for `.enc` |
| Runner/output | exec/get/export/list | exec inherits and injects; get/export reveal; list prints key names |

```mermaid
flowchart LR
  source[Reviewed source commit] --> ci[Linux and macOS tests]
  ci --> tag[Tag pinned to commit]
  tag --> build[Allowlisted deterministic build]
  build --> release[Tarball / manifest / SHA256SUMS / source SHA]
  release --> install[Verify and install version directory]
  install --> active[Atomically select current pair]
  active --> verify[Execute installed commands and compare version]
```

Repository layout: `bin/` commands, `tests/` synthetic tests, `scripts/` build and
install helpers, `docs/` contracts, `examples/` synthetic configuration, and
`.github/workflows/` CI and release gates. External secret stores, synchronization,
node enrollment, and deployment systems belong to consumers.
