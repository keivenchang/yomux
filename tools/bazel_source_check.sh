#!/usr/bin/env bash
set -euo pipefail
buildifier="$1"
shift
build_sources=()
module_file=""
go_mod_file=""
for source in "$@"; do
  case "$(basename "$source")" in
    MODULE.bazel)
      module_file="$source"
      build_sources+=("$source")
      ;;
    go.mod)
      go_mod_file="$source"
      ;;
    *)
      build_sources+=("$source")
      ;;
  esac
done

"$buildifier" -mode=check "${build_sources[@]}"
if [ -z "$module_file" ] || [ -z "$go_mod_file" ]; then
  echo "Bazel source check requires MODULE.bazel and go.mod inputs" >&2
  exit 1
fi

# rules_go is a Bazel module and also supplies the Go runfiles package imported
# by the test helpers. Keep the unavoidable Go module mirror tied to the module
# declaration that owns the Bazel rule version.
mapfile -t bazel_rules_go_versions < <(sed -nE 's/^bazel_dep\(name = "rules_go", version = "([^"]+)"\)$/\1/p' "$module_file")
mapfile -t go_rules_go_versions < <(sed -nE 's/^[[:space:]]*github\.com\/bazelbuild\/rules_go v([^[:space:]]+).*$/\1/p' "$go_mod_file")
if [ "${#bazel_rules_go_versions[@]}" -ne 1 ] || [ "${#go_rules_go_versions[@]}" -ne 1 ]; then
  echo "expected one rules_go version in MODULE.bazel and go.mod" >&2
  exit 1
fi
if [ "${bazel_rules_go_versions[0]}" != "${go_rules_go_versions[0]}" ]; then
  echo "rules_go version drift: MODULE.bazel=${bazel_rules_go_versions[0]} go.mod=${go_rules_go_versions[0]}" >&2
  exit 1
fi
