#!/usr/bin/bash
set -euo pipefail
# shellcheck source=lib/bootc-secure-runner-lib.sh
# shellcheck disable=SC1091 # Resolved from this script's directory at runtime.
source "$(dirname "$0")/lib/bootc-secure-runner-lib.sh"
[[ "$#" == 14 && "$1" == --case && "$3" == --profile && "$5" == --oci-ref && "$7" == --state && "$9" == --iso && "${11}" == --recipe && "${13}" == --recovery-key ]] || { echo "Usage: $0 --case CASE --profile PROFILE --oci-ref REF --state STATE --iso ISO --recipe RECIPE --recovery-key KEY" >&2; exit 1; }
CASE="$2"; case "$CASE" in tpm-replacement|recovery-reenrollment) ;; *) die "unknown secure recovery case: $CASE";; esac
validate_state "$8"; validate_recipe "${12}"; secure_env; need ssh; need sshpass; need scp; need socat; need swtpm; need jq
DISK="$(json_string "$8" target_disk)"; SSH_KEY="$(json_string "$8" ssh_private_key)"; RECOVERY="${14}"; [[ -r "$RECOVERY" && -r "$SSH_KEY" && ( -b "$DISK" || -f "$DISK" ) ]] || blocked "recovery credential, state SSH key, or retained target disk is unavailable"
WORK="$(mktemp -d /var/tmp/snosi-task9-recovery.XXXXXX)"; umask 077
stop_tpm() { [[ -f "$WORK/swtpm.pid" ]] && kill "$(<"$WORK/swtpm.pid")" 2>/dev/null || true; }
trap 'stop_vm "$WORK"; stop_tpm; rm -f "$SNOSI_SECURE_TPM_SOCKET"; rm -rf "$WORK"' EXIT
start_tpm() { swtpm socket --tpm2 --tpmstate "dir=$SNOSI_SECURE_TPM_STATE" --ctrl "type=unixio,path=$SNOSI_SECURE_TPM_SOCKET" --pid "file=$WORK/swtpm.pid" --daemon; }
old_unavailable_proven=0
assert_stale_token_rejected() {
    local port seen=0; port="$(ssh_port)"; start_installed "$DISK" "$WORK" "$port"
    for _ in $(seq 1 60); do
        root_ssh "$SSH_KEY" "$port" && die "replacement TPM unexpectedly unlocked installed root"
        if grep -Eqi 'recovery passphrase|please enter passphrase|enter passphrase.*root' "$WORK/serial.log" 2>/dev/null; then seen=1; break; fi
        sleep 2
    done
    ((seen)) || blocked "replacement boot produced no recovery prompt or boot signal"
    old_unavailable_proven=1
    stop_vm "$WORK"; rm -f "$WORK/monitor.sock" "$WORK/qemu.pid"
}
case "$CASE" in
    tpm-replacement)
        rm -rf "$SNOSI_SECURE_TPM_STATE"; mkdir -p "$SNOSI_SECURE_TPM_STATE"; start_tpm
        assert_stale_token_rejected
        ;;
    recovery-reenrollment)
        # Preserve the old TPM state; only the LUKS TPM token is rotated.
        start_tpm
        ;;
esac
PORT="$(ssh_port)"; start_live "${10}" "$DISK" "$WORK" "$PORT"; wait_live_ssh "$PORT"
live_ssh "$PORT" "sudo install -d -m 0700 /run/snosi-task9"
scp_live "$PORT" "$RECOVERY" /run/snosi-task9/recovery.key
# shellcheck disable=SC2016 # This is intentionally expanded by the remote shell.
live_ssh "$PORT" 'sudo bash -ceu '"'"'mapfile -t luks < <(lsblk -nrpo NAME,FSTYPE /dev/vda | awk "$2==\"crypto_LUKS\"{print \$1}"); ((${#luks[@]} == 1)) || { echo "expected exactly one LUKS device" >&2; exit 1; }; cryptsetup open --key-file=/run/snosi-task9/recovery.key "${luks[0]}" root; backing=$(cryptsetup status root | awk "\$1==\"device:\"{print \$2;exit}"); [[ "$backing" == "${luks[0]}" ]] || exit 1; old_token=$(cryptsetup luksDump --dump-json-metadata "$backing" | jq -c ".tokens | to_entries[] | select(.value.type == \"systemd-tpm2\")"); m=$(mktemp -d); mount /dev/mapper/root "$m"; esp=$(lsblk -nrpo NAME,PARTTYPE /dev/vda | awk "tolower(\$2)==\"c12a7328-f81f-11d2-ba4b-00a0c93ec93b\"{print \$1}"); test $(wc -w <<<"$esp") -eq 1; mount "$esp" "$m/boot/efi"; entry=$(find "$m/boot/efi/loader/entries" -name "*.conf" | head -1); uki=$(awk "\$1==\"efi\"{print \$2;exit}" "$entry"); objcopy --dump-section .pcrpkey=/run/pcr-public "$m/boot/efi/${uki#/}"; systemd-cryptenroll --wipe-slot=tpm2 "$backing"; test -z "$(cryptsetup luksDump --dump-json-metadata "$backing" | jq -r ".tokens | to_entries[]? | select(.value.type == \"systemd-tpm2\") | .key")"; systemd-cryptenroll --unlock-key-file=/run/snosi-task9/recovery.key --tpm2-device=auto --tpm2-pcrs= --tpm2-public-key=/run/pcr-public --tpm2-public-key-pcrs=11 --tpm2-pcrlock= "$backing"; new_token=$(cryptsetup luksDump --dump-json-metadata "$backing" | jq -c ".tokens | to_entries[] | select(.value.type == \"systemd-tpm2\")"); [[ "$old_token" != "$new_token" ]]; cryptsetup open --test-passphrase --key-file=/run/snosi-task9/recovery.key "$backing"; rm -rf /run/snosi-task9 /run/pcr-public; umount "$m/boot/efi"; umount "$m"; rmdir "$m"; cryptsetup close root'"'"''
if [[ "$CASE" == recovery-reenrollment ]]; then
    old_unavailable_proven=1
fi
[[ "$old_unavailable_proven" == 1 ]] || die "old token unavailability was not proven"
printf 'BOOTC_SECURE_RECOVERY: %s: complete\n' "$CASE"
printf 'BOOTC_SECURE_RECOVERY: %s: old-token-unavailable\n' "$CASE"
