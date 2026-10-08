#!/usr/bin/env python3
"""Release-notes helper for .github/workflows/release.yml.

``changelog VERSION``
    Print the body of the ``## [VERSION]`` section of CHANGELOG.md. Exits
    non-zero when the section is missing or empty, so a tag cannot be released
    without a changelog entry.

``notes --tag TAG --records DIR [--digests-out FILE] [--require-match]``
    Print the GitHub Release body for TAG from the build records that
    scripts/build-image.sh wrote into DIR (``build-record-<tag>-<variant>.json``,
    one per MSSP_OUTPUT_TYPE variant plus ``<variant>-rebuild`` for the
    reproducibility rebuild), the workbook contract, and the CHANGELOG section.
    Optionally write the same digests as JSON (a release asset). With
    --require-match, exit 3 (after printing the notes) when the rebuild's
    digests differ from the first build's or no rebuild ran: a release must not
    publish a digest it could not reproduce.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = Path("contracts/workbook/v1.json")
# Output types sharing one extras set, as scripts/build-image.sh derives them.
ALIASES = {"PARQUET": "PARQUET / DUCKDB / MOTHERDUCK"}


def changelog_section(version: str) -> str:
    heading = f"## [{version}]"
    lines = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").splitlines()
    body: list[str] | None = None
    for line in lines:
        if body is None:
            if line.startswith(heading):
                body = []
            continue
        if line.startswith("## ["):
            break
        body.append(line)
    text = "\n".join(body or []).strip()
    if not text:
        raise SystemExit(f"CHANGELOG.md has no non-empty section {heading}")
    return text


def workbook_contract() -> dict[str, str]:
    path = ROOT / CONTRACT
    contract = json.loads(path.read_text(encoding="utf-8"))
    return {
        "contract": contract["contract"],
        "version": contract["version"],
        "path": str(CONTRACT),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def load_records(directory: Path, tag: str) -> tuple[list[dict], dict | None]:
    prefix = f"build-record-{tag}-"
    variants: list[dict] = []
    rebuild = None
    for path in sorted(directory.glob(f"{prefix}*.json")):
        variant = path.name[len(prefix) : -len(".json")]
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("release_id") != tag:
            raise SystemExit(f"{path.name}: release_id {record.get('release_id')!r} != {tag}")
        if variant.endswith("-rebuild"):
            record["output_type"] = variant[: -len("-rebuild")].upper()
            rebuild = record
        else:
            record["output_type"] = variant.upper()
            variants.append(record)
    if not variants:
        raise SystemExit(f"no build records for {tag} in {directory}")
    commits = {r["source_commit"] for r in variants}
    if len(commits) != 1:
        raise SystemExit(f"build records disagree on the source commit: {sorted(commits)}")
    return variants, rebuild


def rebuild_check(variants: list[dict], rebuild: dict | None) -> dict | None:
    if rebuild is None:
        return None
    first = next(r for r in variants if r["output_type"] == rebuild["output_type"])
    return {
        "output_type": rebuild["output_type"],
        "config_digest_match": first["config_digest"] == rebuild["config_digest"],
        "manifest_digest_match": first["manifest_digest"] == rebuild["manifest_digest"],
        "first": {k: first[k] for k in ("config_digest", "manifest_digest")},
        "rebuild": {k: rebuild[k] for k in ("config_digest", "manifest_digest")},
    }


def render(tag: str, variants: list[dict], check: dict | None, contract: dict) -> str:
    commit = variants[0]["source_commit"]
    out = [
        f"Pre-release of `{tag}`. It stays a pre-release until a client deployment "
        "reports a green dev sequence against it.",
        "",
        f"- **Source commit:** `{commit}`",
        f"- **Workbook contract:** `{contract['contract']}` {contract['version']} "
        f"(`{contract['path']}`, sha256 `{contract['sha256']}`)",
        f"- **Dependency checksum (uv.lock sha256):** `{variants[0]['dependency_checksum']}`",
        f"- **SOURCE_DATE_EPOCH:** `{variants[0]['source_date_epoch']}` (commit time)",
        "",
        "## Expected image digests",
        "",
        "Built in CI by `scripts/build-image.sh` for `linux/amd64`. **No image is "
        "published**: build your own from this tag and compare (see README, "
        "*Releases*). The config digest is the image ID and does not depend on "
        "layer compression; the manifest digest is what a registry would report "
        "for an image pushed by `scripts/build-image.sh --push`.",
        "",
        "| MSSP_OUTPUT_TYPE | PIP_EXTRAS | config digest (image ID) | manifest digest |",
        "| --- | --- | --- | --- |",
    ]
    for record in variants:
        out.append(
            f"| {ALIASES.get(record['output_type'], record['output_type'])} "
            f"| `{record['pip_extras']}` | `{record['config_digest']}` "
            f"| `{record['manifest_digest']}` |"
        )
    out += ["", "## Reproducibility check", ""]
    if check is None:
        out.append("No rebuild was run.")
    else:
        verdict = {True: "match", False: "**differ**"}
        out.append(
            f"A second, independent build of `{check['output_type']}` on a fresh "
            f"runner with no shared cache: config digest {verdict[check['config_digest_match']]}, "
            f"manifest digest {verdict[check['manifest_digest_match']]}."
        )
        if check["config_digest_match"] and check["manifest_digest_match"]:
            out.append(
                "A clean checkout of this tag built with `scripts/build-image.sh` and "
                "the same `MSSP_OUTPUT_TYPE` should reproduce the digests above."
            )
        else:
            out.append(
                f"Rebuild produced config `{check['rebuild']['config_digest']}` / "
                f"manifest `{check['rebuild']['manifest_digest']}`. The build did not "
                "reproduce, so the digests above identify this CI build only."
            )
    out += [
        "",
        "## Assets",
        "",
        f"- `release-metadata-{tag}-<output type>.json`: the release-provenance "
        "contract checked by `scripts/verify_release_metadata.py`. Its `image` names "
        "the unpublished CI build (`mssp-pipeline@<manifest digest>`); your own "
        "`scripts/build-and-push-image.sh` run writes the metadata you deploy from.",
        f"- `image-digests-{tag}.json`: the table above, machine-readable.",
        "",
        "## Changelog",
        "",
        changelog_section(tag.removeprefix("v")),
        "",
    ]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    modes = parser.add_subparsers(dest="mode", required=True)
    changelog = modes.add_parser("changelog")
    changelog.add_argument("version")
    notes = modes.add_parser("notes")
    notes.add_argument("--tag", required=True)
    notes.add_argument("--records", type=Path, required=True)
    notes.add_argument("--digests-out", type=Path)
    notes.add_argument("--require-match", action="store_true")
    args = parser.parse_args(argv)

    if args.mode == "changelog":
        print(changelog_section(args.version))
        return 0

    variants, rebuild = load_records(args.records, args.tag)
    check = rebuild_check(variants, rebuild)
    contract = workbook_contract()
    print(render(args.tag, variants, check, contract))
    if args.digests_out:
        args.digests_out.write_text(
            json.dumps(
                {
                    "tag": args.tag,
                    "source_commit": variants[0]["source_commit"],
                    "source_date_epoch": variants[0]["source_date_epoch"],
                    "dependency_checksum": variants[0]["dependency_checksum"],
                    "platform": variants[0]["platform"],
                    "workbook_contract": contract,
                    "variants": [
                        {k: r[k] for k in ("output_type", "pip_extras", "config_digest", "manifest_digest")}
                        for r in variants
                    ],
                    "rebuild_check": check,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    if args.require_match and not (
        check and check["config_digest_match"] and check["manifest_digest_match"]
    ):
        print("ERROR: the rebuild did not reproduce the image digests", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
