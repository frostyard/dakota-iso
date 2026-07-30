# Secure Snow CI OCI Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the secure Snow ISO publisher select the runner-bundled `runc` so Podman builds succeed on GitHub runner image `20260726`.

**Architecture:** Add one job-local containers configuration step before the Snow workflow's first Podman invocation and fail closed if Podman does not report `runc`. Protect the ordering and exact runtime selection with the existing secure-media static test suite; do not alter shipped-image runtime policy or any other Dakota workflow.

**Tech Stack:** GitHub Actions YAML, Podman 5.8.4, containers.conf, Python unittest, actionlint

## Global Constraints

- Modify only the secure Snow publisher's OCI runtime selection; do not change other workflows.
- The effective runtime must be exactly `runc`, verified through `sudo podman info --format '{{.Host.OCIRuntime.Name}}'`.
- Runtime selection must occur after Podman installation and before the first Podman command.
- Do not alter payload selection, immutable installer/Fisherman pins, R2 publication behavior, or runtime policy inside the ISO.
- Preserve existing untracked `.pytest_cache/` and any unrelated worktree state.

---

### Task 1: Select And Guard The Snow Publisher Runtime

**Files:**
- Modify: `tests/test_snow_secure_media.py:338-343`
- Modify: `.github/workflows/build-iso-snow.yml:46-52`
- Modify: `CLAUDE.md:63-68`
- Modify: `docs/snow-secure-media.md:42-50`

**Interfaces:**
- Consumes: GitHub runner's `/etc/containers/containers.conf.d` configuration and runner-bundled `runc` executable.
- Produces: A Snow workflow whose effective Podman OCI runtime is asserted as the literal string `runc` before GHCR login or container build.

- [ ] **Step 1: Write the failing workflow contract test**

Add this method to `TestSnowSecureMedia` after `test_snow_publisher_cannot_fall_back_to_the_generic_media_path`:

```python
    def test_snow_publisher_selects_compatible_podman_runtime(self):
        content = SNOW_WORKFLOW.read_text()
        install = content.index("- name: Install dependencies")
        runtime = content.index("- name: Configure Podman OCI runtime")
        login = content.index("- name: Log in to GHCR")
        self.assertLess(install, runtime)
        self.assertLess(runtime, login)
        for marker in (
            "/etc/containers/containers.conf.d/99-dakota-ci-runtime.conf",
            'runtime = "runc"',
            "sudo podman info --format '{{.Host.OCIRuntime.Name}}'",
            "[[ $runtime == runc ]]",
        ):
            self.assertIn(marker, content)
```

- [ ] **Step 2: Run the focused test and confirm the expected failure**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
  tests.test_snow_secure_media.TestSnowSecureMedia.test_snow_publisher_selects_compatible_podman_runtime
```

Expected: `ERROR` with `ValueError: substring not found` for `- name: Configure Podman OCI runtime`.

- [ ] **Step 3: Add the minimal job-local runtime configuration**

Insert this step in `.github/workflows/build-iso-snow.yml` immediately after `Install dependencies` and before `Log in to GHCR`:

```yaml
      - name: Configure Podman OCI runtime
        # GitHub runner 20260726 upgraded Podman to 5.8.4; its default crun
        # rejects the generated OCI version. The bundled runc is compatible.
        run: |
          sudo install -d -m 0755 /etc/containers/containers.conf.d
          printf '%s\n' '[engine]' 'runtime = "runc"' \
            | sudo tee /etc/containers/containers.conf.d/99-dakota-ci-runtime.conf >/dev/null
          runtime=$(sudo podman info --format '{{.Host.OCIRuntime.Name}}')
          [[ $runtime == runc ]] || {
            echo "::error::Podman selected OCI runtime '$runtime', expected 'runc'" >&2
            exit 1
          }
```

- [ ] **Step 4: Document the compatibility boundary**

Append this paragraph to the `CI build (GitHub Actions)` section in `CLAUDE.md`:

```markdown
The secure Snow publisher selects the runner-bundled `runc` through a
job-local `/etc/containers/containers.conf.d` drop-in and verifies the effective
runtime before its first Podman operation. GitHub runner image `20260726` ships
Podman 5.8.4 with a default `crun` path that rejects the generated OCI version;
this CI-only selection does not change the container policy shipped in an ISO.
```

Insert this paragraph before the smoke-test instructions in `docs/snow-secure-media.md`:

```markdown
The GitHub publisher configures and verifies the runner-bundled `runc` before
using Podman. This is a hosted-runner compatibility choice for Podman 5.8.4 on
runner image `20260726`; it does not alter the live environment's runtime or
container-signature policy.
```

- [ ] **Step 5: Run focused and related fixture tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
  tests.test_snow_secure_media \
  tests.test_bootc_secure_runners
```

