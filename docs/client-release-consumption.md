# Consuming a release in a client fork

How a client fork takes a canonical release of `cms_mssp_pipeline` (and the
matching `cms_mssp_connector` release), proves with git that it runs that
release, deploys it, and reports back so the maintainer can promote the
release from pre-release to release.

This is the last step of the release loop that starts in the README
(*Releases*): the tag's workflow builds every backend variant, rebuilds one to
check the build reproduces, and creates a GitHub **pre-release**. Nothing is
pushed to a registry. A release becomes a full release only after a client has
run it end to end against real data, as below.

**The proof is a git check, not an image comparison.** A fork runs release
`vX.Y.Z` when its commit contains the tag and differs from it only in the
fork's client-specific paths. The fork then builds and deploys its own image
from that commit, as it always does. The image digests a release records are
Tuva's internal guarantee that a tag builds reproducibly (TUVA-68); clients do
not rebuild the tag or compare digests.

The connector's README (*Consuming a release in a client fork*) covers the
connector's own check (`scripts/check_release_conformance.sh`, the same rules
with the connector's client-specific paths).

## Summary

| Step | Who | What |
| --- | --- | --- |
| 1. Pick | client | A pipeline release and a connector release that name the same workbook contract version. |
| 2. Merge | client | Merge the tag into the fork through an `upstream-sync/vX.Y.Z` branch. Merge, never rebase; never a `main` tip. |
| 3. Check conformance | client | `scripts/check-release-conformance.sh vX.Y.Z` must print `PASS`. |
| 4. Build and deploy | client | Build, push and deploy the fork's merge commit as usual. |
| 5. Run | client | Run the fork's dev sequence. |
| 6. Report | client | Open `Validate vX.Y.Z (<client>)` with the conformance output and the dev-sequence results. |
| 7. Promote | maintainer | Flip the release from pre-release to release. |

In the commands below, `upstream` is the fork's remote for
`https://github.com/tuva-health/cms_mssp_pipeline`, `vX.Y.Z` is the release
tag, and `<client>` is the fork's overlay under `infra/clients/<client>/`.

## 1. Pick a matching pair

Each GitHub Release names a workbook contract. The pipeline's release notes
give the contract it exports (`contracts/workbook/v1.json`: name, version and
sha256); the connector's name the contract its benchmark models read
(`[tool.cms_mssp_connector] workbook_contract`). Take a pipeline release and a
connector release whose contract name and version are equal. The two repos'
version numbers are independent and need not match.

## 2. Merge the tag through `upstream-sync/*`

```bash
git fetch upstream --tags
git rev-parse 'vX.Y.Z^{commit}'     # must equal "Source commit" in the release notes
git switch -c upstream-sync/vX.Y.Z main
git merge --no-ff vX.Y.Z -m "Merge cms_mssp_pipeline vX.Y.Z"
git push origin upstream-sync/vX.Y.Z
```

Open a PR from `upstream-sync/vX.Y.Z` into the fork's `main` and merge it with
a merge commit.

- **Merge a tag, not `upstream/main`.** Only a tag has a release to validate.
  A `main` tip between releases is unreleased, and the check in step 3 fails
  on it.
- **Merge, don't rebase.** The tag commit stays an ancestor of the fork's
  `main`, which is half of the proof in step 3, and the next sync merges only
  what is new.
- **Resolve conflicts in client-specific paths only.** A conflict anywhere
  else means the fork has changed canonical code, and step 3 will fail.
- If the fork tags its own releases, keep its tags out of the `v*` namespace
  so they cannot collide with canonical tags.

## 3. Check conformance

