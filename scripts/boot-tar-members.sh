#!/usr/bin/bash
# Print the files that each live-image exporter must preserve for ISO assembly.
# Secure Snow needs Debian's shim/GRUB/MokManager; generic variants do not.
set -euo pipefail

secure="${1:?Usage: boot-tar-members.sh <0|1>}"
case "${secure}" in
    0) printf '%s\n' ./usr/lib/modules ./usr/lib/systemd/boot/efi ;;
    1) printf '%s\n' ./usr/lib/modules ./usr/lib/systemd/boot/efi ./usr/lib/shim ./usr/lib/grub ;;
    *) echo "ERROR: secure flag must be 0 or 1" >&2; exit 2 ;;
esac
