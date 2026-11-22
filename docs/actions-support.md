# GitHub Actions support in Coldbox v1

Coldbox runs jobs assigned to its self-hosted runners. Each job gets a fresh Ubuntu 26.04 VM on an
x86-64 Linux host and runs with GitHub's standard Actions runner. GitHub evaluates the workflow and
assigns jobs. Coldbox provisions the VM, checks admission at job start, and saves the receipt.

## Supported workflows

| Feature | v1 support |
| --- | --- |
| Runner labels | Use `runs-on: [self-hosted, coldbox]` for the default size. Replace `coldbox` with `coldbox-small` or `coldbox-large` for a sized runner. All labels on a job must match the same runner. The default runner advertises `self-hosted`, `linux`, `x64`, `coldbox`, and `dependabot`. Sized runners advertise `self-hosted`, `linux`, `x64`, and their size label. |
| Repository access | Private repositories are the default. On an explicitly enabled public repository, only actors with write access can run jobs. Coldbox refuses fork pull requests on any repository, including writer reruns. |
| Verified triggers | `push`, same-repository `pull_request`, and `workflow_dispatch`. |
| Shell steps and Docker | Bash `run:` steps and `docker run` in a step have passed on Coldbox. |
| Workflow structure | Multiple jobs, `strategy.matrix`, `if:` conditions, and `timeout-minutes` have passed. |
| Secrets | Repository secrets passed through a step's `env:` have worked. |
| Verified actions | `actions/checkout@v4` and `@v5`, `actions/setup-node@v4` and `@v5`, and `actions/upload-artifact@v4`. |
| Other operating systems and architectures | Windows, macOS, and ARM jobs are outside v1 support. |

See the [job receipt and workflow evidence](compatibility.md) for the verified rows.

## Admission and compatibility limits

- The job-start hook treats `schedule`, `repository_dispatch`, `release`, `create`, and `delete` as
  same-repository events. These triggers are outside the verified subset. The hook checks head and
  base repositories when a payload contains a pull request or the event is `workflow_run`. A private
  `dynamic` event can pass the hook when its ref does not start with `refs/pull/`. These code paths
  are admission rules. The verified trigger list above records successful workflows.
- Service containers, job containers, reusable workflows, composite actions, `actions/cache`,
  `actions/download-artifact`, and other action versions are outside the verified subset.
- Ubuntu supplies Python, Go, and Rust. Compatibility with `actions/setup-python`,
  `actions/setup-go`, and `dtolnay/rust-toolchain` is unverified. The guest inherits the host's CPU
  features, which can limit toolchain binaries. See [what is in the VM](../README.md#what-is-in-the-vm).
