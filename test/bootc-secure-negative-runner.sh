#!/usr/bin/bash
set -euo pipefail
# shellcheck source=lib/bootc-secure-runner-lib.sh
# shellcheck disable=SC1091 # Resolved from this script's directory at runtime.
source "$(dirname "$0")/lib/bootc-secure-runner-lib.sh"
[[ "$#" == 8 && "$1" == --case && "$3" == --profile && "$5" == --oci-ref && "$7" == --recipe ]] || { echo "Usage: $0 --case CASE --profile PROFILE --oci-ref REF --recipe RECIPE" >&2; exit 1; }
case "$2" in unsigned|wrong-key|wrong-repository|false-capability|wrong-mok-uki|composefs-mismatch|esp-full|interrupted-finalize|reconcile-failure) ;; *) die "unknown secure negative case: $2";; esac
blocked "signed causal fixture for $2 was not supplied"
