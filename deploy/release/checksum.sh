#!/bin/sh

sha256_digest() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$@"
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$@"
  else
    printf '缺少 sha256sum 或 shasum。\n' >&2
    return 1
  fi
}

sha256_verify() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum -c "$@"
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 -c "$@"
  else
    printf '缺少 sha256sum 或 shasum。\n' >&2
    return 1
  fi
}
