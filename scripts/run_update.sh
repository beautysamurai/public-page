#!/usr/bin/env bash

set -Eeuo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd -- "${script_dir}/.." && pwd)"
local_dir="${repository_root}/.local"
log_dir="${local_dir}/logs"
python_bin="${PYTHON_BIN:-python3}"

mkdir -p -- "${log_dir}"
log_file="${log_dir}/update-$(date -u +'%Y%m%d').log"

exec 9>"${local_dir}/run_update.lock"
if ! flock -n 9; then
  printf '[%s] Another arXiv Daily update is already running; skipping.\n' \
    "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" >>"${log_file}"
  exit 0
fi

exec >>"${log_file}" 2>&1

printf '[%s] Starting arXiv Daily update.\n' \
  "$(date -u +'%Y-%m-%dT%H:%M:%SZ')"

cd -- "${repository_root}"
"${python_bin}" scripts/arxiv_digest.py \
  --output site/data/latest.json \
  --archive-dir site/data/archive

printf '[%s] Update completed. Review site/data before committing.\n' \
  "$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
