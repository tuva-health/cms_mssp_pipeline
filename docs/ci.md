# Continuous integration (PR gate)

`.github/workflows/ci.yml` runs on every pull request targeting `main` and on
every push to `main`. It needs no secrets and touches no client account: the
only network traffic is to package and provider registries. A newer run on the
same ref cancels the older one.

## Required checks

Branch protection on `main` requires these five jobs. The names are stable;
rename one only together with the protection rule.

| Job | What it proves |
| --- | --- |
| `test` | `uv sync --frozen` (the `dev` dependency group carries every import the tests need, so no extras are installed) then `uv run --frozen pytest`, run in sequence on Python 3.10 (the `requires-python` floor) and 3.11 (the runtime image's interpreter) so the check keeps a single stable name. The suite covers the processing engine, the sequencer, the lease backends (against moto), the render goldens, and the genericity guard that keeps client literals out of this repository. |
| `lock` | `uv lock --check`: `uv.lock` is consistent with `pyproject.toml`, so the frozen image install is a function of the checkout alone. |
| `terraform` | `terraform fmt -check -recursive`, then `terraform init -backend=false` and `terraform validate` for every root under `infra/terraform/aws/` (`foundation`, `bootstrap`, `activate`, `modules/lease-table`). Terraform version comes from `.terraform-version`. |
| `shell` | `shellcheck scripts/*.sh docker/*.sh` at the default severity. |
| `image` | `docker build` for `linux/amd64` with the same build arguments `scripts/build-and-push-image.sh` passes (`SOURCE_COMMIT`, `RELEASE_ID`, `DEPENDENCY_CHECKSUM`, `PIP_EXTRAS=processing,snowflake`). Proves the digest-pinned base image resolves, the frozen install succeeds, and the bundled CMS binaries match `release/cms-binaries.sha256`. The image is never pushed or loaded; layers are cached in the GitHub Actions cache. |

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
- Release builds. `scripts/build-and-push-image.sh` remains the only path that
  produces a tagged, digest-recorded image.
- Multi-architecture images. The runtime target is `linux/amd64` only.
