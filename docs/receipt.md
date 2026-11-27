# Receipt format: `coldbox.receipt/v1`

Every job VM writes one JSON receipt to `/var/lib/coldbox/receipts/<id>.json`, with its console log next to it
(`<id>.log`). `coldbox run --receipts DIR` writes them to DIR instead, for stress and test runs. `examples/receipts/` has real ones.

The tail of a truncated log stays in memory until the job finishes. An abrupt worker kill can leave only the head
and marker. Timing statistics that need timestamps from omitted lines may be unavailable. Runner verdicts use
the job result observed during capture.

## Stability

`schema` is `coldbox.receipt/v1`. Within v1, fields are only added. A field is never renamed or removed, and its meaning
never changes. Anything that would break a reader ships as `coldbox.receipt/v2`. Readers should ignore unknown fields.
Optional fields are missing, not `null`, when they don't apply, except where noted.

## Fields

| Field | Type | Meaning |
| --- | --- | --- |
| `schema` | string | `coldbox.receipt/v1` |
| `id` | string | `cbx-<unix time>-<random>`; also the VM name and the GitHub runner name |
| `mode` | string | `local` (`coldbox run PATH` or `--repo OWNER/REPO --sha SHA`), `adhoc` (`coldbox run -- CMD`), `shell` (`coldbox shell --repo PATH`) or `runner` (a GitHub Actions job) |
| `host` | string | Hostname of the machine that ran the VM. Missing in receipts written before it was added |
| `repo` | string | `OWNER/REPO`. Local mode uses the `origin` remote, or the folder name without one; ad-hoc mode is `adhoc` |
| `command` | list | Ad-hoc mode: the command's argv |
| `sha` | string or null | Commit that ran. Runner mode: `GITHUB_SHA`; null if no job was picked up |
| `profile` | string | Requested direct verification profile when the caller passes `--profile` |
| `profile_refusal` | object | Profile refusal details, with `cause` and a human-readable `error` |
| `verdict` | string | See below |
| `checks` | list | Always present; empty unless `coldbox checks` ran. `{check, command, status, exit, seconds}` per command; `status` is `pass`, `fail` or `not_run` (a later command after one failed; `exit` is null). Receipts written before `not_run` say `skipped` for it |
| `checks_verdict` | string | `pass` or `fail`, from `coldbox checks` |
| `cause` | string | Present on a finished receipt with verdict `fail`, `incomplete`, or a profile refusal. See below |
| `reason_code` | string | Stable outcome detail on newly finalized receipts. See below |
| `verification` | object | Check publication state for runner and selected host-suite jobs, and for receipts finished by `coldbox reap`. `pending` means publication is unresolved. `sending` means the POST may have reached GitHub. `posted` records `check_id`. `not_posted` records the `reason` |
| `started_at` | string | ISO 8601 UTC |
| `log` | string | Path of the console log on the host. Coldbox retains up to 1 MiB per job. Longer logs keep the beginning and end with a truncation marker between them |
| `console_truncated_bytes` | int | On truncated logs, the number of original output bytes omitted |
| `exit` | int | Exit code of the job command in the VM |
| `job_seconds` | number | Time the job command ran. Runner mode: includes the wait for GitHub to assign a job |
| `job_started_at`, `job_ended_at` | string | ISO 8601 UTC, when the job command started and ended on the host |
| `seconds` | number | VM create to VM destroyed |
| `error` | string | Why the job could not finish (`TTL reached`, `no job before the burst idle limit`, `stopped`, a failed command) |
| `vm` | object | `name`, `image`, `ttl_seconds`, `offline`, `ready_seconds` (create to VM agent and cloud-init ready), `clone_seconds` (copying the base; `0.0` when claimed from the warm pool), `boot_seconds` (start to ready; `0.0` when claimed from the warm pool), `warm_claimed` (optional bool, `true` when claimed from the standby warm pool), `teardown` (optional string, `"async"` when handed to the background teardown reaper), `host_ceiling` (the host's VM ceiling, its CPU threads), `destroyed` (bool), `destroyed_at`, `destroy_seconds` (delete and confirm), `lifetime_seconds` |
| `resources` | object | Read just before the VM is destroyed: `cpus` and `memory_bytes` given (`limits.cpu`, `limits.memory`), `peak_rss_bytes` (peak resident memory of the VM's QEMU process on the host), `cpu_seconds` (CPU time Incus reports for the VM), `disk_read_bytes` and `disk_write_bytes` (the guest's whole disks, from Incus metrics). Unlike other fields, a value that could not be read is `null`; collecting them never changes the verdict |
| `vm.reclaimed_bytes` | int | Observed drop in ZFS pool usage around successful VM deletion when that VM is the only job using the pool. Missing when the pool delta could not be attributed |
| `cache` | object | Runner jobs and authorized exact-SHA direct jobs of a repo with a host cache folder: `in_bytes` (size of the `npm.tar` unpacked into the VM, 0 when there was none), `in_seconds` and `in_exit` (the guest `tar -x` exit; non-zero never fails the job). After a runner job passes on a push to the default branch, also `out_bytes` and `out_seconds` for the saved `~/.npm`, `out_bytes: 0` with `out_absent: true` when the job never made that folder, or `out_bytes: 0` with `out_refused` when tar failed, it was over 2 GiB or another job of the repo was saving; the saved tar is then kept. `pip` holds the same fields for `pip.tar` and `~/.cache/pip`, `go` for `go.tar` and `~/go/pkg/mod` (the module cache only), `cargo` for `cargo.tar` and `~/.cargo/registry`, `cargo-git` for `cargo-git.tar` and `~/.cargo/git` |
| `artifacts` | list | Present when the job command started and left files in `$COLDBOX_JOB_DIR/artifacts` in the VM: `{path, bytes}` per regular file copied to `<id>.artifacts/` next to the receipt on the host, read just before the VM is destroyed. Symlinks and other non-regular files are skipped. Copying stops at the first file that would take the total past 100 MiB or 1000 files, or after 120 seconds; hard links are copied as separate files. See [Artifact retention](#artifact-retention). Collecting them never changes the verdict |
| `artifacts_truncated` | bool | With `artifacts`: `true` when not every file was copied: one did not fit a cap (it and the rest were skipped), or the copy failed or timed out, including when the reaper deleted the VM at its TTL during the copy. The TTL is never extended for artifacts |
| `admission` | object | Runner mode: `coldbox.admission/v1`, `host`, `primary`, `target`, `guest`, `admitted_at` (Unix seconds), `ttl` (seconds from `admitted_at` to the VM's expiry; the guest refuses the admission once it is older), and `primary_capacity` (always null; older receipts from overflow hosts hold measured host, target, timestamp, total/busy CPU threads, available bytes and VM bytes). Copied to `/etc/coldbox/admission.json` before the runner starts. |
| `runner_id` | int | Runner mode: the GitHub runner id |
| `job_name` | string | Runner mode: the job's display name from the runner log, e.g. `test (3.12)` for a matrix leg. Also set when the runner was stopped while it was running a job |
| `job_result` | string | Runner mode: the runner's conclusion, `Succeeded`, `Failed` or `Canceled` |
| `github` | object | Runner mode, from the job-started hook: `repo`, `sha`, `ref`, `event`, `workflow`, `job` (the job id), `run_id`, `run_attempt`, `actor`, `fork_pr` (true, false, or null when the event can't show where the code came from), `refused` (null or a reason) |
| `tests` | object | Present when JUnit XML reports were found. See below |
| `tests_ran` | int | Tests that ran and were not skipped, `0` when nothing showed a test ran. Includes tests observed before an interruption. Omitted when collection could not run or the count could not be read. See below |
| `tests_missing_reason` | string | Why an incomplete receipt lacks test evidence. See below |
| `tests_source` | string or null | Observed evidence source, including a ColdVerify result with an unreadable count. `junit` identifies the `tests` reports, `cold-verify` identifies a ColdVerify result, `job-log` identifies a test summary parsed from the runner job log, and `host-suite` identifies a host observation. Null indicates no source was found |
| `isolation` | object | `coldbox selftest isolation` only (ad-hoc mode, repo `selftest`): `expected` (`cpus`, `memory_bytes`, `disk_bytes` from the profile, `write_bytes` the root disk plus 1 GiB), `found` (the guest's `probes` with `kind` `host`, `lan`, `tailnet`, `ipv6` or `control`, `address`, `port`, `error` and `errno`, both null when it connected; every probe but the control and public IPv6 targets the host's own address on a port the host is listening on, and `ipv6` probes must fail with ENETUNREACH; `cpus` from `nproc`'s affinity, `memory_bytes` from `MemTotal`, `disk_bytes` the size of the block device holding `/`, `written_bytes`, `write_errno`) or null if the guest reported nothing, and `problems`. Any problem makes the verdict `fail` |
| `reason` | string | Why a job that otherwise succeeded reads `incomplete`: no tests ran, or the test count could not be read |
| `reconciled_by` | string | `coldbox reap` when the reaper, not the worker, finalized the receipt after the worker died and its VM was gone |

### Artifact retention

On file systems with user extended attributes, Coldbox labels each new artifact folder with
`user.coldbox.managed=true` and `user.coldbox.expires`. The expiry is a Unix timestamp based on 30 days of
retention. `coldbox reap --keep-days` adjusts the retention window and keeps evidence while the worker or VM exists.

`user.coldbox.provenance` contains a JSON object with `owner` (`coldbox`), `job_id` (the receipt ID), `repo`,
and `sha` (the job revision, or null when no revision is known). The receipt also records the job ID, repository,
and revision on file systems without extended attributes.

To preserve a managed artifact folder, set its expiry to `never` before the next sweep:

```sh
setfattr -n user.coldbox.expires -v never "$artifact_dir"
```

The folder keeps its provenance when the receipt and log age out. To return it to ordinary retention, set its expiry
back to a Unix timestamp. The next sweep can remove the folder if its receipt has already aged out.

### `verdict`

| Value | Meaning |
| --- | --- |
| `pass` | No `error`, and: runner, `job_result` is `Succeeded`, the job-started hook recorded `github` and `tests_ran` is above 0; local, every check passed, exit 0 and `tests_ran` is above 0; ad-hoc, exit 0 |
| `fail` | A check failed, an ad-hoc command exited non-zero, `isolation` lists a problem, or the runner job failed for a reason other than the executed-test requirement alone |
| `refused` | Coldbox refused a direct verification profile. The job-started hook also refuses fork pull requests, events such as `merge_group` that do not identify the source of the code, and jobs triggered by users without write access on a public repo |
| `canceled` | No `error`, runner, the job-started hook recorded `github`, and `job_result` is `Canceled`: GitHub canceled the job (a newer push, a manual cancel or the workflow's `timeout-minutes`) |
| `idle` | A runner VM reached its TTL, reached the burst 10-minute idle limit, or was stopped (`error` is `stopped`) without being given a job: no `github` and no `job_name` |
| `incomplete` | Anything else: the VM was not confirmed destroyed, an `error` was recorded, it stopped early (including a runner stopped while it was running a job), or no tests ran (`reason` says so) |

### `cause`

`cause` separates "the code failed" from "Coldbox could not run it". It is recomputed with `verdict`.

| `verdict` | `cause` | Meaning |
| --- | --- | --- |
| `fail` | a check name, e.g. `setup` or `test` | The first check whose `status` is `fail`. `coldbox checks` stops there, so the rest read `not_run` |
| `fail` | `checks` | `coldbox checks` failed with no failed check, e.g. nothing to run |
| `fail` | `job` | The runner job's `job_result` is `Failed`, or an ad-hoc command exited non-zero, with no failed check |
| `refused` | `unknown_profile` | The requested commit does not declare the profile |
| `refused` | `unauthorized_profile` | The profile names a check outside the enabled plan |
| `incomplete` | `stopped` | The worker was stopped (`error` is `stopped`) |
| `incomplete` | `vm_not_ready` | The VM was never ready: copy, start, agent or cloud-init failed (`error` says how) |
| `incomplete` | `ttl` | The job command hit the VM's TTL |
| `incomplete` | `infra_error` | Any other `error` after the VM was ready, such as a failed file push or report pull |
| `incomplete` | `vm_not_destroyed` | The VM was not confirmed destroyed |
| `incomplete` | `worker_interrupted` | The worker exited before recording an outcome and the reaper confirmed the VM was gone |
| `incomplete` | `no_tests` | The job succeeded or failed only the executed-test requirement, and no tests ran or the count could not be read (`reason` says which) |
| `incomplete` | `no_result` | Anything else, e.g. the runner never reported a job result |

`stopped`, `vm_not_ready`, `ttl`, `infra_error`, `vm_not_destroyed` and `worker_interrupted` report infrastructure
failures. New `cause` values may be added within v1; read an unknown one by its `verdict`.

A definitive failure stays `fail` even if the job was also stopped or canceled. `canceled`, `idle` and `incomplete`
are never a pass. Older receipts read `incomplete` for both a canceled job and a stopped idle runner, and older receipts
without `tests_ran` could read `pass` with no tests. A reader that sees
`vm.destroyed: false` should treat the host as needing attention. When the worker that wrote a receipt is gone and its
VM no longer exists, `coldbox reap` sets `vm.destroyed: true` and `vm.destroyed_at`, adds `reconciled_by: "coldbox reap"`
and recomputes `verdict`, so a runner stopped (`error` is `stopped`) before it got a job reads `idle`. If the worker
left neither an outcome nor an error, the receipt reads `incomplete` with `cause: "worker_interrupted"`.

### `reason_code`

Use this field to explain a finalized receipt alongside its `verdict`. Older receipts and receipts still being
written may omit it. For a missing or unfamiliar code, use `verdict` and the available `cause`, `reason` or `error`.

| Code | Meaning |
| --- | --- |
| `pass` | The receipt verdict is `pass` |
| `test_failed` | A counted JUnit report recorded a test failure or error, or the host suite failed a case |
| `check_failed` | The receipt records a failure without identifying a failed test case |
| `all_tests_skipped` | JUnit was the count source, every reported test skipped, and verification failed the test-count requirement |
| `no_usable_tests` | Verification required test evidence but no usable count or no executed test was found |
| `infra_error` | A host, VM or resource error prevented completion, the VM hit its TTL, or cleanup could not confirm destruction |
| `canceled` | GitHub canceled the job or the worker stopped before completion, and the VM was confirmed destroyed |
| `unfinished` | Verification ended without a result or a recorded cause that explains the outcome |
| `refused`, `idle` | The receipt verdict is `refused` or `idle`, respectively |

For a failed Actions job, the receipt recognizes the test-count requirement when GitHub reports
`Run coldbox require-tests` as the sole failed step. Use the workflow's own log for details if step evidence is missing.
The GitHub App check stays red for an incomplete result and shows the receipt reason, executed count, and skipped count.

### `tests`

Coldbox looks for JUnit XML after the job, inside the VM, before it is destroyed. `coldbox checks` asks pytest for a
report through `PYTEST_ADDOPTS` (keeping any value you set), unless the project already sets `--junitxml` in the command,
`PYTEST_ADDOPTS`, `pytest.ini`, `pyproject.toml`, `setup.cfg` or `tox.ini`; then the project's own report is used.
Other tools are picked up when the job writes a JUnit file anywhere under the runner's `_work` folder, or under the repo
in local mode. Only reports written while the job ran count: files older than the job's start and committed files (tracked
by git, or in local mode copied in from `git archive`, even if the job rewrote them) are skipped, as are folders starting with `.`, `node_modules` and `vendor`.

| Field | Meaning |
| --- | --- |
| `files` | Report paths inside the VM |
| `total`, `passed`, `failed`, `error`, `skipped` | Counts |
| `truncated` | `true` when there were more than 5000 results; `results` holds the first 5000 |
| `elapsed_test_seconds` | Sum of JUnit testcase `time` for executed cases before truncation. `null` when any executed case lacks a valid time or there are no executed cases |
| `results` | `{suite, classname, name, status, seconds}` per test case. `status` is `passed`, `failed`, `error` or `skipped`; `seconds` is null if the report had no usable time |

### `tests_ran`

A runner or local job passes only when a test ran. For a runner's `verify` job, Coldbox uses the count in the checkout's
`.cold/verify-receipt.json` when it was written during the job, is untracked, and contains a valid `run.tests_ran`,
including zero. The `tests` field still records
discovered JUnit XML and may include fixture reports. Otherwise, Coldbox takes the count from JUnit (`total` minus
`skipped`), even when they hold no tests. The built-in `go test` and `cargo test` checks write a report with one unnamed
test case per passing test. Without reports, it reads `.cold/verify-result.json`, which ColdVerify writes into the checkout, from any
checkout under the same folders, written during the job and not committed (in local mode, not copied in from
`git archive`), and adds up their `tests_ran`. If one of those files can't be read or has no valid `tests_ran`, the
count is left out and the job reads `incomplete` with `reason` set. For a runner job with none of those reports,
Coldbox reads the GitHub Actions job log after teardown and counts executed (non-skipped) tests from `unittest`,
`pytest`, `cargo test` and `go test` summaries with `tests_source` set to `job-log`. A job with none of these, such as
a lint-only or deploy job, reads `incomplete` with `reason` set, never `pass`. Ad-hoc commands
(`coldbox run -- COMMAND`) keep their exit-code verdict; their count covers only reports under `$COLDBOX_JOB_DIR/junit`.

An interrupted job retains the test reports it produced before teardown. An empty JUnit report keeps its file path
and zero counts in `tests`. When an incomplete receipt has no test report, `tests_missing_reason` is
`no_test_report` after a successful scan or `test_count_unavailable` when the count could not be collected.

In `coldbox stats --json`, `requests.missing_observations` lists each accepted request without a definitive outcome.
Each entry identifies the direct submission or GitHub repository and job ID. It includes the recorded verdict and
reason, or a null verdict with the missing assignment or terminal observation.

### `suite_observation`

Run `coldbox run --offline --trusted-suite FILE PATH` on the host to check the committed checkout at `PATH` against
a host-selected black-box suite. Use a suite from a trusted source outside the candidate's control. `FILE` is a
nonempty JSON array of cases, for example `[{"argv":["python3","app.py","21"],"stdout":"42\n"}]`.
Each case runs in the job VM from the repository directory. The host checks that the command exits zero and its
stdout matches the UTF-8 bytes in the suite, including newlines. It stops at the first failure. Only the command and
arguments cross into the VM. The suite file, expected output, and host receipt stay on the host.

The receipt has `tests_source: "host-suite"` and a `suite_observation` object with `schema:
"coldbox.suite-observation/v1"`, the source `commit` and `tree` used for `git archive`, the `suite_sha256` of the
manifest, `tests_ran` for completed comparisons (including a failed case), and `result` (`PASS`, `FAIL` or
`INCOMPLETE`). A consumer must read the receipt from trusted host storage and verify the revision and suite hash.
Require a positive `tests_ran` and a top-level `verdict` of `pass`. The top-level verdict also requires successful
job teardown.

For a PR comparison, point `PATH` at the PR head and use a host-selected JSON object.
Set `base` to the full SHA of the PR base commit in that checkout's Git history.
In `proof` and `suite`, list one case per named test. Each case has a unique `name`, an `argv` array, and optional
`stdout` to compare against. Both arrays must contain at least one case.

```json
{
  "base": "0123456789abcdef0123456789abcdef01234567",
  "proof": [{"name": "cli::test_exit", "argv": ["python3", "-m", "unittest", "tests.test_cli.ExitTest.test_exit"]}],
  "suite": [{"name": "cli::test_help", "argv": ["python3", "cli.py", "--help"]}]
}
```

Coldbox runs the same commands on base and head, with a fresh VM for each revision. If the PR adds a proof
test, supply its source in the host manifest's `argv`, for example as a `python3 -c` argument. A command that
references a file present only on head will record a missing-file exit on base.
Coldbox continues after a test failure. It starts the head VM after the base VM finishes and is destroyed.
The final head receipt adds `base_commit`, `base_tree`, `base_receipt` (the base receipt ID), and `tests`
to `suite_observation`. Each completed test records `name`, `group`, `base_exit`, and `head_exit`.
Read both receipts and require that both VMs were destroyed. The command exits with the head verdict after a
complete comparison, or 125 if either VM run is incomplete. The PR comparison writes host receipts.

To publish the observation as `coldbox verify (host suite)`, run
`coldbox run --offline --trusted-suite FILE --verify-job ID PATH` with the single-revision JSON array on a host
with the GitHub App installed.
Use the checkout at the PR head and the numeric ID of its successful Coldbox `verify` job. Coldbox verifies that
the job belongs to the current attempt of the same-repository PR workflow and that the head matches the base CI
configuration. The host records the selected repository, job, attempt, revision, and suite digest in the receipt's
`verify_job` object. Before publishing a success, Coldbox confirms the workflow still has the same attempt and head.
The App check reports the receipt ID and observation. The legacy `coldbox verify` check still uses the in-guest
ColdVerify path. To require host-observed evidence, name `coldbox verify (host suite)` and select the Coldbox App.

## Flaky tests

Coldbox records; it does not judge. A receipt reader can find flakes by grouping runner receipts on
`github.repo`, `github.sha`, `github.workflow`, `job_name`. Within a group, `github.run_id` plus `github.run_attempt`
tells the attempts apart, and `job_result` is each attempt's conclusion. A test is identified by `suite` + `classname` +
`name` (each is `""` when the report leaves it out). A test whose `tests.results[].status` differs across attempts at
the same `sha` is a flake candidate. If one receipt has two results with the same identity, that identity is ambiguous:
skip it rather than compare it. A receipt with `tests.truncated: true` is left out of the comparison: its missing results
could hold a duplicate or a different status.

Local receipts have no `github` object. Group them on `repo` + `sha`.

The optional `github.started_at` field records the UTC time the job-started hook ran.
After a runner job finishes, the host matches its runner name within the exact run attempt on GitHub.
When available, `github.job_id` identifies that job, `github.queued_at` is its GitHub `created_at`,
`github.runner_started_at` is its GitHub `started_at`, and `github.queue_wait_seconds` is the difference
between those timestamps in seconds. `github.completed_at` is its GitHub `completed_at`, and `github.steps` lists
each workflow step as `{name, conclusion, seconds}`, with `seconds` null for a step that never ran. These measure the individual job, including matrix jobs and
reruns, rather than the workflow run or VM lifetime. Missing timing or an API failure leaves these
fields absent and logs `queue wait unavailable`; it does not change the job verdict.
