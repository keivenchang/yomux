#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Maintained namespace owner. Installed launcher copies are generated from this file.
# BEGIN GENERATED PRODUCT NAMESPACE
PRODUCT_ENV_CANONICAL_PREFIX=YOMUX_
PRODUCT_ENV_LEGACY_PREFIXES=(YOLOMUX_)
# END GENERATED PRODUCT NAMESPACE

product_env_suffix() {
  local _product_suffix_key="$1" _product_suffix_prefix _product_suffix_value
  if [[ "$#" -ge 2 ]]; then
    [[ "$2" =~ ^[a-zA-Z][a-zA-Z_0-9]*$ && "$2" != _product_suffix_* ]] || return 2
  fi
  for _product_suffix_prefix in "$PRODUCT_ENV_CANONICAL_PREFIX" "${PRODUCT_ENV_LEGACY_PREFIXES[@]}"; do
    if [[ "$_product_suffix_key" == "$_product_suffix_prefix"* ]]; then
      _product_suffix_value="${_product_suffix_key#"$_product_suffix_prefix"}"
      if [[ "$#" -ge 2 ]]; then
        printf -v "$2" '%s' "$_product_suffix_value"
      else
        printf '%s' "$_product_suffix_value"
      fi
      return 0
    fi
  done
  return 1
}

product_env_name() {
  local product_setting="$1"
  product_env_suffix "$product_setting" product_setting || product_setting="$1"
  [[ "$product_setting" =~ ^[A-Z][A-Z_0-9]*$ ]] || return 2
  printf '%s' "$PRODUCT_ENV_CANONICAL_PREFIX$product_setting"
}

# Caller-owned output avoids command substitution stripping trailing newlines.
# Internal names are reserved so Bash's dynamic scoping cannot shadow output.
product_env() {
  local _product_env_setting="$1" _product_env_output="$2" _product_env_key _product_env_prefix
  [[ "$_product_env_output" =~ ^[a-zA-Z][a-zA-Z_0-9]*$ && "$_product_env_output" != _product_env_* ]] || return 2
  product_env_suffix "$_product_env_setting" _product_env_setting || _product_env_setting="$1"
  [[ "$_product_env_setting" =~ ^[A-Z][A-Z_0-9]*$ ]] || return 2
  for _product_env_prefix in "$PRODUCT_ENV_CANONICAL_PREFIX" "${PRODUCT_ENV_LEGACY_PREFIXES[@]}"; do
    _product_env_key="$_product_env_prefix$_product_env_setting"
    if [[ "${!_product_env_key+x}" ]]; then
      printf -v "$_product_env_output" '%s' "${!_product_env_key}"
      return 0
    fi
  done
  printf -v "$_product_env_output" '%s' ''
  return 1
}

product_env_present() {
  local product_probe
  product_env "$1" product_probe
}

product_env_print_present() {
  local product_value
  if ! product_env "$1" product_value; then product_value="${2-}"; fi
  printf '%s' "$product_value"
}

product_env_print() {
  local product_value
  if ! product_env "$1" product_value; then
    if [[ "$#" -lt 2 ]]; then return 1; fi
    product_value="$2"
  elif [[ "$#" -ge 2 && -z "$product_value" ]]; then
    product_value="$2"
  fi
  printf '%s' "$product_value"
}

product_env_fingerprint() {
  local fingerprint python_bin="${PYTHON:-python3}"
  if command -v sha256sum >/dev/null 2>&1; then
    fingerprint="$(printf '%s' "$1" | sha256sum)"
  elif command -v shasum >/dev/null 2>&1; then
    fingerprint="$(printf '%s' "$1" | shasum -a 256)"
  else
    fingerprint="$(printf '%s' "$1" | "$python_bin" -c 'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())')" || return
  fi
  printf '%s' "${fingerprint%% *}"
}

