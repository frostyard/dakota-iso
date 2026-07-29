# Secure Online Payload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent secure Snow ISO builds from embedding an unaccepted offline OCI payload while preserving generic offline media behavior.

**Architecture:** Guard both offline-embedding entry points with `SECURE_SNOSI=1`. Secure media still receives the existing writable container graphroot and restrictive registry policy, then Fisherman performs the required online Cosign-verified pull. Fixture tests exercise the shell decision without Podman or an ISO build.

**Tech Stack:** Bash, Python `unittest`, ShellCheck

## Global Constraints

- Work only in this repository; do not commit, reset, or clean.
- Do not broaden the shipped secure runtime policy.
- Preserve generic OCI embedding byte-for-byte.
- Secure Fisherman must resolve and policy/Cosign-verify an immutable remote image online.

---

### Task 1: Guard Offline Payload Embedding

**Files:**
- Modify: `scripts/build-live-squashfs.sh:130-266`
- Modify: `scripts/iso-sd-boot.sh:94-244`

- [ ] **Step 1: Write failing fixtures that require secure mode to skip archive import and generic mode to retain it.**
- [ ] **Step 2: Run `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.test_snow_secure_media` and confirm the new fixtures fail.**
- [ ] **Step 3: Wrap each complete offline archive/import/copy pipeline in a secure-mode exclusion, emitting an explicit online-only message. Keep generic commands unchanged.**
- [ ] **Step 4: Run the focused unittest module and confirm it passes.**

### Task 2: Preserve and Document the Secure Runtime Contract

**Files:**
- Modify: `tests/test_snow_secure_media.py`
- Modify: `docs/snow-secure-media.md`
- Modify: `CLAUDE.md:164-173`

- [ ] **Step 1: Add fixtures that assert the secure policy remains default-reject, retains only the exact signed GHCR scopes, and retains the empty writable graphroot setup.**
- [ ] **Step 2: Document that secure media has no offline payload and requires an online immutable, policy- and Cosign-verified Fisherman pull.**
- [ ] **Step 3: Run focused tests, full Python tests, `bash -n` on edited shell scripts, ShellCheck, and diffs.**
