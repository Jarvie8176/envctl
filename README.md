# envctl

Edit dotenv files, keep versioned backups, and run commands with their values.
Files ending in `.enc` use SOPS with age. `envctl` and its sibling `bakctl` ship
as one MIT-licensed release. Python 3.10+; Linux and macOS. No daemon or service.

## Install a fixed release

Install Python 3.10+. For encryption, install [SOPS](https://github.com/getsops/sops)
and [age](https://github.com/FiloSottile/age) (`age-keygen` creates identities).
PyYAML is optional: default backup retention works without it; YAML config
reads/writes require it. Development tests require pytest and PyYAML.

Download these assets from the [v0.1.0 release](https://github.com/Jarvie8176/envctl/releases/tag/v0.1.0):

```sh
curl -fLO https://github.com/Jarvie8176/envctl/releases/download/v0.1.0/envctl-v0.1.0.tar.gz
curl -fLO https://github.com/Jarvie8176/envctl/releases/download/v0.1.0/SHA256SUMS
curl -fLO https://github.com/Jarvie8176/envctl/releases/download/v0.1.0/MANIFEST.json
curl -fLO https://github.com/Jarvie8176/envctl/releases/download/v0.1.0/RELEASE.json
shasum -a 256 -c SHA256SUMS
tar -xzf envctl-v0.1.0.tar.gz
sh envctl-v0.1.0/scripts/install.sh --prefix "$HOME/.local"
"$HOME/.local/bin/envctl" --version
"$HOME/.local/bin/bakctl" --version
```

Add `$HOME/.local/bin` to PATH. The installer verifies file hashes and installs
both tools under `PREFIX/lib/envctl/0.1.0/`. Stable command links follow a single
atomically switched `current` link. It refuses to replace unmanaged commands.
Install a different prefix if you already have another `envctl` or `bakctl`.
Hashes provide integrity, not independent publisher authentication: trust the
repository, release tag, and download channel. `RELEASE.json` records the exact
public source commit. Private provenance is not part of the distribution.

Upgrade by installing another verified release into the same prefix. Roll back
by running the earlier release's installer again. Existing version directories
are immutable: differing files under the same version are refused. Keep previous
release archives. No automatic updater or `curl | sh` installer is provided.

## Encrypted example

Use a fresh directory and generate your own identity; never commit its private key.

```sh
mkdir -p demo
chmod 700 demo
age-keygen -o demo/identity.txt
age-keygen -y demo/identity.txt > demo/recipient.txt
printf 'creation_rules:\n  - age: %s\n' "$(cat demo/recipient.txt)" > demo/.sops.yaml
export SOPS_AGE_KEY_FILE="$PWD/demo/identity.txt"
printf '%s' 'synthetic-example' | envctl --file demo/app.env.enc set APP_TOKEN
envctl --file demo/app.env.enc list
envctl --file demo/app.env.enc exec -- python3 -c 'import os; assert "APP_TOKEN" in os.environ'
envctl --file demo/app.env.enc history
```

Use `envctl --file FILE restore TIMESTAMP` to restore a backup listed by history.
`get KEY` and `export` deliberately print values. `exec` inherits the caller's
environment and injects **all** parsed variables into the child. It provides no
per-command allowlist or protection from programs running as the same OS user.

The historical default is `~/cc/.env` (plaintext). Set `--file` explicitly or
`ENVCTL_FILE`; only the `.enc` suffix enables encryption. The tool edits literal
single-line assignments, not shell scripts. See [format and compatibility](docs/format-and-compatibility.md).

## Components and development

- [Architecture and component boundaries](docs/architecture.md)
- [Security model and limitations](docs/security-model.md)
- [Integrations and adoption](docs/integrations.md)
- [Publication privacy checks](docs/publication-checks.md)
- [Changelog](CHANGELOG.md)

```sh
python3 -m pip install pytest PyYAML
python3 -m pytest tests -q
python3 scripts/build.py
```

Release acceptance tests require real `sops` and `age-keygen` on PATH, generate
disposable identities, and fail when those dependencies are absent. Builds require
a clean committed tree and emit a deterministic tarball, manifest, checksums, and
source metadata. The PR workflow checks Linux and macOS before release publication.

The `docker-e2e` CI job also installs the built archive in a digest-pinned Python
3.13 container. The container runs as a non-root user with a read-only root
filesystem, no network, no host mounts, and no PyYAML. It tests the installed
commands against real SOPS/age using disposable identities: CRUD/exec, encrypted
backups/restores, wrong-key/recipient failure protection, file permissions, locks,
paired installation, tamper refusal, and upgrade/rollback with a synthetic
next-version fixture. This job is part of the reusable release gate.

To reproduce it on a Docker-capable machine from a clean committed checkout:

```sh
context="$(mktemp -d)"
python3 scripts/build.py --output "$context/package"
python3 scripts/ci-dependencies.py "$context/dependencies"
cp tests/docker/Dockerfile tests/docker/e2e.py "$context/"
docker build --tag envctl-e2e:local "$context"
docker run --rm --network none --read-only \
  --tmpfs /tmp:rw,exec,nosuid,nodev,size=128m --cap-drop ALL \
  --security-opt no-new-privileges --pids-limit 128 \
  --memory 256m --cpus 2 envctl-e2e:local
```
