#!/usr/bin/bash
# Snow-specific live environment adjustments, run at the end of
# configure-live.sh (see the per-variant hook there).
#
# Snow (Debian bootc, frostyard/snosi) ships its own live-session support:
# snow-linux-live-setup.service creates a passwordless "snow" user at boot
# and /etc/gdm3/daemon.conf autologins it.  We keep that flow instead of the
# generic liveuser + /etc/gdm/custom.conf path (Debian GDM ignores the
# latter), and replicate the fixes proven on the titanoboa bootc-installer
# branch (frostyard/titanoboa feat/bootc-installer-live).
set -euxo pipefail

SNOW_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Live-session home must be under real /home, not /var/home: Flatpak gives
# sandboxes a private /var even with --filesystem=host, so a /var/home user
# breaks the installer's host-staging of fisherman/recipe/log (files land in
# the sandbox-private /var and the host-side pkexec launch cannot see them).
# Snow defaults HOME=/var/home with /home as a bind mount of it; on ephemeral
# live media a plain /home directory is fine.
systemctl mask home.mount
sed -i 's|^HOME=/var/home$|HOME=/home|' /etc/default/useradd

# Snow also bind-mounts /var/usrlocal over /usr/local (usr-local.mount); on
# live media the ephemeral /var shadows the fisherman symlink configure-live
# created in /usr/local/bin.  Mask it so the squashfs /usr/local stays visible.
systemctl mask usr-local.mount

# Snow's first-run wizard (snow-first-setup) autostarts for every new user via
# /etc/skel.  On live media the boot-created "snow" user would get it alongside
# the installer — remove it from skel in the live env only (the installed
# system uses the untouched payload image and keeps its first-boot flow).
rm -f /etc/skel/.config/autostart/org.frostyard.FirstSetup.autostart.desktop

# The payload also pins the snow user's GDM session to the dedicated
# firstsetup GNOME session via AccountsService.  Until now this file only
# vanished from the live image by accident (install-flatpaks.sh starts a
# system dbus-daemon, flatpak activates accounts-daemon, and accounts-daemon
# purges cached records for users that don't exist in the build container).
# Delete it explicitly so wizard suppression doesn't hinge on that side
# effect; the installed system keeps it via the pristine embedded payload.
rm -f /var/lib/AccountsService/users/snow

# Polkit: the boot-created live user is "snow", not "liveuser". Cover both.
cat > /etc/polkit-1/rules.d/99-live-installer.rules << 'EOF'
polkit.addRule(function(action, subject) {
    if ((action.id === "org.freedesktop.policykit.exec" ||
         action.id === "org.tunaos.Installer.install") &&
            (subject.user === "snow" || subject.user === "liveuser") &&
            subject.local) {
        return polkit.Result.YES;
    }
});
EOF

# Passwordless sudo for the snow live user (mirrors the liveuser drop-in).
echo 'snow ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/snow-live
chmod 0440 /etc/sudoers.d/snow-live

# cosign public key referenced by recipe.json (cosignPubKey) — consumed by
# the frostyard fisherman build; ignored for offline containers-storage
# installs but staged so registry installs can verify signatures.
install -D -m 0644 "${SNOW_DIR}/cosign.pub" /etc/bootc-installer/cosign.pub

# Swap the bundle's fisherman for the frostyard build when staged (untracked
# live/src/snow/fisherman): carries the composefs scratch-store pull fix and
# cosign verification until upstream releases them.
if [ -x "${SNOW_DIR}/fisherman" ]; then
    BUNDLED=$(find /var/lib/flatpak/app -name fisherman -type f | head -1)
    if [ -n "${BUNDLED}" ]; then
        install -m 0755 "${SNOW_DIR}/fisherman" "${BUNDLED}"
        echo "Replaced bundle fisherman with frostyard build"
    fi
fi

# Rebrand the generic installer desktop entries.
sed -i 's/^Name=.*/Name=Install Snow Linux/' \
    /usr/share/applications/dakota-installer.desktop \
    /etc/xdg/autostart/tuna-installer.desktop || true

# Live hostname.
echo 'f /etc/hostname 0644 - - - snow-live' > /usr/lib/tmpfiles.d/live-hostname.conf

echo "snow live configuration complete"
