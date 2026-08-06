#!/usr/bin/bash
set -euo pipefail
# shellcheck source=lib/bootc-secure-runner-lib.sh
# shellcheck disable=SC1091 # Resolved from this script's directory at runtime.
source "$(dirname "$0")/lib/bootc-secure-runner-lib.sh"
[[ "$#" == 5 && "$1" == --non-interactive && "$2" == --iso && "$4" == --recipe ]] || { echo "Usage: $0 --non-interactive --iso ISO --recipe RECIPE" >&2; exit 1; }
ISO="$3" RECIPE="$5"; secure_env; validate_recipe "$RECIPE"
need ssh; need ssh-keygen; need scp; need socat
DISK="$(json_string "$RECIPE" target_disk)"; RECOVERY="$(json_string "$RECIPE" recovery_key)"; PUBKEY="$(json_string "$RECIPE" root_ssh_authorized_key)"
[[ -b "$DISK" || -f "$DISK" ]] || blocked "target disk is unavailable"; [[ -r "$RECOVERY" && -r "$PUBKEY" ]] || blocked "recovery credential or test SSH public key is unavailable"
WORK="$(mktemp -d /var/tmp/snosi-task9-install.XXXXXX)"; umask 077; trap 'stop_vm "$WORK"; rm -rf "$WORK"' EXIT
PORT="$(ssh_port)"; start_live "$ISO" "$DISK" "$WORK" "$PORT"; wait_live_ssh "$PORT"
live_ssh "$PORT" "sudo install -d -m 0700 /run/snosi-task9"
scp_live "$PORT" "$RECOVERY" /run/snosi-task9/recovery.key
scp_live "$PORT" "$PUBKEY" /run/snosi-task9/root.pub
live_ssh "$PORT" "sudo chmod 0600 /run/snosi-task9/recovery.key"
# fisherman requires secureInstall.mokPasswordFile, and for an external
# autoinstall recipe it is caller-owned -- the GUI parent generates it only for
# its own interactive flow (bootc-installer docs/secure-install.md). Generate the
# documented shape here: a one-time, 16-character ASCII-alphanumeric MokManager
# password, mode 0600, created on the guest so the secret never crosses scp.
# The harness proves enrollment host-side with virt-fw-vars rather than by
# typing this at MokManager, but fisherman still validates its presence.
live_ssh "$PORT" "sudo sh -c 'LC_ALL=C tr -dc A-Za-z0-9 </dev/urandom | head -c 16 > /run/snosi-task9/mok.pass; chmod 0600 /run/snosi-task9/mok.pass'"
cat >"$WORK/fisherman-recipe.json" <<EOF
{"disk":"/dev/vda","filesystem":"btrfs","image":"$(json_string "$RECIPE" oci_ref)","targetImgref":"$(json_string "$RECIPE" tracking_ref)","cosignPubKey":"/usr/lib/snosi/cosign.pub","composeFsBackend":true,"bootloader":"systemd","hostname":"snosi-task9","encryption":{"type":"luks-passphrase"},"luksMapperName":"root","secureInstall":{"recoveryKeyFile":"/run/snosi-task9/recovery.key","mokPasswordFile":"/run/snosi-task9/mok.pass"}}
EOF
scp_live "$PORT" "$WORK/fisherman-recipe.json" /run/snosi-task9/recipe.json
scp_live "$PORT" "$(dirname "$0")/lib/bootc-secure-runner-lib.sh" /run/snosi-task9/bootc-secure-runner-lib.sh
live_ssh "$PORT" 'sudo /usr/lib/snosi/fisherman /run/snosi-task9/recipe.json'
# Test-only public-key access is retained in persistent /etc; recovery bytes remain in /run.
# shellcheck disable=SC2016 # This is intentionally expanded by the remote shell.
live_ssh "$PORT" 'sudo bash -ceu '"'"'source /run/snosi-task9/bootc-secure-runner-lib.sh; mapfile -t luks < <(lsblk -nrpo NAME,FSTYPE /dev/vda | awk "\$2==\"crypto_LUKS\"{print \$1}"); ((${#luks[@]} == 1)) || { echo "expected exactly one LUKS device" >&2; exit 1; }; cryptsetup open --key-file=/run/snosi-task9/recovery.key "${luks[0]}" root; backing=$(cryptsetup status root | awk "\$1==\"device:\"{print \$2;exit}"); [[ "$backing" == "${luks[0]}" ]] || { echo "root mapper backing device mismatch" >&2; exit 1; }; m=$(mktemp -d); mount /dev/mapper/root "$m"; e=$(composefs_persistent_etc "$m"); install -d -m 0700 "$e/snosi-task9" "$e/ssh/sshd_config.d"; install -m 0600 /run/snosi-task9/root.pub "$e/snosi-task9/root_authorized_keys"; printf "%s\n" "AuthorizedKeysFile /etc/snosi-task9/root_authorized_keys" > "$e/ssh/sshd_config.d/99-snosi-task9-test.conf"; test "$(stat -c '%a' "$e/snosi-task9/root_authorized_keys")" = 600; test "$(stat -c '%a' "$e/ssh/sshd_config.d/99-snosi-task9-test.conf")" = 644; umount -R "$m"; rmdir "$m"; cryptsetup close root; rm -rf /run/snosi-task9'"'"''
printf 'BOOTC_SECURE_INSTALLER: installed\n'
