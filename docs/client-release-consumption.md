# Consuming a release in a client fork

How a client fork takes a canonical release of `cms_mssp_pipeline` (and the
matching `cms_mssp_connector` release), checks that what it builds is what the
release recorded, runs it, and reports back so the maintainer can promote the
release from pre-release to release.

This is the last step of the release loop that starts in the README
(*Releases*): the tag's workflow builds every backend variant, rebuilds one to
check the build reproduces, and creates a GitHub **pre-release** with the
expected digests. Nothing is pushed to a registry. A release becomes a full
release only after a client has run it end to end against real data, as below.

The connector's README (*Consuming a release in a client fork*) covers the
connector-specific steps. Its image is not byte-reproducible, so it is compared
by metadata instead of digest.

## Summary

| Step | Who | What |
| --- | --- | --- |
| 1. Pick | client | A pipeline release and a connector release that name the same workbook contract version. |
| 2. Merge | client | Merge the tag into the fork through an `upstream-sync/vX.Y.Z` branch. Merge, never rebase; never a `main` tip. |
| 3. Check identity | client | Confirm the fork changes nothing the image is built from. |
| 4. Build and compare | client | Build the tag commit for your `MSSP_OUTPUT_TYPE` and compare digests with the release. |
| 5. Push and pin | client | Push that build to your registry and pin `PIPELINE_IMAGE` (and `CONNECTOR_IMAGE`) to the digests. |
| 6. Run | client | Run the fork's dev sequence on the pinned images. |
| 7. Report | client | Post the evidence where the release links to it. |
| 8. Promote | maintainer | Flip the release from pre-release to release. |

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

- **Merge a tag, not `upstream/main`.** Only a tag has a release, digests and
  release notes to check against. A `main` tip between releases is
  unreleased.
- **Merge, don't rebase.** The tag commit stays an ancestor of the fork's
  `main`, so `git merge-base --is-ancestor vX.Y.Z main` proves which release
  the fork carries, and the next sync merges only what is new.
- **Resolve conflicts in fork-only files.** A conflict in a canonical file means
  the fork has changed canonical code; see step 3.
- If the fork tags its own releases, keep its tags out of the `v*` namespace
  so they cannot collide with canonical tags.

## 3. Check the fork changes nothing in the image

The digest comparison in step 4 only means something if the image the fork
deploys is the image the release built. That holds when the fork's own
content is outside what the Dockerfile copies: templates, docs, and the
gitignored overlay under `infra/clients/<client>/`. Check:

```bash
git diff --stat vX.Y.Z upstream-sync/vX.Y.Z -- \
  Dockerfile .dockerignore pyproject.toml uv.lock README.md \
  mssp_pipeline bin release docker
```

The path list is the Dockerfile's `COPY` sources at the tag; check it against
that Dockerfile if it has changed. `README.md` is on it because the package
build reads it.

- **Empty:** the fork's image is the release's image. Go on to step 4.
- **Not empty:** the fork carries changes the image is built from. You can still
  build and run the fork's own commit, but the digest comparison does not
  apply, and the evidence in step 7 must say the fork's build was used. Open a
  PR upstream to hoist those changes into canonical, so the next release
  carries them and the fork can return to an identical build.

## 4. Build the tag and compare digests

Build the **tag commit**, not the fork's merge commit. `scripts/build-image.sh`
bakes the checkout's `HEAD` commit into the image (`SOURCE_COMMIT`) and uses its
commit time as `SOURCE_DATE_EPOCH`. A build of the merge commit therefore never
has the release's digests, even when its files are identical. A worktree at the
tag, with the overlay copied in, gives a checkout whose `HEAD` is the tag commit
and that `build-image.sh` accepts as clean (the overlay is gitignored):

```bash
git worktree add ../mssp-pipeline-vX.Y.Z vX.Y.Z
cp -R infra/clients/<client> ../mssp-pipeline-vX.Y.Z/infra/clients/
cd ../mssp-pipeline-vX.Y.Z

docker buildx create --use --name mssp-repro   # docker-container builder, as in CI
MSSP_OUTPUT_TYPE=<your output type> \
  scripts/build-image.sh mssp-pipeline vX.Y.Z --record /tmp/build-vX.Y.Z.json

gh release download vX.Y.Z -R tuva-health/cms_mssp_pipeline \
  -p 'image-digests-vX.Y.Z.json' -D /tmp
python3 - /tmp/build-vX.Y.Z.json /tmp/image-digests-vX.Y.Z.json <<'PY'
import json, sys
mine, release = (json.load(open(p)) for p in sys.argv[1:])
variant = next((v for v in release["variants"] if v["pip_extras"] == mine["pip_extras"]), None)
if variant is None:
    raise SystemExit(f"no release variant has extras {mine['pip_extras']!r}")
print("source_commit  ", "match" if mine["source_commit"] == release["source_commit"] else "DIFFER")
for key in ("config_digest", "manifest_digest"):
    print(f"{key:15}", "match" if mine[key] == variant[key] else f"DIFFER release={variant[key]} mine={mine[key]}")
PY
```

Use the release id `vX.Y.Z` exactly: it is a build argument baked into the
image. Set `MSSP_OUTPUT_TYPE` to the value in your overlay's `env.sh`; the
release has one variant per distinct extras set (PARQUET also covers DUCKDB and
MOTHERDUCK). If your `env.sh` overrides `PIP_EXTRAS` with a set the release did
not build, there is nothing to compare against.

Reading the result (the README's *Checking your build against a release* has
the background):

- **config digest matches:** your image is the release's image. The manifest
  digest normally matches too with a docker-container builder. A different
  manifest digest with the same config digest is a compression difference, not
  a content difference.
