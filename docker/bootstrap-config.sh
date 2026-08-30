#!/usr/bin/env bash
set -euo pipefail

# One-off bootstrap helper:
# 1) Reads CMS key/secret from env
# 2) Runs `acoms-cli configure` non-interactively via expect
# 3) Writes generated config.txt to Secrets Manager

MSSP_WORKDIR="${MSSP_WORKDIR:-/app}"
MSSP_CLI_PATH="${MSSP_CLI_PATH:-/app/bin/acoms-cli}"
ACOMS_HOME="${HOME:-/root}"
CMS_API_KEY="${CMS_API_KEY:-}"
CMS_API_SECRET="${CMS_API_SECRET:-}"
CMS_WHITELIST_IP="${CMS_WHITELIST_IP:-}"
ACOMS_CONFIG_SECRET_ID="${ACOMS_CONFIG_SECRET_ID:-mssp/acoms-config}"
BOOTSTRAP_COMPLETE_PARAM="${BOOTSTRAP_COMPLETE_PARAM:-}"
WHITELIST_CONFIRMED_PARAM="${WHITELIST_CONFIRMED_PARAM:-}"

if [[ -z "$CMS_API_KEY" || -z "$CMS_API_SECRET" ]]; then
  echo "CMS_API_KEY and CMS_API_SECRET are required." >&2
  exit 1
fi

if [[ ! -x "$MSSP_CLI_PATH" ]]; then
  echo "acoms-cli not found or not executable: $MSSP_CLI_PATH" >&2
  exit 1
fi

mkdir -p "$MSSP_WORKDIR"
cd "$MSSP_WORKDIR"
# Locations acoms-cli is known to write config.txt to. Every path searched
# after configure must also be cleared here: a stale file left by an earlier
# failed bootstrap would otherwise be published to Secrets Manager as though
# it had just been generated.
CONFIG_CANDIDATES=(
  "./config.txt"
  "$ACOMS_HOME/.config/acoms-cli/config.txt"
  "$ACOMS_HOME/config.txt"
  "$ACOMS_HOME/.acoms/config.txt"
  "$ACOMS_HOME/.config/acoms/config.txt"
)
rm -f "${CONFIG_CANDIDATES[@]}"

KEY="$CMS_API_KEY" SECRET="$CMS_API_SECRET" IP="$CMS_WHITELIST_IP" CLI="$MSSP_CLI_PATH" expect <<'EOF'
set timeout 120
# Do not echo the spawned session. The CLI prompts for and may echo the
# API key and secret, and this container's stdout goes to CloudWatch.
log_user 0
set key $env(KEY)
set secret $env(SECRET)
set ip $env(IP)
set cli $env(CLI)

spawn $cli configure

expect {
  -re {(?i)please enter your .*?(api[[:space:]_-]*client[[:space:]_-]*id|api[[:space:]_-]*key|client[[:space:]_-]*id|username).*:[[:space:]]*} {
    send -- "$key\r"
    exp_continue
  }
  -re {(?i)(api[[:space:]_-]*client[[:space:]_-]*id|api[[:space:]_-]*key|client[[:space:]_-]*id|username).*:[[:space:]]*} {
    send -- "$key\r"
    exp_continue
  }
  -re {(?i)please enter your .*?(client[[:space:]_-]*secret|api[[:space:]_-]*secret|secret|password).*:[[:space:]]*} {
    send -- "$secret\r"
    exp_continue
  }
  -re {(?i)(client[[:space:]_-]*secret|api[[:space:]_-]*secret|secret|password).*:[[:space:]]*} {
    send -- "$secret\r"
    exp_continue
  }
  -re {(?i)(whitelist|ip[[:space:]_-]*address|public[[:space:]_-]*ip).*:[[:space:]]*} {
    if {$ip eq ""} {
      send -- "\r"
    } else {
      send -- "$ip\r"
    }
    exp_continue
  }
  -re {(?i)(continue|confirm|proceed).*(\[Y/n\]|\(y/n\)|:)[[:space:]]*} {
    send -- "y\r"
    exp_continue
  }
  eof
}
EOF

config_path=""
for candidate in "${CONFIG_CANDIDATES[@]}"; do
  if [[ -s "$candidate" ]]; then
    config_path="$candidate"
    break
  fi
done

if [[ -z "$config_path" ]]; then
  echo "acoms-cli configure did not produce a non-empty config.txt in a known location." >&2
  printf 'Checked: %s\n' "${CONFIG_CANDIDATES[*]}" >&2
  exit 1
fi

export CONFIG_PATH="$config_path"

python - <<'PY'
import os
import boto3

secret_id = os.environ.get("ACOMS_CONFIG_SECRET_ID", "mssp/acoms-config")
config_path = os.environ["CONFIG_PATH"]
with open(config_path, "r", encoding="utf-8") as f:
    value = f.read()

sm = boto3.client("secretsmanager")
sm.put_secret_value(SecretId=secret_id, SecretString=value)
print(f"Stored config from {config_path} in secret: {secret_id}")

ssm = boto3.client("ssm")
for param_name, val in [
    (os.environ.get("BOOTSTRAP_COMPLETE_PARAM"), "true"),
    (os.environ.get("WHITELIST_CONFIRMED_PARAM"), "true"),
]:
    if param_name:
        ssm.put_parameter(Name=param_name, Value=val, Type="String", Overwrite=True)
        print(f"Updated SSM parameter: {param_name}={val}")
PY

echo "Bootstrap completed successfully."
