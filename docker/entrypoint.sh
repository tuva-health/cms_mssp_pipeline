#!/usr/bin/env bash
set -euo pipefail

MSSP_WORKDIR="${MSSP_WORKDIR:-/app}"
MSSP_WRITE_CONFIG_TXT="${MSSP_WRITE_CONFIG_TXT:-auto}" # auto|always|never

mkdir -p "$MSSP_WORKDIR"
cd "$MSSP_WORKDIR"

install_config_txt() {
  local source_path="$1"
  cp "$source_path" ./config.txt
  chmod 600 ./config.txt

  local home_config_dir="${HOME:-/root}/.config/acoms-cli"
  mkdir -p "$home_config_dir"
  cp "$source_path" "$home_config_dir/config.txt"
  chmod 600 "$home_config_dir/config.txt"
}

write_config_txt_if_provided() {
  if [[ -n "${ACOMS_CONFIG_TXT_B64:-}" ]]; then
    local tmp
    tmp="$(mktemp)"
    printf '%s' "$ACOMS_CONFIG_TXT_B64" | base64 -d > "$tmp"
    install_config_txt "$tmp"
    rm -f "$tmp"
    return 0
  fi

  if [[ -n "${ACOMS_CONFIG_TXT:-}" ]]; then
    local tmp
    tmp="$(mktemp)"
    printf '%s' "$ACOMS_CONFIG_TXT" > "$tmp"
    install_config_txt "$tmp"
    rm -f "$tmp"
    return 0
  fi

  if [[ -n "${ACOMS_CONFIG_PATH:-}" ]]; then
    install_config_txt "$ACOMS_CONFIG_PATH"
    return 0
  fi

  return 1
}

needs_config_txt() {
  if [[ "$#" -eq 0 ]]; then
    return 0
  fi

  case "$1" in
    mssp-pipeline)
      return 0
      ;;
    mssp-download)
      shift
      for arg in "$@"; do
        if [[ "$arg" == "--configure" ]]; then
          return 1
        fi
      done
      return 0
      ;;
    python)
      if [[ "$#" -ge 4 && "$2" == "-m" && "$3" == "mssp_pipeline" ]]; then
        case "$4" in
          pipeline)
            return 0
            ;;
          download)
            shift 4
            for arg in "$@"; do
              if [[ "$arg" == "--configure" ]]; then
                return 1
              fi
            done
            return 0
            ;;
        esac
      fi
      return 1
      ;;
    *)
      return 1
      ;;
  esac
}

config_provided=false
if write_config_txt_if_provided; then
  config_provided=true
fi

case "$MSSP_WRITE_CONFIG_TXT" in
  always)
    if [[ "$config_provided" != "true" ]]; then
      echo "[entrypoint] MSSP_WRITE_CONFIG_TXT=always but no ACOMS_CONFIG_TXT(_B64) or ACOMS_CONFIG_PATH was provided." >&2
      exit 1
    fi
    ;;
  never)
    ;;
  auto)
    if needs_config_txt "$@"; then
      if [[ ! -f ./config.txt ]]; then
        echo "[entrypoint] Command requires ./config.txt, but none is present." >&2
        echo "[entrypoint] Provide ACOMS_CONFIG_TXT_B64 (recommended in ECS Secrets) or ACOMS_CONFIG_TXT." >&2
        exit 1
      fi
    fi
    ;;
  *)
    echo "[entrypoint] Invalid MSSP_WRITE_CONFIG_TXT='$MSSP_WRITE_CONFIG_TXT' (expected auto|always|never)." >&2
    exit 1
    ;;
esac

exec "$@"
