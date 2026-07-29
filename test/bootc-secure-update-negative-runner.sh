#!/usr/bin/bash
set -euo pipefail
# shellcheck source=lib/bootc-secure-runner-lib.sh
# shellcheck disable=SC1091 # Resolved from this script's directory at runtime.
source "$(dirname "$0")/lib/bootc-secure-runner-lib.sh"
[[ "$#" == 8 && "$1" == --case && "$3" == --profile && "$5" == --state && "$7" == --tracking-ref ]] || { echo "Usage: $0 --case CASE --profile PROFILE --state STATE --tracking-ref REF" >&2; exit 1; }
case "$2" in unsigned|wrong-key|wrong-repository|wrong-mok-uki|digest-mismatch|esp-full|interrupted-finalize|reconcile-failure) ;; *) die "unknown secure update negative case: $2";; esac
validate_state "$6"; blocked "causal signed update mutation fixture for $2 was not supplied"
