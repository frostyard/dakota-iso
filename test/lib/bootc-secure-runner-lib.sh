#!/usr/bin/bash
# Shared Task 9 runner helpers. These runners are test adapters, never media build inputs.
set -euo pipefail

blocked() { printf 'BLOCKED: %s\n' "$*" >&2; exit 2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || blocked "missing required host tool: $1"; }
secure_env() {
    for path in "${SNOSI_SECURE_OVMF_CODE:-}" "${SNOSI_SECURE_OVMF_VARS:-}" "${SNOSI_SECURE_TPM_STATE:-}"; do
        [[ -n "$path" && -e "$path" ]] || blocked "SNOSI_SECURE_OVMF_CODE, SNOSI_SECURE_OVMF_VARS, and SNOSI_SECURE_TPM_STATE are required"
    done
    [[ -n "${SNOSI_SECURE_TPM_SOCKET:-}" ]] || blocked "SNOSI_SECURE_TPM_SOCKET is required"
}
json_string() { jq -er --arg key "$2" '.[$key] | strings' "$1"; }
composefs_persistent_etc() {
    local mount="$1"
    local -a etcs=("$mount/state/deploy"/*/etc)
    [[ ${#etcs[@]} -eq 1 && -d ${etcs[0]} ]] || {
        printf 'expected exactly one composefs persistent etc\n' >&2
        return 1
    }
    printf '%s\n' "${etcs[0]}"
}
same_secure_repo() {
    local image="$1" tracking="$2" profile="$3"
    local repo="ghcr.io/frostyard/$profile"
    [[ "$image" == "$repo@sha256:"* && "${image#"$repo@sha256:"}" =~ ^[a-f0-9]{64}$ ]] || die "oci_ref must be an immutable $repo digest"
    local tag="${tracking#"$repo:"}"
    [[ "$tracking" == "$repo:"* && "$tag" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]] || die "tracking_ref must be a $repo tag"
}
validate_recipe() {
    local recipe="$1"; need jq; [[ -r "$recipe" ]] || die "recipe is unreadable"
    jq -e 'type == "object" and (keys | sort) == ["mok_certificate","oci_ref","pcr_public_key","profile","recovery_key","root_ssh_authorized_key","schema","target_disk","tracking_ref"] and .schema == 1' "$recipe" >/dev/null || die "recipe must be schema-1 JSON with exactly the Task 9 keys"
    local profile image tracking
    profile="$(json_string "$recipe" profile)"; image="$(json_string "$recipe" oci_ref)"; tracking="$(json_string "$recipe" tracking_ref)"
    [[ "$profile" =~ ^(cayo|snow|snowfield)$ ]] || die "unsupported secure profile"
    same_secure_repo "$image" "$tracking" "$profile"
    for key in target_disk recovery_key mok_certificate pcr_public_key root_ssh_authorized_key; do [[ -n "$(json_string "$recipe" "$key")" ]] || die "recipe $key is empty"; done
}
validate_state() {
    local state="$1"; need jq; [[ -r "$state" ]] || blocked "secure state manifest is unavailable"
    [[ "$(stat -c '%a' "$state")" == 600 ]] || die "secure state manifest must be mode 0600"
    jq -e 'type == "object" and (.target_disk|strings) and (.ssh_private_key|strings)' "$state" >/dev/null || die "secure state manifest is not path-only Task 9 state"
}
qemu_bin() { command -v qemu-system-x86_64 || blocked "qemu-system-x86_64 is required"; }
ssh_port() { python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()' 2>/dev/null || blocked "python3 is required to allocate an SSH port"; }
# Build the harness's SSH channel into the live guest.
#
# The published secure media has sshd INSTALLED but not enabled, and liveuser
# has an empty password hash, so the original password-helper transport
# (`-p live` as liveuser) could never connect. Rather than enable sshd or set a password on
# media that real users boot, the harness injects a one-shot unit as a systemd
# credential over SMBIOS type 11. Nothing is enabled unless someone controls the
# VM definition, which is a strictly stronger gate than a kernel argument, and
# no password exists anywhere.
#
# Sets SNOSI_TASK9_LIVE_KEY for live_ssh/scp_live. Echoes the two -smbios
# arguments for the caller to splice into its QEMU invocation.
# Create the harness keypair. MUST be called from the parent shell, not from
# inside the command substitution that builds the credentials: `mapfile < <(...)`
# runs in a subshell, so an export there is lost and live_ssh later dereferences
# an unset SNOSI_TASK9_LIVE_KEY.
live_ssh_keygen() {
    local work="$1"
    [[ -n "${SNOSI_TASK9_LIVE_KEY:-}" ]] || SNOSI_TASK9_LIVE_KEY="$work/live_ssh_key"
    export SNOSI_TASK9_LIVE_KEY
    [[ -f "$SNOSI_TASK9_LIVE_KEY" ]] || \
        ssh-keygen -q -t ed25519 -N '' -C snosi-task9-live -f "$SNOSI_TASK9_LIVE_KEY" </dev/null
}

live_ssh_credentials() {
    local pub unit dropin
    [[ -n "${SNOSI_TASK9_LIVE_KEY:-}" && -f "${SNOSI_TASK9_LIVE_KEY}.pub" ]] \
        || die "live_ssh_keygen must run before live_ssh_credentials"
    pub="$(base64 -w0 <"${SNOSI_TASK9_LIVE_KEY}.pub")"

    # ssh-keygen -A because the live image generates host keys lazily; starting
    # sshd without them fails. ssh.socket first, falling back to ssh.service,
    # covers both Debian activation styles.
    unit="[Unit]
Description=snosi Task 9 QA SSH (harness-injected; never present on installed systems)
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/bash -c 'set -eu; \
install -d -m 0700 /root/.ssh; \
echo ${pub} | base64 -d > /root/.ssh/authorized_keys; \
chmod 600 /root/.ssh/authorized_keys; \
install -d -m 0755 /etc/ssh/sshd_config.d; \
printf \"%s\\n\" \"PermitRootLogin prohibit-password\" \"PasswordAuthentication no\" \"KbdInteractiveAuthentication no\" > /etc/ssh/sshd_config.d/99-snosi-task9.conf; \
ssh-keygen -A || true; \
systemctl start ssh.socket 2>/dev/null || systemctl start ssh.service'"
    dropin="[Unit]
Wants=snosi-task9-ssh.service"

    # One argv element per line. `-smbios` and its value must be SEPARATE
    # arguments; emitting "-smbios type=11,..." as a single element makes QEMU
    # report `invalid option` on the whole string.
    printf '%s\n%s\n%s\n%s\n' \
        '-smbios' "type=11,value=io.systemd.credential.binary:systemd.extra-unit.snosi-task9-ssh.service=$(printf '%s' "$unit" | base64 -w0)" \
        '-smbios' "type=11,value=io.systemd.credential.binary:systemd.unit-dropin.multi-user.target=$(printf '%s' "$dropin" | base64 -w0)"
}

start_live() {
    local iso="$1" disk="$2" work="$3" port="$4" qemu
    [[ -r "$iso" ]] || blocked "secure Dakota ISO artifact is required"
    qemu="$(qemu_bin)"; need swtpm; need ssh-keygen
    local -a creds=()
    # Remember where the guest console lands so a timeout can show it. Without
    # this a failed wait reports only that SSH never came up, which says nothing
    # about whether the guest booted, panicked, or simply never started sshd.
    SNOSI_TASK9_SERIAL="$work/serial.log"
    export SNOSI_TASK9_SERIAL
    live_ssh_keygen "$work"
    mapfile -t creds < <(live_ssh_credentials)
    "$qemu" -machine q35,accel=kvm -cpu host -m 8G -smp 4 \
        "${creds[@]}" \
        -drive "if=pflash,format=raw,readonly=on,file=$SNOSI_SECURE_OVMF_CODE" \
        -drive "if=pflash,format=raw,file=$SNOSI_SECURE_OVMF_VARS" \
        -drive "if=none,id=iso,file=$iso,media=cdrom,readonly=on,format=raw" -device virtio-scsi-pci,id=scsi -device scsi-cd,drive=iso \
        -drive "if=none,id=target,file=$disk,format=raw" -device virtio-blk-pci,drive=target \
        -chardev "socket,id=chrtpm,path=$SNOSI_SECURE_TPM_SOCKET" -tpmdev emulator,id=tpm0,chardev=chrtpm -device tpm-tis,tpmdev=tpm0 \
        -netdev "user,id=net0,hostfwd=tcp:127.0.0.1:${port}-:22" -device virtio-net-pci,netdev=net0 \
        -monitor "unix:$work/monitor.sock,server=on,wait=off" -serial "file:$work/serial.log" -display none -pidfile "$work/qemu.pid" -daemonize
}
start_installed() {
    local disk="$1" work="$2" port="$3" qemu
    qemu="$(qemu_bin)"
    "$qemu" -machine q35,accel=kvm -cpu host -m 4G -smp 2 \
        -drive "if=pflash,format=raw,readonly=on,file=$SNOSI_SECURE_OVMF_CODE" \
        -drive "if=pflash,format=raw,file=$SNOSI_SECURE_OVMF_VARS" \
        -drive "if=none,id=target,file=$disk,format=raw" -device virtio-blk-pci,drive=target \
        -chardev "socket,id=chrtpm,path=$SNOSI_SECURE_TPM_SOCKET" -tpmdev emulator,id=tpm0,chardev=chrtpm -device tpm-tis,tpmdev=tpm0 \
        -netdev "user,id=net0,hostfwd=tcp:127.0.0.1:${port}-:22" -device virtio-net-pci,netdev=net0 \
        -monitor "unix:$work/monitor.sock,server=on,wait=off" -serial "file:$work/serial.log" -display none -pidfile "$work/qemu.pid" -daemonize
}
stop_vm() { local work="$1" pid; [[ -S "$work/monitor.sock" ]] && printf 'quit\n' | socat - "UNIX-CONNECT:$work/monitor.sock" >/dev/null 2>&1 || true; [[ -f "$work/qemu.pid" ]] || return; pid="$(<"$work/qemu.pid")"; kill -TERM "$pid" 2>/dev/null || true; for _ in $(seq 1 10); do kill -0 "$pid" 2>/dev/null || return; sleep 1; done; kill -KILL "$pid" 2>/dev/null || true; kill -0 "$pid" 2>/dev/null && die "QEMU did not stop" || true; }
# Key auth as root, via the credential-injected unit above. Logging in as root
# removes the sudo hop the old liveuser path needed, and drops the
# password-helper dependency entirely.
live_ssh() { ssh -i "$SNOSI_TASK9_LIVE_KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o BatchMode=yes -o ConnectTimeout=5 -p "$1" "${SNOSI_TASK9_LIVE_USER:-root}"@127.0.0.1 "$2"; }
scp_live() { scp -q -i "$SNOSI_TASK9_LIVE_KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o BatchMode=yes -P "$1" "$2" "${SNOSI_TASK9_LIVE_USER:-root}"@127.0.0.1:"$3"; }
root_ssh() { ssh -i "$1" -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=3 -p "$2" root@127.0.0.1 true; }
# 600s by default, not 300s. The secure Snow media boots a full desktop session
# before the injected unit can start sshd, and 60x5s was marginal enough to pass
# on one run and time out on the next against identical media. frostyard/lab
# uses the same 600s budget for this ISO in its other lanes.
wait_live_ssh() {
    local port="$1" attempts="${SNOSI_TASK9_LIVE_SSH_ATTEMPTS:-120}"
    for _ in $(seq 1 "$attempts"); do live_ssh "$port" true >/dev/null 2>&1 && return; sleep 5; done
    if [[ -n "${SNOSI_TASK9_SERIAL:-}" && -r "$SNOSI_TASK9_SERIAL" ]]; then
        printf -- '--- guest serial console, last 120 lines (%s) ---\n' "$SNOSI_TASK9_SERIAL" >&2
        tail -n 120 "$SNOSI_TASK9_SERIAL" >&2 || true
        printf -- '--- end serial console ---\n' >&2
    else
        printf 'No serial log recorded at %s\n' "${SNOSI_TASK9_SERIAL:-<unset>}" >&2
    fi
    die "Dakota live SSH did not become ready after $((attempts * 5))s"
}
