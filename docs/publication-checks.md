# Publication checks

The `publication-privacy` CI job runs on pull requests, main pushes, manual runs,
and release tags. Release publication depends on the complete check workflow.
The publishing job also scans the exact archive it is about to upload.

| Surface | Check |
|---|---|
| Tracked source at the publication tip | Approved repository references; public commit provenance; email, session, private-network, personal-path and identity-file rules |
| New commits | Author and committer use GitHub noreply addresses; commit messages and added content pass metadata checks, including content deleted in a later commit |
| All reachable Git history | Pinned Gitleaks default credential rules, with inline allow comments disabled |
| Built assets and tar members | Same privacy rules, regular safe members only, strict RELEASE.json schema and exact public source SHA |
| Release publisher | Rebuild and check its own upload directory before calling the GitHub Release API |

The metadata-history boundary is the public v0.1.0 merge commit, recorded in
`.github/privacy-policy.json`. Existing metadata in earlier commits remains a
known remediation item; this boundary does **not** declare that history clean.
Current source and every new artifact get full metadata checks. Credential scans
have no historical exemption. Policy and allowlist changes require human review.
Approved external commit hashes refer only to pinned public Actions dependencies.

Run before pushing, from a clean committed checkout with full history:

```sh
python3 scripts/build.py
python3 scripts/check-privacy.py --artifacts dist
gitleaks git . --log-opts=HEAD --config .github/gitleaks.toml --redact --ignore-gitleaks-allow
gitleaks dir dist --max-archive-depth 2 --config .github/gitleaks.toml --redact --ignore-gitleaks-allow
```

CI pins Gitleaks 8.30.1 and its download checksum. Findings print rule names and
object numbers or public commit IDs, not matched values or potentially private
filenames. Inspect the local change to identify the offending value. Unknown
binary source/asset formats and incomplete history fail closed.

Use a GitHub noreply email for both author and committer before creating commits.
GitHub can choose an account email when generating merge commits: select a noreply
merge-author email and verify the resulting commit. The main-push check verifies
that actual metadata and blocks later release publication on failure.

CI runs after data has reached GitHub. It cannot prevent the initial exposure of
an unsafe push, retract cached PR revisions, or remove old release downloads.
Run the local checks first and review public content manually. The rules are
heuristics, not a complete organization-aware privacy detector. Branch-protection
required-check settings are an owner/admin control; adding this job alone does
not configure them. Keep `publication-privacy` required where that control exists.
