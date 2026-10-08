#!/usr/bin/env bash
# Check that a client fork carries a canonical release unchanged.
#
#   scripts/check-release-conformance.sh vX.Y.Z [REV]
#
# PASS when the tag vX.Y.Z is an ancestor of REV (default HEAD) and every path
# that differs between the tag and REV is client-specific (see allowed below).
# Otherwise FAIL, naming the offending paths. Exit 0 on PASS, 1 on FAIL.
# See docs/client-release-consumption.md.
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "usage: $0 vX.Y.Z [REV]" >&2
  exit 64
fi
tag=$1
rev=${2:-HEAD}

# Client-specific paths: the fork's deployment overlay under
# infra/clients/<client>/ (env.sh, *.tfvars, *.backend.hcl, the sequencer plan
# overlay, rendered/), and .gitignore, which a fork edits to commit that
# overlay. client.example/ is canonical. Anything else is a change to canonical
# code and belongs upstream.
allowed() {
  case $1 in
    infra/clients/client.example/*) return 1 ;;
    infra/clients/*/* | .gitignore) return 0 ;;
    *) return 1 ;;
  esac
}

if ! tag_commit=$(git rev-parse -q --verify "refs/tags/$tag^{commit}"); then
  echo "FAIL: no tag $tag in this clone (git fetch upstream --tags)"
  exit 1
fi
if ! rev_commit=$(git rev-parse -q --verify "$rev^{commit}"); then
  echo "FAIL: $rev is not a commit"
  exit 1
fi
echo "tag $tag: $tag_commit"
echo "rev $rev: $rev_commit"

if ! git merge-base --is-ancestor "$tag_commit" "$rev_commit"; then
  echo "FAIL: $tag is not an ancestor of $rev (merge the tag, do not rebase)"
  exit 1
fi

changed=$(git -c core.quotePath=false diff --name-only --no-renames "$tag_commit" "$rev_commit")
client=()
offending=()
while IFS= read -r path; do
  [[ -n $path ]] || continue
  if allowed "$path"; then client+=("$path"); else offending+=("$path"); fi
done <<<"$changed"

if [[ ${#offending[@]} -gt 0 ]]; then
  echo "FAIL: $rev changes ${#offending[@]} canonical path(s) relative to $tag:"
  printf '  %s\n' "${offending[@]}"
  exit 1
fi
echo "PASS: $rev carries $tag; ${#client[@]} client-specific path(s) differ"
if [[ ${#client[@]} -gt 0 ]]; then printf '  %s\n' "${client[@]}"; fi