Expected: all tests pass and no `__pycache__` directory is created by this run.

- [ ] **Step 6: Validate workflow syntax**

Run:

```bash
actionlint .github/workflows/build-iso-snow.yml
```

Expected: exit status 0 with no diagnostics.

- [ ] **Step 7: Review and commit the implementation**

Run:

```bash
git diff --check
git diff -- .github/workflows/build-iso-snow.yml tests/test_snow_secure_media.py CLAUDE.md docs/snow-secure-media.md
git status --short
git add .github/workflows/build-iso-snow.yml tests/test_snow_secure_media.py CLAUDE.md docs/snow-secure-media.md
git commit -m "ci: select runc for secure Snow build"
```

Expected: the commit contains exactly the workflow, test, and two documentation changes; `.pytest_cache/` remains untracked.

### Task 2: Publish And Verify The Non-Publishing ISO Build

**Files:**
- No repository file changes.

**Interfaces:**
- Consumes: Task 1's merged Snow workflow and the four configured `SNOW_SECURE_*` repository secrets.
- Produces: A retained GitHub Actions `snow-live-iso` artifact whose workflow passed container build, ISO assembly, and enforced/tampered Secure Boot smoke validation without writing to R2.

- [ ] **Step 1: Push the implementation branch and open a PR**

Run:

```bash
git push -u origin fix/snow-ci-runc
PR_URL=$(gh pr create --repo frostyard/dakota-iso \
  --base main \
  --head fix/snow-ci-runc \
  --title "ci: select runc for secure Snow build" \
  --body $'## Summary\n- select and verify the runner-bundled runc in the secure Snow publisher\n- keep the compatibility workaround local to build-iso-snow.yml\n- document and statically test the runtime ordering\n\n## Failure evidence\nRun 30508140896 failed before artifact download because default crun returned: unknown version specified\n\n## Validation\n- Python secure-media and Task 9 runner fixtures\n- actionlint')
PR_NUMBER=${PR_URL##*/}
printf 'PR %s: %s\n' "$PR_NUMBER" "$PR_URL"
```

Expected: the PR body states the observed run `30508140896`, the exact `crun` error `unknown version specified`, the Snow-only scope, and the focused test/actionlint results.

- [ ] **Step 2: Merge after required checks pass**

Run:

```bash
PR_NUMBER=$(gh pr view --repo frostyard/dakota-iso --json number --jq .number)
gh pr checks "$PR_NUMBER" --repo frostyard/dakota-iso --watch
gh pr diff "$PR_NUMBER" --repo frostyard/dakota-iso
gh pr merge "$PR_NUMBER" --repo frostyard/dakota-iso --squash --delete-branch
```

Expected: `main` contains the runtime configuration and no unrelated generated files.

- [ ] **Step 3: Dispatch a non-publishing secure ISO build**

Run:

```bash
RUN_URL=$(gh workflow run build-iso-snow.yml \
  --repo frostyard/dakota-iso \
  --ref main \
  -f installer_channel=stable \
  -f skip_upload=true)
RUN_ID=${RUN_URL##*/}
printf 'Run %s: %s\n' "$RUN_ID" "$RUN_URL"
```

Expected: a new workflow-dispatch run starts from the merged `main` commit.

- [ ] **Step 4: Verify the complete workflow result**

Run:

```bash
RUN_ID=$(gh run list --repo frostyard/dakota-iso \
  --workflow build-iso-snow.yml --event workflow_dispatch --limit 1 \
  --json databaseId --jq '.[0].databaseId')
gh run watch "$RUN_ID" --repo frostyard/dakota-iso --exit-status
```

Expected:

```text
Configure Podman OCI runtime       success
Build live container              success
Build live squashfs + boot tar    success
Assemble ISO                      success
Upload ISO artifact               success
Secure Boot live-media smoke      success
Upload ISO to Cloudflare R2       skipped
```

- [ ] **Step 5: Record immutable evidence**

Run:

```bash
RUN_ID=$(gh run list --repo frostyard/dakota-iso \
  --workflow build-iso-snow.yml --event workflow_dispatch --limit 1 \
  --json databaseId --jq '.[0].databaseId')
gh run view "$RUN_ID" --repo frostyard/dakota-iso \
  --json url,headSha,status,conclusion
gh api "repos/frostyard/dakota-iso/actions/runs/${RUN_ID}/artifacts" \
  --jq '.artifacts[] | {name,size_in_bytes,expired,archive_download_url}'
```

Expected: conclusion `success`, merged `main` SHA, and a non-expired `snow-live-iso` artifact. Record the run URL and artifact metadata in the task report; do not claim installed-system Task 9 evidence from this media-only smoke run.