Run the check on the commit you will deploy (the `upstream-sync` branch before
its PR merges, or the fork's `main` after):

```bash
scripts/check-release-conformance.sh vX.Y.Z           # checks HEAD
scripts/check-release-conformance.sh vX.Y.Z main      # or name the commit
```

It passes when both hold:

- the tag is in the commit's history:
  `git merge-base --is-ancestor vX.Y.Z <commit>`;
- every path in `git diff --name-only vX.Y.Z <commit>` is client-specific.

It prints `PASS` with the client-specific paths that differ, or `FAIL` with the
canonical paths that differ (or the missing ancestry), and exits non-zero on
`FAIL`. To be sure you run the release's copy of the check rather than one the
fork might have changed, run it from the tag:

```bash
git show vX.Y.Z:scripts/check-release-conformance.sh | bash -s -- vX.Y.Z
```

### Client-specific paths

| Path | Why it is the fork's |
| --- | --- |
| `infra/clients/<client>/**` (any name except `client.example`) | The deployment overlay every deploy script reads: `env.sh` (AWS profile, `MSSP_OUTPUT_TYPE`, `PIPELINE_IMAGE`, `CONNECTOR_IMAGE`, warehouse settings, secret ids), `foundation`/`bootstrap`/`activate` `*.tfvars` and `*.backend.hcl`, the sequencer plan overlay (`sequencer/<plan module>.py`, published to S3 for `python -m mssp_pipeline.sequencer_overlay`), and `rendered/`. |
| `.gitignore` | Canonical ignores `infra/clients/*`; a fork that commits its overlay adds a `!infra/clients/<client>/` line. |

Everything else is canonical, including `infra/clients/client.example/`, the
scripts, the Terraform modules and the CI workflows. The list is fixed in the
script, so a fork cannot widen it without the script itself showing up as a
canonical change.

If the check fails on a change the fork needs, open a PR upstream to hoist it
into canonical (as code, or as a new overlay setting), and validate the next
release that carries it. Evidence from a failing fork does not validate a
release (step 7).

## 4. Build and deploy the fork's commit

Build and deploy from the fork's merge commit exactly as for any other change;
there is no second build from the tag:

```bash
MSSP_OUTPUT_TYPE=<your output type> scripts/build-and-push-image.sh <client> <release-id>
export PIPELINE_IMAGE="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["image"])' release-metadata/<release-id>.json)"
scripts/deploy-client.sh <client> render-taskdefs
scripts/deploy-client.sh <client> register-taskdefs
scripts/deploy-client.sh <client> activate
```

`<release-id>` is the fork's own image tag; ECR tags are immutable, so pick one
that is unique per build (for example `vX.Y.Z-<short merge commit>`). The image
bakes the merge commit as its source commit, so its digest will not equal any
digest in the release notes, and is not meant to. Set `CONNECTOR_IMAGE` to the
connector image the fork built the same way from its connector merge (see the
connector README). `scripts/deploy-client.sh` refuses either variable unless it
is a `repository@sha256:` digest.

## 5. Run the dev sequence

Deploy to the fork's **dev** environment and run the fork's dev sequence end to
end: the staged plan that the sequencer (`mssp-sequence`) drives, which
processes the CMS deliveries with the pipeline image, checks the declared
output contracts, and runs the connector's dbt build and tests.

## 6. Post the evidence

On a green run, post the evidence for the maintainer. A GitHub Release has no
comment thread unless the repository has Discussions enabled and the release
links a discussion. Neither canonical repository has Discussions enabled today,
so open an issue on `tuva-health/cms_mssp_pipeline` titled
`Validate vX.Y.Z (<client>)`, or comment on it if one exists. Cover the
connector release in the same issue. Once releases link discussions, post in
the release's discussion instead.

Use this template. Fill in every line, or write why a line does not apply.

````markdown
**Validation of cms_mssp_pipeline vX.Y.Z + cms_mssp_connector vA.B.C: <client>, dev, <date>**

- Workbook contract: <name> <version>, the same in both releases
- Pipeline conformance (`scripts/check-release-conformance.sh vX.Y.Z <commit>` in the fork):
  ```
  <paste the full output, PASS line included>
  ```
- Connector conformance (`scripts/check_release_conformance.sh vA.B.C <commit>` in the connector fork):
  ```
  <paste the full output, PASS line included>
  ```
- Deployed: `MSSP_OUTPUT_TYPE=<type>`, `PIPELINE_IMAGE` and `CONNECTOR_IMAGE` built from the commits above
- Dev sequence: run <run id / lease token>, every stage green
  - ECS tasks: <stage>: <task ARN>, …
  - Output contract checks: <stage>: passed, …
  - dbt build: PASS=<n> WARN=<n> ERROR=0 SKIP=<n>; dbt test: PASS=<n> WARN=<n> ERROR=0
- Not exercised: <for example: other output types, production schedule, Terraform changes in this release>
````

Task ARNs, run ids, commit hashes, overlay paths and dbt counts are fine to
post. Do not post data, member identifiers, ACO ids, account ids, or warehouse
names: the repository is public.

## 7. Promote the release

The maintainer reads the evidence and, when both conformance outputs say
`PASS` and the dev sequence is green, promotes each release it covers:

```bash
gh release edit vX.Y.Z -R tuva-health/cms_mssp_pipeline --prerelease=false --latest
```

Edit the release notes to link the evidence. A `FAIL`, or a run on a commit
other than the one checked, does not promote a release: what ran was not the
release.

## What "validated" means

A release marked as a full release means that at least one client:

- merged exactly that tag into its fork, and the commit it deployed contains
  the tag and differs from it only in that client's overlay and `.gitignore`;
- built its images from that commit with the canonical scripts;
- ran its full dev sequence on those images against its real CMS deliveries
  and warehouse, and every stage, output contract check and dbt test passed.

It does **not** mean:

- **the client's image is byte-identical to the release build.** Nobody
  compared digests. The client's image bakes the fork's merge commit, and its
  apt layer comes from the live Debian archive on the day it was built. The
  release's recorded digests show the tag builds reproducibly in Tuva's CI
  (TUVA-68); they are not a client-side check.
- **other output types work.** Only the client's variant ran. The other
  variants were built in the release workflow, nothing more.
- **other clients' setups work.** Each client's data, CMS whitelist and
  delivery history, warehouse, credentials, overlay and Terraform remain that
  client's to validate. A release validated by one client is a pre-checked
  starting point for another, not a guarantee.
- **production is safe.** The run was in dev. Promoting to production, and the
  production schedule, are the client's own change.
- **the numbers are right.** Green means the sequence ran, the contracts held
  and the dbt tests passed. It is not a reconciliation of benchmark figures
  against CMS reports unless the evidence says one was done.
- **the overlay is right.** The check confines the fork's changes to its
  overlay; it does not review what the overlay says.
