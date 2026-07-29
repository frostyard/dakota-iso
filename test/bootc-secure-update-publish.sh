#!/usr/bin/bash
set -euo pipefail
# shellcheck source=lib/bootc-secure-runner-lib.sh
# shellcheck disable=SC1091 # Resolved from this script's directory at runtime.
source "$(dirname "$0")/lib/bootc-secure-runner-lib.sh"
[[ "$#" == 8 && "$1" == --profile && "$3" == --tracking-ref && "$5" == --slot && "$7" == --digest-ref ]] || { echo "Usage: $0 --profile PROFILE --tracking-ref REF --slot N+1\|N+2 --digest-ref REF" >&2; exit 1; }
PROFILE="$2" TRACKING="$4" SLOT="$6" DIGEST="$8"; [[ "$SLOT" == N+1 || "$SLOT" == N+2 ]] || die "--slot must be N+1 or N+2"; same_secure_repo "$DIGEST" "$TRACKING" "$PROFILE"; need skopeo
before="$(skopeo inspect --format '{{.Digest}}' "docker://$TRACKING" 2>/dev/null || true)"; want="${DIGEST##*@}"
[[ "$before" != "$want" ]] || die "refusing no-op tracking tag publication"
skopeo copy --all "docker://$DIGEST" "docker://$TRACKING"
[[ "$(skopeo inspect --format '{{.Digest}}' "docker://$TRACKING")" == "$want" ]] || die "tracking tag did not resolve to requested digest"
printf 'BOOTC_SECURE_UPDATE_PUBLISH: %s: published\n' "$SLOT"
