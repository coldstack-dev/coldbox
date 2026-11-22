# Interrupt a job and verify recovery

Run the matrix from the dedicated nested-test host. Supply an existing local
Incus storage pool and an unused private subnet for the inner installation.
The host needs KVM, Git, and Python 3.

```sh
scripts/restart-matrix.sh INCUS_POOL INNER_SUBNET
```

The script installs the checkout's committed `HEAD` in a new Ubuntu guest
with its own network. It starts the installed Coldbox runner service with
automatic restart disabled for the fixture. It waits for a job in its own VM
to write a fixture artifact and announce that it is running. The matrix then
runs each fault twice.

1. Send SIGKILL to the Coldbox worker.
2. Send SIGKILL to the Coldbox supervisor.
3. Force-restart the disposable test guest from Incus and verify that its boot
   ID changes.

The first two cases invoke the installed reaper service. The reboot case
waits for the installed reaper timer. A second reaper run checks for duplicate
delivery. Read `worker_interrupted` in the finalized receipt's `cause` field.
Each trial requires zero job VMs, submission records, queue entries, and
artifact folders before the outer guest is deleted.

The installation is keyless. The App key stays outside the guest. A local
`gh` fixture supplies the runner assignment and records the completed check
payload from Coldbox. The matrix counts exactly one terminal check result from
that file per receipt. GitHub App delivery remains outside this measurement.

Find the generated table, receipts, and recorded check payloads under
`.smoke/restart-matrix-*/`. The script removes its disposable guest and
network on exit and leaves the evidence directory.

## Live trial results

On 2026-11-20, the matrix recovered **6/6** jobs on a fresh Ubuntu 24.04
install with directory-backed Incus storage. All six receipts finalized as
`incomplete` with cause `worker_interrupted` and `vm.destroyed: true`.

| Fault | Recovered | Terminal results per receipt | Job VMs left | Submissions left | Artifacts left |
| --- | ---: | ---: | ---: | ---: | ---: |
| Worker SIGKILL | 2/2 | 1 | 0 | 0 | 0 |
| Supervisor SIGKILL | 2/2 | 1 | 0 | 0 | 0 |
| Hard reboot of test guest | 2/2 | 1 | 0 | 0 | 0 |
