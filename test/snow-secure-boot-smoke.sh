#!/usr/bin/bash
# Verify a freshly-built secure Snow ISO under Microsoft-enrolled Secure Boot.
# This is live-media coverage only; encrypted installed-system E2E is Task 9.
set -euo pipefail

ISO="${1:?Usage: snow-secure-boot-smoke.sh <snow-live.iso>}"
QEMU="${QEMU:-$(command -v qemu-system-x86_64)}"
CODE="${OVMF_CODE:-/usr/share/OVMF/OVMF_CODE_4M.secboot.fd}"
VARS_SOURCE="${OVMF_VARS:-/usr/share/OVMF/OVMF_VARS_4M.ms.fd}"
WORK="$(mktemp -d /var/tmp/snow-secure-smoke.XXXXXX)"
POSITIVE_PID=""
NEGATIVE_PID=""
cleanup() {
    for pid in "${POSITIVE_PID}" "${NEGATIVE_PID}"; do
        [[ -n "${pid}" ]] && kill "${pid}" 2>/dev/null || true
    done
    rm -rf "${WORK}"
}
trap cleanup EXIT

[[ -r "${ISO}" ]] || { echo "BLOCKED: secure ISO artifact is required" >&2; exit 2; }
for tool in "$QEMU" sbverify objdump od dd xorriso mcopy; do
    command -v "$tool" >/dev/null || { echo "BLOCKED: missing $tool" >&2; exit 2; }
done
[[ -r "$CODE" && -r "$VARS_SOURCE" ]] || { echo "BLOCKED: Microsoft-enrolled OVMF files are required" >&2; exit 2; }

cp "$VARS_SOURCE" "${WORK}/vars.fd"
xorriso -osirrox on -indev "$ISO" -extract /EFI/efi.img "${WORK}/efi.img" >/dev/null 2>&1
for asset in BOOTX64.EFI grubx64.efi mmx64.efi; do
    mcopy -i "${WORK}/efi.img" "::/EFI/BOOT/${asset}" "${WORK}/${asset}"
done
sbverify --list "${WORK}/BOOTX64.EFI" >/dev/null
sbverify --list "${WORK}/grubx64.efi" >/dev/null
test -s "${WORK}/mmx64.efi"

"$QEMU" -machine q35,accel=kvm -cpu host -m 4G -smp 2 \
    -drive "if=pflash,format=raw,readonly=on,file=${CODE}" \
    -drive "if=pflash,format=raw,file=${WORK}/vars.fd" \
    -cdrom "$ISO" -serial "file:${WORK}/serial.log" -display none \
    -no-reboot -pidfile "${WORK}/positive.pid" -daemonize
POSITIVE_PID="$(<"${WORK}/positive.pid")"
for _ in $(seq 1 60); do
    grep -q DAKOTA_LIVE_READY "${WORK}/serial.log" 2>/dev/null && break
    sleep 5
done
grep -q DAKOTA_LIVE_READY "${WORK}/serial.log" || { tail -50 "${WORK}/serial.log" >&2; exit 1; }
kill "${POSITIVE_PID}" 2>/dev/null || true
wait "${POSITIVE_PID}" 2>/dev/null || true
POSITIVE_PID=""

# Modify one byte in GRUB's signed .text section. Do not rebuild the PE: its
# signature table must remain parseable so this proves signature enforcement.
text_offset="$(objdump -h "${WORK}/grubx64.efi" | awk '$2 == ".text" { print "0x" $6; exit }')"
[[ -n "${text_offset}" ]] || { echo "ERROR: signed GRUB has no .text section" >&2; exit 1; }
cp "${WORK}/grubx64.efi" "${WORK}/tampered-grubx64.efi"
original_byte="$(od -An -tu1 -j "$((text_offset + 1))" -N 1 "${WORK}/tampered-grubx64.efi" | tr -d '[:space:]')"
[[ "${original_byte}" =~ ^[0-9]+$ ]] || { echo "ERROR: could not read signed GRUB .text byte" >&2; exit 1; }
printf -v replacement_byte '\\%03o' "$((10#${original_byte} ^ 1))"
printf '%b' "${replacement_byte}" | dd of="${WORK}/tampered-grubx64.efi" bs=1 seek="$((text_offset + 1))" conv=notrunc status=none
cmp -s "${WORK}/grubx64.efi" "${WORK}/tampered-grubx64.efi" && {
    echo "ERROR: tampered loader is byte-identical to signed GRUB" >&2; exit 1;
}
if ! sbverify --list "${WORK}/tampered-grubx64.efi" >/dev/null 2>&1; then
    echo "ERROR: signature table became unreadable after tampering" >&2
    exit 1
fi

# Shim must reject this parseable, signature-invalid replacement on the same
# Microsoft-enrolled firmware. It is not a malformed-file or boot-order proof.
cp "$VARS_SOURCE" "${WORK}/negative-vars.fd"
cp "${WORK}/efi.img" "${WORK}/unsigned-efi.img"
chmod u+w "${WORK}/unsigned-efi.img"
mcopy -o -i "${WORK}/unsigned-efi.img" "${WORK}/tampered-grubx64.efi" ::/EFI/BOOT/grubx64.efi
"$QEMU" -machine q35,accel=kvm -cpu host -m 1G \
    -drive "if=pflash,format=raw,readonly=on,file=${CODE}" \
    -drive "if=pflash,format=raw,file=${WORK}/negative-vars.fd" \
    -drive "if=none,id=esp,file=${WORK}/unsigned-efi.img,format=raw,readonly=on" \
    -device virtio-blk-pci,drive=esp -serial "file:${WORK}/negative.log" \
    -display none -no-reboot -pidfile "${WORK}/negative.pid" -daemonize
NEGATIVE_PID="$(<"${WORK}/negative.pid")"
for _ in $(seq 1 12); do
    grep -Eq 'Security (Policy )?Violation' "${WORK}/negative.log" 2>/dev/null && break
    sleep 5
done
grep -Eq 'Security (Policy )?Violation' "${WORK}/negative.log" || {
    echo "ERROR: signature-invalid loader negative proof did not report a Security Violation" >&2
    exit 1
}
kill "${NEGATIVE_PID}" 2>/dev/null || true
wait "${NEGATIVE_PID}" 2>/dev/null || true
NEGATIVE_PID=""
echo 'Secure Snow live-media smoke passed'
