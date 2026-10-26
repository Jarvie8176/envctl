# Integrations and adoption

- This repository is the source authority for published versions. Consumers pin a
  release tag and full source commit from RELEASE.json; avoid maintaining divergent
  editable copies. Release archives contain both executables and their contracts.
- Secret managers may pipe a value into `envctl --file FILE set KEY`. The core
  does not require a particular secret manager, vault, host, or deployment system.
- Synchronization, node enrollment, container deployment, identity provisioning,
  and credential rotation stay in the consumer's integration layer.
- Use absolute file paths in automation. Execute the installed command's
  `--version`; checking a source checkout alone does not verify the running copy.
- Symlink installations must resolve into the same version directory. Copy-based
  enrollment must copy the **whole verified release** or both sibling commands
  with matching source metadata, then verify the actual executables on the node.
- Adopt in a separate prefix first. Verify synthetic encrypted reads, writes,
  backups and restores before switching an existing consumer. Preserve the prior
  installation and data backups for rollback. No fleet rollout is implicit.
- Updating a source repository does not update copied tools. Every enrolled node
  requires an explicit deployment and verification step.
