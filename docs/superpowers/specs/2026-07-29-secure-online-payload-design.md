# Secure Online Payload Design

Secure Snow live-media assembly must not embed an OCI archive into local
`containers-storage`. A standalone local store is not an accepted source in the
secure trust model: Fisherman must resolve the selected GHCR image online,
Cosign-verify its immutable digest, and pull it through the shipped restrictive
policy.

Both live-squashfs assembly entry points will bypass the entire offline payload
archive/import/copy pipeline when `SECURE_SNOSI=1`. The existing generic path
remains unchanged. The live image will retain its empty writable
`/var/lib/containers/storage` graphroot and secure policy so the later signed
registry pull can proceed.

Tests will statically execute an extracted fixture of the embedding decision to
prove secure mode performs no archive import while generic mode retains it.
They will also assert the secure policy rejects by default and permits only
sigstore-signed Frostyard registry repositories, with the narrow local
`containers-storage` transfer exception intact.
