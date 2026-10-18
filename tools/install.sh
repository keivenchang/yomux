#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
set -euo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
prefix="$HOME/.local"
source_root="$repo_root"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h) printf 'Usage: tools/install.sh [--prefix ABSOLUTE_DIRECTORY] [--source-root ABSOLUTE_CHECKOUT]\n'; exit 0 ;;
    --prefix) [[ $# -ge 2 ]] || exit 2; prefix="$2"; shift 2 ;;
    --source-root) [[ $# -ge 2 ]] || exit 2; source_root="$2"; shift 2 ;;
    *) printf 'Unknown install option: %s\n' "$1" >&2; exit 2 ;;
  esac
done
[[ "$prefix" == /* && "$prefix" != / ]] || { printf 'Install prefix must be an absolute non-root directory\n' >&2; exit 2; }
[[ "$source_root" == /* ]] || { printf 'Update source must be an absolute checkout path\n' >&2; exit 2; }
source "$repo_root/tools/startup_common.sh"
yomux_validate_backend
binary="$(yomux_go_binary "$repo_root")"
"$binary" --operator version --root "$repo_root" >/dev/null
source_root="$("$binary" --operator update-source --root "$source_root")"
for asset in yomux.js yomux.css; do
  [[ -s "$repo_root/static/$asset" ]] || { printf 'Missing static/%s; run make build\n' "$asset" >&2; exit 2; }
done
mkdir -p "$prefix/lib" "$prefix/bin"
prefix="$(cd -- "$prefix" && pwd -P)"
app="$prefix/lib/yomux"
# Installation owns only product files; user configuration and agent directories stay external.
mkdir -p "$app/tools" "$app/assets/runtime" "$app/yomux_lib/yoagent"
# The old process can serve these files until restart. Every file publication,
# including wrappers, uses the same native owner as source-checkout updates.
atomic_install_file() {
  "$binary" --operator copy-file --root "$repo_root" -- "$1" "$2"
}
atomic_install_tree() {
  local source="$1" destination="$2" entry name
  mkdir -p "$destination"
  for entry in "$source"/* "$source"/.[!.]* "$source"/..?*; do
    [[ -e "$entry" || -L "$entry" ]] || continue
    [[ ! -L "$entry" ]] || { printf 'Installation payload contains a symlink: %s\n' "$entry" >&2; return 2; }
    name="${entry##*/}"
    if [[ -d "$entry" ]]; then
      atomic_install_tree "$entry" "$destination/$name" || return
    else
      atomic_install_file "$entry" "$destination/$name" || return
    fi
  done
}
atomic_install_file "$binary" "$app/yomux-go"
atomic_install_file "$repo_root/boot.sh" "$app/boot.sh"
atomic_install_file "$repo_root/version.yaml" "$app/version.yaml"
for script in startup_common.sh product_environment.sh setup-tls.sh; do
  atomic_install_file "$repo_root/tools/$script" "$app/tools/$script"
done
for asset in chat-question-rules.json internal_model_pricing.json locale-registry.json model_pricing.json model_pricing.schema.json settings-schema.json; do
  atomic_install_file "$repo_root/assets/runtime/$asset" "$app/assets/runtime/$asset"
done
atomic_install_tree "$repo_root/static" "$app/static"
for directory in builtin_skills builtin_context; do
  atomic_install_tree "$repo_root/yomux_lib/yoagent/$directory" "$app/yomux_lib/yoagent/$directory"
done
"$app/yomux-go" --operator record-install-source --root "$app" --source-root "$source_root" --prefix "$prefix"
# A wrapper preserves a stable resource root while the binary remains directly callable.
wrapper_stage="$prefix/bin/.yomux-wrapper.$$"
trap 'rm -f -- "$wrapper_stage"' EXIT
for command in yomux yolomux; do
  {
    printf '#!/usr/bin/env bash\nset -euo pipefail\n'
    printf 'checkout=%q\n' "$app"
    printf 'source "$checkout/tools/startup_common.sh"\n'
    printf 'yomux_validate_backend\nbinary="$(yomux_go_binary "$checkout")"\n'
    printf 'exec "$binary" --launch --root "$checkout" "$@"\n'
  } > "$wrapper_stage"
  chmod 755 "$wrapper_stage"
  atomic_install_file "$wrapper_stage" "$prefix/bin/$command"
done
printf 'Installed native YOmux in %s\n' "$app"
