# Changelog

All notable changes to `cms_mssp_pipeline` are recorded here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-09-04

First formal release of the converged baseline. Validated end to end in a
client dev and prod deployment on 2026-09-03/04.

### Added

- Fail-closed input and source-discovery spine. Every value the pipeline
  splices into a filesystem path or a DuckDB glob (the ACO id and the file
  store) is validated before use; a malformed identifier or a source URI with
  an unknown scheme, query string, fragment, traversal segment, or glob
  operator fails the run instead of silently broadening a glob. The effective
  ACO id and file store are resolved once and shared by the download and
  processing sides. Workbook discovery distinguishes an empty prefix from an
  unlistable one and raises `SourceDiscoveryError` on a listing failure.
- Workbook export contract v1 (`contracts/workbook/v1.json`): the versioned,
  machine-readable description of the twenty BNMRK, AEXPU, and QEXPU cell-grain
  relations, their shared schema, delivery cadence, and optional-sheet
  behavior, with conformance tests that run every relation from synthetic
  fixtures and check it column for column against the contract (TUVA-24).
- Append-only run evidence (`mssp_pipeline/evidence/`): a neutral record graph
  (run started, stage attempted, stage reused, prerequisite checked, stage
  blocked, metric observed, artifact observed) written through an
  `EvidenceSink` to an atomic JSONL log. The run manifest is now a projection
  over the evidence stream rather than the authority for what happened, with
  the public API and on-disk shape unchanged.
- Hardened runtime image and release provenance. The base image is pinned by
  digest, dependencies are installed frozen from the committed `uv.lock`, the
  runtime runs as a non-root user, and the bundled CMS ACO-MS CLI is verified
  against `release/cms-binaries.sha256`. Source commit, release id, and
  dependency checksum are baked into build args, environment, and OCI labels;
  `scripts/build-and-push-image.sh` requires a clean checkout and an
  immutable registry and records the pushed `repository@sha256` digest in
  release metadata, which `scripts/verify_release_metadata.py` proves against
  the checkout. Secret scanning configuration is included.
- Parameterized Terraform substrate: a remote-state bootstrap module with S3
  native locking and a non-personal deployment role; foundation hardening
  (optional managed data bucket, immutable scan-on-push ECR repositories,
  least-privilege readiness IAM, configurable log retention, an allowed-account
  guard); immutable image tokens in the ECS task definitions with a
  non-essential readiness-gates sidecar; and a deploy engine that binds exactly
  the registered task revisions and requires a `repository@sha256` image. A
  genericity test proves no client identity is present in the tracked
  substrate.
- Per-stage task-definition rendering (`scripts/render_taskdefs.py`): every
  `taskdef-*.json` template in `infra/aws/ecs/` is discovered, rendered from
  one placeholder map (per-stage role ARNs, connector image, Snowflake
  environment and secrets, per-environment database and query tag), registered
  by family, and fails closed on any unresolved placeholder (TUVA-41).
- Sequencer engine and primitives (TUVA-44). `mssp-sequence` runs an ordered
  plan of ECS stages, gating each on the run lease (`lease.py`), the named
  readiness policy (`readiness.py`), exact image and task-revision identity
  (`release_identity.py`), and, where declared, the output contract
  (`output_contract.py`), halting on the first failure. Seams for the ECS
  client, lease backend, and readiness and output sources allow cloud-free
  testing. Fencing tokens order across runs so a stale redrive cannot out-fence
  a fresher run, a stage that pins neither image nor task revision is rejected
  at plan validation, and a launch failure halts the sequence cleanly.
- DynamoDB lease backend (`mssp_pipeline/lease_backends.py`) implementing the
  sequencer's lease protocol with conditional writes, plus a reusable
  Terraform module (`infra/terraform/aws/modules/lease-table`) for the lock
  table (TUVA-49).
- Readiness gates injected from SSM (TUVA-52). The readiness-gates sidecar
  reads each gate as an ECS secret whose `valueFrom` is the foundation's SSM
  parameter ARN, resolved by the render engine from region and account; task
  execution roles are granted `ssm:GetParameters` on exactly those parameters;
  and the render refuses any template whose readiness container does not carry
  the gate secrets or that asserts a gate as a plain environment value.
- `information_schema` output source and Snowflake session seam (TUVA-53).
  `InformationSchemaOutputSource` reads a warehouse's `information_schema.tables`
  and reports the produced placement for the outputs a stage's contract names.
  The Snowflake connection and private-key loading are extracted into
  `mssp_pipeline.processing.snowflake_session` so an overlay reuses the
  exporter's credential path.
- Sequencer overlay-fetch shim (`mssp_pipeline.sequencer_overlay`) that
  materializes a client plan overlay from an object-store prefix at task start
  and hands over to the sequencer, failing closed on a missing URI, an overlay
  with no `sequencer/` directory, or any key that would escape the destination
  (TUVA-61).

### Changed

- Client-run scripts (`deploy-and-smoke-client.sh`,
  `run-client-process-task.sh`) take a release id, hand the digest recorded in
  its release metadata to the deploy engine, and run the exact task revision
  recorded at register time; a mutable image, a bare family ARN, or a digest
  that differs from the recorded revision fails closed (TUVA-39).
- The sequencer waits for a stage on a generous, environment-tunable budget
  (`MSSP_TASK_WAIT_DELAY_SECONDS`, `MSSP_TASK_WAIT_MAX_ATTEMPTS`; default two
  hours per stage) instead of the ten-minute default ECS waiter.
- The genericity guard scans git-tracked files rather than the working tree, so
  untracked build output cannot fail it (TUVA-42). `AGENTS.md` now states that
  `uv.lock` is committed and must be regenerated when dependencies change
  (TUVA-50).
- Deployment documentation (container contract, IAM minimum policies, client
  example) describes the readiness secrets and the digest-bound deploy flow.

### Removed

- Mutable `:latest` image tags and latest-revision task family discovery from
  the deploy path; every image reference is a `repository@sha256` digest and
  every task an exact revision.
- DynamoDB state locking for Terraform remote state, replaced by S3 native
  locking in the bootstrap module.

### Fixed

- The Snowflake exporter creates table columns in UPPERCASE so the connector's
  unquoted model references resolve; previously a freshly recreated table
  carried case-sensitive lowercase columns inferred from the staged Parquet.
- MCQM archive listing fails closed: an unlistable inner zip-content glob raises
  `SourceDiscoveryError` instead of being read as an empty delivery, and one
  empty archive no longer hides the members of the others (TUVA-38).
- Output-backend augmentation lands on the essential workload container rather
  than whichever container is first in the template, so the Snowflake
  environment and key secrets reach the runtime and not the readiness sidecar
  (TUVA-47).
- A sequencer stage succeeds only when every container in the task exited 0;
  a failed workload can no longer be masked by the sidecar's exit code.
- The CMS binary checksum manifest lists only the Linux CLI that ships in the
  image, so the in-container verification no longer fails on the excluded
  macOS binary.

[Unreleased]: https://github.com/tuva-health/cms_mssp_pipeline/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/tuva-health/cms_mssp_pipeline/releases/tag/v0.2.0
