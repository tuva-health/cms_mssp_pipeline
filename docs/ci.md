# Continuous integration (PR gate)

`.github/workflows/ci.yml` runs on every pull request targeting `main` and on
every push to `main`. It needs no secrets and touches no client account: the
only network traffic is to package and provider registries. A newer run on the
same ref cancels the older one.

## Required checks

Changes land on `main` only through a pull request. Branch protection on
`main` requires these five jobs to pass on a branch that is up to date with
`main`; it requires no approvals, blocks force-pushes and deletion, applies to
admins, and allows merge commits and squash merges (not rebase merges). Jobs
not listed here run but do not gate the merge. The names are stable; rename
one only together with the protection rule.

| Job | What it proves |
| --- | --- |
| `test` | `uv sync --frozen` (the `dev` dependency group carries every import the tests need, so no extras are installed) then `uv run --frozen pytest`, run in sequence on Python 3.10 (the `requires-python` floor) and 3.11 (the runtime image's interpreter) so the check keeps a single stable name. The suite covers the processing engine, the sequencer, the lease backends (against moto), the render goldens, and the genericity guard that keeps client literals out of this repository. |
| `lock` | `uv lock --check`: `uv.lock` is consistent with `pyproject.toml`, so the frozen image install is a function of the checkout alone. |
| `terraform` | `terraform fmt -check -recursive`, then `terraform init -backend=false` and `terraform validate` for every root under `infra/terraform/aws/` (`foundation`, `bootstrap`, `activate`, `modules/lease-table`). Terraform version comes from `.terraform-version`. |
| `shell` | `shellcheck scripts/*.sh docker/*.sh` at the default severity. |
| `image` | `docker build` for `linux/amd64` with the same build arguments `scripts/build-and-push-image.sh` passes (`SOURCE_COMMIT`, `RELEASE_ID`, `DEPENDENCY_CHECKSUM`, `PIP_EXTRAS=processing,snowflake`). Proves the digest-pinned base image resolves, the frozen install succeeds, and the bundled CMS binaries match `release/cms-binaries.sha256`. The image is never pushed or loaded; layers are cached in the GitHub Actions cache. |

## Synthetic end-to-end job (`e2e`, not required)

`e2e` runs `tests/e2e/synthetic_run.py`: it writes a synthetic CMS delivery
(the openpyxl BNMRK / AEXPU / QEXPU workbook fixtures plus one small
fixed-width file per CCLF type, all fake values) and drives a three-stage plan
through the real sequencer engine with the in-memory lease store, a synthetic
readiness source and a fake ECS client whose tasks run locally:

| Stage | Task | Gate after it |
| --- | --- | --- |
| `process` | the real `mssp-process` with `MSSP_OUTPUT_TYPE=PARQUET` | readiness (`bootstrap`, `whitelist`), exact image digest + task revision before launch |
| `load` | every Parquet output loaded into a local DuckDB (`raw_data`) | output contract: the twenty relations of `contracts/workbook/v1.json`, read through `InformationSchemaOutputSource` |
| `conformance` | every contracted table checked column-for-column and type-for-type against the contract; CCLF row counts | task exit code |

The clean run must pass; then each injected fault must halt the sequence at
the stage and gate that should catch it (the job fails if a fault slips
through):

| `--fault` | Defect | Caught at |
| --- | --- | --- |
| `uppercase-columns` | exporter skips the lowercase column normalisation | `conformance` / task |
| `missing-table` | exporter silently drops `bnmrk_table_1` | `load` / output-contract |
| `readiness-blocked` | `whitelist` gate reads `false` | `process` / readiness |
| `lease-held` | another run holds the lease | `process` / lease |
| `image-drift` | a family resolves to a different digest than the plan pins | `load` / image-identity |

It is deliberately **not** a required check while it beds in; add it to branch
protection only together with the rule. Run it locally exactly as CI does:

```bash
uv run --frozen python -m tests.e2e.synthetic_run --workdir /tmp/mssp-e2e
uv run --frozen python -m tests.e2e.synthetic_run --workdir /tmp/mssp-e2e --fault missing-table
```

Not covered: the download subsystem (`acoms-cli`), MSSP CSV / MCQM / BNEX
deliveries, cloud exporters, the container image, and real ECS/SSM/DynamoDB --
the ECS client, lease store and readiness source are the same fakes the unit
tests use.

Tool versions are read from the files that already pin them: `uv` from the
Dockerfile's `UV_VERSION` argument and Terraform from `.terraform-version`.

## Running the same checks locally

```bash
uv sync --frozen && uv run --frozen pytest
uv lock --check
(cd infra/terraform/aws && terraform fmt -check -recursive)
for root in foundation bootstrap activate modules/lease-table; do
  terraform -chdir="infra/terraform/aws/$root" init -backend=false
  terraform -chdir="infra/terraform/aws/$root" validate
done
shellcheck scripts/*.sh docker/*.sh
docker buildx build --platform linux/amd64 \
  --build-arg "SOURCE_COMMIT=$(git rev-parse HEAD)" \
  --build-arg RELEASE_ID=local \
  --build-arg "DEPENDENCY_CHECKSUM=$(shasum -a 256 uv.lock | cut -d ' ' -f 1)" \
  --build-arg PIP_EXTRAS=processing,snowflake .
```

`tests/test_lease_backends.py` fails when `AWS_PROFILE` names a profile the
machine does not have (an empty string included). The `test` job unsets it;
locally, leave it unset rather than exporting `AWS_PROFILE=`.

## Deliberately not covered

- Anything that needs a client account: live CMS Datahub downloads, Snowflake
  or other warehouse exports, S3 or DynamoDB against real AWS, ECR pushes,
  `terraform plan`/`apply` against a remote backend. Those run from the client
  repositories, which carry the account, bucket, and credential configuration
  this repository must not.
- Release builds. `ci.yml` never runs `scripts/build-image.sh`; the release
  workflow below does.
- Multi-architecture images. The runtime target is `linux/amd64` only.

# Release workflow

`.github/workflows/release.yml` runs on a pushed `v*` tag. It uses only the
workflow's `GITHUB_TOKEN`, and its one write is the GitHub Release; no image is
pushed and no registry credential exists. How to cut a release and how a client
compares its build are in the README (*Releases*).

| Job | What it does |
| --- | --- |
| `verify` | The tag is semver, equals `v` + the `pyproject.toml` version, its commit is an ancestor of `origin/main`, and `CHANGELOG.md` has a non-empty section for the version (`scripts/release_notes.py changelog`). |
| `build (<type>)` | `scripts/build-image.sh mssp-pipeline <tag>` for each `MSSP_OUTPUT_TYPE` with a distinct extras set (PARQUET, SNOWFLAKE, DATABRICKS, BIGQUERY, REDSHIFT, FABRIC) on a fresh docker-container builder with no cache, then `scripts/verify_release_metadata.py --repo .` on the metadata it wrote. |
| `build (PARQUET, rebuild)` | The same build again on another runner: the reproducibility check. |
| `release` | `scripts/release_notes.py notes --require-match` assembles the body (CHANGELOG section, workbook contract name, version and sha256, digest table, rebuild verdict). It fails when the rebuild's digests differ. On a tag push it then runs `gh release create --verify-tag --prerelease` with the metadata files and `image-digests-<tag>.json` attached. |

Any other trigger is a dry run: the `release` job writes the notes to the job
summary and uploads them with the assets as `release-dry-run-<tag>` instead of
creating a Release. The commit-on-`main` check is a warning, not an error. Dry
runs come from `workflow_dispatch` (optional `tag` input, default
`v<pyproject version>`) and from pull requests that change `release.yml`,
`Dockerfile`, `scripts/build-image.sh`, `scripts/release_notes.py`, or
`scripts/verify_release_metadata.py`. On a pull request the built commit is the
merge commit, so the digests differ from the ones the tag will produce.

Running a build by hand reproduces a `build` job exactly:

```bash
docker buildx create --use --name mssp-repro
MSSP_OUTPUT_TYPE=PARQUET scripts/build-image.sh mssp-pipeline v0.2.0 \
  --metadata /tmp/meta.json --record /tmp/record.json
python3 scripts/verify_release_metadata.py /tmp/meta.json --repo .
```
