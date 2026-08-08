#!/usr/bin/bash
set -Eeuo pipefail
# shellcheck source=lib/bootc-secure-runner-lib.sh
# shellcheck disable=SC1091 # Resolved from this script's directory at runtime.
source "$(dirname "$0")/lib/bootc-secure-runner-lib.sh"
[[ "$#" == 14 && "$1" == --case && "$3" == --profile && "$5" == --oci-ref && "$7" == --state && "$9" == --iso && "${11}" == --recipe && "${13}" == --recovery-key ]] || { echo "Usage: $0 --case CASE --profile PROFILE --oci-ref REF --state STATE --iso ISO --recipe RECIPE --recovery-key KEY" >&2; exit 1; }
CASE="$2"; case "$CASE" in tpm-replacement|recovery-reenrollment) ;; *) die "unknown secure recovery case: $CASE";; esac
validate_state "$8"; validate_recipe "${12}"; secure_env; need ssh; need ssh-keygen; need scp; need socat; need swtpm; need jq
DISK="$(json_string "$8" target_disk)"; SSH_KEY="$(json_string "$8" ssh_key)"; RECOVERY="${14}"; [[ -r "$RECOVERY" && -r "$SSH_KEY" && ( -b "$DISK" || -f "$DISK" ) ]] || blocked "recovery credential, state SSH key, or retained target disk is unavailable"
WORK="$(mktemp -d /var/tmp/snosi-task9-recovery.XXXXXX)"; umask 077
stop_tpm() { [[ -f "$WORK/swtpm.pid" ]] && kill "$(<"$WORK/swtpm.pid")" 2>/dev/null || true; }
# Name the failure. Every deliberate exit here goes through die/blocked, which
# print a reason, but any OTHER nonzero command dies on `set -e` with nothing
# said -- and snosi can only report "failed or omitted its completion marker".
# That reduced a real defect to four stray ssh lines and cost a whole lane run
# to re-observe. `set -E` propagates this trap into functions and subshells;
# without it the trap does not fire inside assert_stale_token_rejected.
on_err() {
    local status=$? line=$1 cmd=$2
    printf 'ERROR: %s: line %s exited %s: %s\n' "${0##*/}" "$line" "$status" "$cmd" >&2
    printf 'ERROR: last phase reached: %s\n' "${PHASE:-<none>}" >&2
    if [[ -r "$WORK/serial.log" ]]; then
        printf -- '--- guest serial console, last 60 lines ---\n' >&2
        tail -n 60 "$WORK/serial.log" >&2 || true
        printf -- '--- end serial console ---\n' >&2
    fi
}
trap 'on_err "$LINENO" "$BASH_COMMAND"' ERR
trap 'stop_vm "$WORK"; stop_tpm; rm -f "$SNOSI_SECURE_TPM_SOCKET"; rm -rf "$WORK"' EXIT
phase() { PHASE="$1"; printf 'recovery-runner: %s\n' "$1" >&2; }
start_tpm() {
    rm -f "$SNOSI_SECURE_TPM_SOCKET"
    swtpm socket --tpm2 --tpmstate "dir=$SNOSI_SECURE_TPM_STATE" --ctrl "type=unixio,path=$SNOSI_SECURE_TPM_SOCKET" --pid "file=$WORK/swtpm.pid" --daemon
    # --daemon forks before the control socket necessarily exists; QEMU fails
    # outright rather than retrying if it connects too early.
    for _ in $(seq 1 50); do [[ -S "$SNOSI_SECURE_TPM_SOCKET" ]] && return 0; sleep 0.1; done
    blocked "swtpm control socket never appeared at $SNOSI_SECURE_TPM_SOCKET"
}
# Bring swtpm back up around a VM stop, whether or not it followed its client
# down. Tearing the old one down first keeps this correct in both cases: two
# swtpm processes sharing one --tpmstate dir would be worse than the failure
# this repairs. Never wipes state -- callers own that decision.
restart_tpm() {
    local pid
    if [[ -f "$WORK/swtpm.pid" ]]; then
        pid="$(<"$WORK/swtpm.pid")"
        kill -TERM "$pid" 2>/dev/null || true
        for _ in $(seq 1 50); do kill -0 "$pid" 2>/dev/null || break; sleep 0.1; done
        kill -0 "$pid" 2>/dev/null && kill -KILL "$pid" 2>/dev/null || true
    fi
    start_tpm
}
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
    phase "stale token rejected; stopping the replacement-TPM VM"
    stop_vm "$WORK"; rm -f "$WORK/monitor.sock" "$WORK/qemu.pid"
    # The observed failure: after this VM stops, the swtpm control socket is
    # gone, and the live guest started next dies at -chardev with "No such file
    # or directory". Rather than depend on exactly when swtpm decides to follow
    # its client down -- a bare connect/disconnect provably does NOT kill it,
    # so the trigger is something in QEMU's session teardown -- make the state
    # deterministic: tear swtpm down explicitly, then bring it back up.
    #
    # Re-arm against the SAME --tpmstate dir. This is the replacement TPM whose
    # staleness the proof above just established; reinitializing it here would
    # silently discard exactly what the rest of the case goes on to rotate.
    phase "re-arming swtpm against the retained replacement TPM state"
    restart_tpm
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
phase "allocating a port for the live guest"
PORT="$(ssh_port)"
phase "starting the live guest from ${10}"
start_live "${10}" "$DISK" "$WORK" "$PORT"
phase "waiting for live guest SSH on 127.0.0.1:$PORT"
wait_live_ssh "$PORT"
phase "staging the recovery credential in the live guest"
live_ssh "$PORT" "sudo install -d -m 0700 /run/snosi-task9"
scp_live "$PORT" "$RECOVERY" /run/snosi-task9/recovery.key
phase "rotating the LUKS TPM token"
# The rotation block below also clears systemd's stored TPM SRK public key.
#
# A replaced TPM leaves that key belonging to the OLD one, and
# systemd-tpm2-setup{,-early} then fail on every subsequent boot with
#
#     TPM key integrity check failed. Key most likely does not belong to this TPM.
#
# so a system recovered by this exact procedure boots, TPM-unlocks, and is
# permanently degraded. Observed live on snosi run 31235071207, where the
# install harness had already reported "no system units failed" BEFORE the
# recovery legs and never re-checked afterwards.
#
# Cleared unconditionally rather than only for tpm-replacement: it is done
# inside the mount this block already holds, so it costs nothing, and on the
# same-TPM rotation case systemd simply re-derives an identical key from the
# unchanged TPM. A case-conditional would have meant a second VM round trip
# and a second LUKS open for no behavioural difference.
#
# /var on a composefs deployment is state/os/<stateroot>/var. <root>/var also
# exists, is a different directory, and nothing mounts it -- writing there
# would silently do nothing, which is why the glob is asserted to match once.
# shellcheck disable=SC2016 # This is intentionally expanded by the remote shell.
live_ssh "$PORT" 'sudo bash -ceu '"'"'mapfile -t luks < <(lsblk -nrpo NAME,FSTYPE /dev/vda | awk "\$2==\"crypto_LUKS\"{print \$1}"); ((${#luks[@]} == 1)) || { echo "expected exactly one LUKS device" >&2; exit 1; }; cryptsetup open --key-file=/run/snosi-task9/recovery.key "${luks[0]}" root; backing=$(cryptsetup status root | awk "\$1==\"device:\"{print \$2;exit}"); [[ "$backing" == "${luks[0]}" ]] || exit 1; old_token=$(cryptsetup luksDump --dump-json-metadata "$backing" | jq -c ".tokens | to_entries[] | select(.value.type == \"systemd-tpm2\")"); m=$(mktemp -d); mount /dev/mapper/root "$m"; esp=$(lsblk -nrpo NAME,PARTTYPE /dev/vda | awk "tolower(\$2)==\"c12a7328-f81f-11d2-ba4b-00a0c93ec93b\"{print \$1}"); test $(wc -w <<<"$esp") -eq 1; mount "$esp" "$m/boot/efi"; entry=$(find "$m/boot/efi/loader/entries" -name "*.conf" | head -1); uki=$(awk "\$1==\"uki\" || \$1==\"efi\"{print \$2;exit}" "$entry"); cp "$m/boot/efi/${uki#/}" /run/uki-copy.efi; objcopy --dump-section .pcrpkey=/run/pcr-public /run/uki-copy.efi /run/uki-discard.efi; rm -f /run/uki-copy.efi /run/uki-discard.efi; systemd-cryptenroll --wipe-slot=tpm2 "$backing"; test -z "$(cryptsetup luksDump --dump-json-metadata "$backing" | jq -r ".tokens | to_entries[]? | select(.value.type == \"systemd-tpm2\") | .key")"; systemd-cryptenroll --unlock-key-file=/run/snosi-task9/recovery.key --tpm2-device=auto --tpm2-pcrs= --tpm2-public-key=/run/pcr-public --tpm2-public-key-pcrs=11 --tpm2-pcrlock= "$backing"; new_token=$(cryptsetup luksDump --dump-json-metadata "$backing" | jq -c ".tokens | to_entries[] | select(.value.type == \"systemd-tpm2\")"); [[ "$old_token" != "$new_token" ]]; cryptsetup open --test-passphrase --key-file=/run/snosi-task9/recovery.key "$backing"; mapfile -t srkvars < <(printf "%s\n" "$m"/state/os/*/var); { ((${#srkvars[@]} == 1)) && test -d "${srkvars[0]}"; } || { echo "expected exactly one stateroot var, got: ${srkvars[*]}" >&2; exit 1; }; rm -f "${srkvars[0]}/lib/systemd/tpm2-srk-public-key.pem"; sync; rm -rf /run/snosi-task9 /run/pcr-public; umount "$m/boot/efi"; umount "$m"; rmdir "$m"; cryptsetup close root'"'"''
# A REPLACED TPM leaves systemd's stored SRK public key belonging to the old
# one. systemd-tpm2-setup{,-early} then fail every boot with
#
#     TPM key integrity check failed. Key most likely does not belong to this TPM.
#
# so a system recovered by this exact procedure boots, unlocks, and is
# permanently degraded. Observed on snosi run 31235071207. Clearing the stale
# key lets systemd-tpm2-setup re-derive it from the new TPM on next boot.
#
# tpm-replacement ONLY. recovery-reenrollment rotates the LUKS token against
# the SAME TPM, where the stored key is still correct and removing it would be
# churn for no reason.
#
# /var on a composefs deployment is state/os/<stateroot>/var, not <root>/var --
# the latter exists and is a different directory nothing mounts, so writing
# there would silently do nothing.
if [[ "$CASE" == recovery-reenrollment ]]; then
    old_unavailable_proven=1
fi
[[ "$old_unavailable_proven" == 1 ]] || die "old token unavailability was not proven"
printf 'BOOTC_SECURE_RECOVERY: %s: complete\n' "$CASE"
printf 'BOOTC_SECURE_RECOVERY: %s: old-token-unavailable\n' "$CASE"
