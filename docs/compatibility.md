# GitHub Actions compatibility

Coldbox runs a GitHub Actions job in a fresh Ubuntu 26.04 VM with the standard `actions-runner`. This table says which
actions and workflow features real jobs on Coldbox have actually passed with. A row is **Verified** only when a job on a
Coldbox runner (`[self-hosted, coldbox]`) finished with that step or feature succeeding, as shown by the job's Coldbox
receipt and GitHub's step log. Everything else is **Untested**: it may work, but nothing proves it yet.

The original survey covered about 1,900 passed jobs through 2026-11-11. The matrix evidence below is from 2026-11-20.

## Actions

| Action | Status | Evidence |
| --- | --- | --- |
| `actions/checkout@v4`, `@v5` | Verified | Nearly every passed job, including the run and post-run steps. |
| `actions/setup-node@v4`, `@v5` | Verified | Passed jobs that then ran `npm` builds and tests (2026-11-06 to 2026-11-11). |
| `actions/upload-artifact@v4` | Verified | A passed deploy job uploaded its evidence (2026-11-11). |
| `actions/setup-python`, `actions/setup-go`, `dtolnay/rust-toolchain` | Untested | The VM already has Python, Go and Rust from Ubuntu's packages. setup-python's Ubuntu 26.04 builds need x86-64-v3 and fail on x86-64-v2 hosts; see the [README](../README.md#what-is-in-the-vm). |
| `actions/cache`, `actions/download-artifact` | Untested | |
| Any other action | Untested | |

## Workflow features

| Feature | Status | Evidence |
| --- | --- | --- |
| `run:` steps with bash | Verified | Every passed job. |
| `docker run` in a step | Verified | A smoke workflow ran `docker run --rm hello-world` (2026-11-10). |
| `push`, `pull_request`, `workflow_dispatch` triggers | Verified | Passed jobs from each event. |
| Several jobs in one workflow | Verified | A two-job smoke workflow passed (2026-11-10). |
| Repository secrets in `env:` | Verified | A passed deploy step used them (2026-11-11). |
| `if:` conditions, `timeout-minutes` | Verified | Used by passed jobs above. |
| Pull requests from forks | Always refused | No opt-in changes this, on private or public repos. An outside pull request gets no CI on your hosts; a writer reviews it by hand. See the [safety model](../README.md#safety-model-read-this-first). |
| `schedule` and other triggers | Untested | |
| `strategy.matrix` | Verified | A push expanded one job into three Python versions on 2026-11-20. See [matrix evidence](#matrix-evidence). |
| `services:` and `container:` (job containers) | Untested | |
| Reusable workflows, composite actions | Untested | |
| Windows, macOS or ARM jobs | Not supported | Job VMs are Ubuntu 26.04 on x86_64 Linux hosts only. |

### Matrix evidence

On 2026-11-20, a push expanded a Python version matrix into three jobs on `[self-hosted, coldbox]`.
Each job used `uv python install` for the selected version and ran 602 tests.

| Python | Actions job ID and receipt `github.job_id` | Receipt `job_result` | Receipt `vm.destroyed_at` (UTC) |
| --- | --- | --- | --- |
| 3.11 | `136210774855` | `Succeeded` | 09:50:28 |
| 3.13 | `136210774823` | `Succeeded` | 09:50:29 |
| 3.14 | `136210774849` | `Succeeded` | 09:50:07 |

The Actions step logs and VM receipts match run `44712991513`, attempt `1`, and the job IDs above.
All three receipts record `vm.destroyed: true`. Their test verdict is `incomplete` because the workflow supplied
neither JUnit XML nor a ColdVerify test report. The test counts come from the Actions logs.

### Fork refusal evidence

The [private queue fixture](../tests/test_coldbox.py) presents Coldbox with a queued job labeled
`[self-hosted, coldbox]` from a fork pull request rerun by a writer. Coldbox reports zero eligible jobs and logs the
fork refusal. The same job from a same-repository pull request is eligible. The fixture exercises queue admission.

To add a row, run a workflow that uses the action or feature on a Coldbox runner. Match the successful Actions steps
to the VM receipt by run, attempt, and job ID, and record the date here.