product_env_normalize() {
  local product_key product_setting product_prefix product_canonical product_value fingerprint used_legacy=0
  local prior_aliases aliases="," pending processed=","
  product_env ENVIRONMENT_ALIASES prior_aliases || prior_aliases=,
  product_env ENVIRONMENT_LEGACY_INPUT pending || pending=
  while IFS= read -r product_key; do
    product_env_suffix "$product_key" product_setting || continue
    case "$product_setting" in ENVIRONMENT_NORMALIZED|ENVIRONMENT_ALIASES|ENVIRONMENT_LEGACY_INPUT) continue ;; esac
    product_canonical="$PRODUCT_ENV_CANONICAL_PREFIX$product_setting"
    if [[ "$product_key" != "$product_canonical" ]]; then
      if [[ "$prior_aliases" != *",$product_key="* ]]; then
        used_legacy=1
      else
        fingerprint="$(product_env_fingerprint "${!product_key}")"
        if [[ "$prior_aliases" != *",$product_key=$fingerprint,"* ]]; then used_legacy=1; fi
      fi
    fi
  done < <(compgen -e)
  while IFS= read -r product_key; do
    product_env_suffix "$product_key" product_setting || continue
    case "$product_setting" in ENVIRONMENT_NORMALIZED|ENVIRONMENT_ALIASES|ENVIRONMENT_LEGACY_INPUT) continue ;; esac
    # Both spellings select one setting; hash its chosen value only once.
    if [[ "$processed" == *",$product_setting,"* ]]; then continue; fi
    processed="$processed$product_setting,"
    product_env "$product_setting" product_value || continue
    fingerprint="$(product_env_fingerprint "$product_value")"
    for product_prefix in "$PRODUCT_ENV_CANONICAL_PREFIX" "${PRODUCT_ENV_LEGACY_PREFIXES[@]}"; do
      product_key="$product_prefix$product_setting"
      printf -v "$product_key" '%s' "$product_value"
      export "$product_key"
      if [[ "$product_prefix" != "$PRODUCT_ENV_CANONICAL_PREFIX" && "$aliases" != *",$product_key=$fingerprint,"* ]]; then
        aliases="$aliases$product_key=$fingerprint,"
      fi
    done
  done < <(compgen -e)
  if [[ "$used_legacy" -eq 1 || -n "$pending" ]]; then
    printf 'WARNING: %s* environment names are deprecated; use %s*.\n' "${PRODUCT_ENV_LEGACY_PREFIXES[*]}" "$PRODUCT_ENV_CANONICAL_PREFIX" >&2
  fi
  for product_prefix in "${PRODUCT_ENV_LEGACY_PREFIXES[@]}"; do
    unset "${product_prefix}ENVIRONMENT_NORMALIZED" "${product_prefix}ENVIRONMENT_ALIASES" "${product_prefix}ENVIRONMENT_LEGACY_INPUT"
  done
  export "${PRODUCT_ENV_CANONICAL_PREFIX}ENVIRONMENT_NORMALIZED=1" "${PRODUCT_ENV_CANONICAL_PREFIX}ENVIRONMENT_ALIASES=$aliases" "${PRODUCT_ENV_CANONICAL_PREFIX}ENVIRONMENT_LEGACY_INPUT="
}

product_env_assign() {
  local product_assignment product_setting product_prefix product_canonical
  product_env_normalize
  for product_assignment in "$@"; do
    [[ "$product_assignment" == *=* ]] || return 2
    product_setting="${product_assignment%%=*}"
    product_env_suffix "$product_setting" product_setting || product_setting="${product_assignment%%=*}"
    product_canonical="$(product_env_name "$product_setting")" || return 2
    for product_prefix in "${PRODUCT_ENV_LEGACY_PREFIXES[@]}"; do unset "$product_prefix$product_setting"; done
    export "$product_canonical=${product_assignment#*=}"
  done
  product_env_normalize
}

product_env_unset() {
  local product_setting product_original product_prefix
  for product_original in "$@"; do
    product_env_suffix "$product_original" product_setting || product_setting="$product_original"
    product_env_name "$product_setting" >/dev/null || return 2
    for product_prefix in "$PRODUCT_ENV_CANONICAL_PREFIX" "${PRODUCT_ENV_LEGACY_PREFIXES[@]}"; do unset "$product_prefix$product_setting"; done
  done
  # Normalization regenerates provenance after removing the aliases.
  product_env_normalize
}

# Supported launcher spellings delegate to the maintained owner.
yomux_normalize_environment() { product_env_normalize; }
normalize_yomux_environment() { product_env_normalize; }
yomux_assign_environment() { product_env_assign "$@"; }