- **config digest differs:** do not assume the source is at fault. The apt
  packages come from the live Debian archive, so a Debian security or point
  release between the tag and your build changes that layer and every digest
  after it; the further your build is from the release date, the likelier this
  is. Compare the layers against the release's variant (for example with
  `diffoci`). If only the apt layer differs, record that in the evidence and go
  on. If the Python environment, the application, or the CMS CLI layer differs,
  stop and raise it on the release before deploying.

## 5. Push and pin

Push from the same worktree with the usual script. The build reuses the
builder's cache, and the script cross-checks the digest ECR reports against
the one buildx pushed:

```bash
MSSP_OUTPUT_TYPE=<your output type> scripts/build-and-push-image.sh <client> vX.Y.Z
export PIPELINE_IMAGE="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["image"])' release-metadata/vX.Y.Z.json)"
```

Then `PIPELINE_IMAGE` is `<your ECR repository>@sha256:<manifest digest>`.
When step 4 matched, that is the manifest digest in the release's table:
pinning the release means pinning that digest. Set it in the overlay's
`env.sh` (or export it for the deploy) along with the connector's
`CONNECTOR_IMAGE`, built the same way from the connector release (see the
connector README). `scripts/deploy-client.sh` refuses either variable unless it
is a `repository@sha256:` digest.

## 6. Run the dev sequence

Deploy to the fork's **dev** environment from the fork's `main` (after the
`upstream-sync` PR has merged), with both images pinned:

```bash
scripts/deploy-client.sh <client> render-taskdefs
scripts/deploy-client.sh <client> register-taskdefs
scripts/deploy-client.sh <client> activate
```

Then run the fork's dev sequence end to end: the staged plan that the
sequencer (`mssp-sequence`) drives, which processes the CMS deliveries with
the pipeline image, checks the declared output contracts, and runs the
connector's dbt build and tests. Don't use `scripts/deploy-and-smoke-client.sh`
here: it builds its own image from the current checkout, which is the merge
commit, not the tag.

## 7. Post the evidence

On a green run, post the evidence for the maintainer. A GitHub Release has no
comment thread unless the repository has Discussions enabled and the release
links a discussion. Neither canonical repository has Discussions enabled today,
so open an issue on `tuva-health/cms_mssp_pipeline` titled
`Validate vX.Y.Z (<client>)`, or comment on it if one exists. Cover the
connector release in the same issue. Once releases link discussions, post in
the release's discussion instead.

Use this template. Fill in every line, or write why a line does not apply.

```markdown
**Validation of cms_mssp_pipeline vX.Y.Z + cms_mssp_connector vA.B.C: <client>, dev, <date>**

- Fork: <fork repo> `main` at <merge commit> (merged `upstream-sync/vX.Y.Z`; `vX.Y.Z` is an ancestor)
- Image identity (step 3): fork changes no image input / fork carries image changes in <paths>
- Pipeline image: `MSSP_OUTPUT_TYPE=<type>`, built from <tag commit>
  - config digest <sha256:…>: matches the release / differs: <which layers, why>
  - `PIPELINE_IMAGE` = <repository@sha256:…>
- Connector image: `/app/release-metadata.json` source_commit, release_id, dependency_sha256, command_contract match `release-metadata-vA.B.C.json`: yes / no (<field>)
  - `CONNECTOR_IMAGE` = <repository@sha256:…>
- Workbook contract: <name> <version>, the same in both releases
- Dev sequence: run <run id / lease token>, every stage green
  - ECS tasks: <stage>: <task ARN>, …
  - Output contract checks: <stage>: passed, …
  - dbt build: PASS=<n> WARN=<n> ERROR=0 SKIP=<n>; dbt test: PASS=<n> WARN=<n> ERROR=0
- Not exercised: <for example: other output types, production schedule, Terraform changes in this release>
```

Task ARNs, run ids and dbt counts are fine to post. Do not post data, member
identifiers, ACO ids, account ids, or warehouse names: the repository is
public.

## 8. Promote the release

The maintainer reads the evidence and promotes each release it covers:

```bash
gh release edit vX.Y.Z -R tuva-health/cms_mssp_pipeline --prerelease=false --latest
```

Edit the release notes to link the evidence. Evidence from a fork's own build
(step 3 not empty), or with a config digest that differs outside the apt
layer, does not promote a release on its own: the release itself did not run.

## What "validated" means

A release marked as a full release means that at least one client:

- merged exactly that tag into its fork;
- built an image from the tag commit that matched the release's recorded
  digests for its output type (pipeline), or whose baked metadata matched the
  release's (connector), apart from a documented apt-layer drift;
- ran its full dev sequence on those pinned images against its real CMS
  deliveries and warehouse, and every stage, output contract check and dbt
  test passed.

It does **not** mean:

- **other output types work.** Only the client's variant ran. The other
  variants were built and their digests recorded, nothing more.
- **other clients' setups work.** Each client's data, CMS whitelist and
  delivery history, warehouse, credentials, overlay and Terraform remain that
  client's to validate. A release validated by one client is a pre-checked
  starting point for another, not a guarantee.
- **production is safe.** The run was in dev. Promoting the same digests to
  production, and the production schedule, are the client's own change.
- **the numbers are right.** Green means the sequence ran, the contracts held
  and the dbt tests passed. It is not a reconciliation of benchmark figures
  against CMS reports unless the evidence says one was done.
- **a later build will match.** The digests identify the release build. A
  build weeks later can differ in the apt layer (step 4).
- **fork-only content is checked.** Templates, overlays and anything else the
  fork adds are outside the release.
