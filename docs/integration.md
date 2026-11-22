# Integrating with Coldbox

This is the interface other tools rely on: agents' `vmrun`/`offload` wrappers, ColdVerify's workflow template, ColdPass,
ColdRelease and ColdFix. It describes what Coldbox ships today. Coldbox is the one execution layer. Each product keeps
its own standalone default and picks Coldbox as a backend. The CLI below is the interface; `coldbox api` is its
submit-and-poll form and returns the same receipts.

Coldbox exposes the pinned-revision submit and poll path of ColdStack's `cold.exec/v0` under `coldbox api`.
Call `coldbox api version` to check the interface before submitting. The v1 receipt records these runs with `mode: "local"`.

`tests/test_coldbox.py` (`IntegrationContract`) checks this page against the code: every call under
[Calls other tools make](#calls-other-tools-make) must parse and reach its handler, the exit codes must match
[Exit codes](#exit-codes), and the runner labels in the template's `runs-on` must be Coldbox runner labels.
`tests/test_integration_consumers.py` checks the real consumer files against this page: `vmrun` must make exactly the
listed calls, `offload` none, both must read the host files named under [Guest markers](#guest-markers), and the
template must route like [`examples/coldbox.yml`](../examples/coldbox.yml) onto the listed labels and make exactly the
listed `coldbox` calls. Point it at them with
`COLDBOX_CONTRACT_VMRUN=…/vmrun` (`offload` is read from the same folder) and
`COLDBOX_CONTRACT_TEMPLATE=…/coldstack/ci/templates/verify-coldbox.yml`. In Coldbox CI the template defaults to the
guest copy at `/opt/coldstack/ci/templates/verify-coldbox.yml`, so its tests always run there. The `vmrun`/`offload`
tests skip when their variable is unset. Coldbox CI leaves it unset until those files reach the guest.
When a consumer changes a call, update this page with it.

## Supported versions

The [JSON pointer](integration.json) identifies this contract as `coldbox.integration/v1`.
It names the CLI API interface `cold.exec/v0`, the terminal evidence format `coldbox.evidence/v1`, and the receipt
format `coldbox.receipt/v1`. Check the backend and interface against `coldbox api version` and the evidence and receipt
schemas against `coldbox api evidence ID`.

## Minimum permissions

| Integration | Access needed |
| --- | --- |
| Standalone install and local `coldbox run PATH` | Administrator access to install. To run, use the Coldbox host account with Incus access and write access to Coldbox state and receipts, plus read access to the committed checkout. A local run needs no GitHub App or Coldlocker account. |
| `vmrun` / `offload` and the ColdPass smoke replay | Access to invoke Coldbox as its VM operator, locally or over the configured SSH path. The wrappers also need read access to the host files listed under [Guest markers](#guest-markers). The replay needs read access to the checker and input archive. Guest commands run as the guest's `runner` user. |
| ColdVerify via `coldbox api` | Access to invoke the CLI on the execution host, locally or over SSH. Submit needs the host operator access used by `run`. Status, receipt and evidence need read access to receipts. When submission state exists, these commands also need write access to Coldbox state and the receipts directory for the host lock and queue updates. A private checkout needs the host's repository-scoped App token with Contents read. |
| ColdStack verify workflow used by ColdVerify and other checks | Access to an eligible `self-hosted, coldbox` runner and read access to the repository contents for checkout. The host App separately needs the [runner and check permissions](../README.md#requirements) for registration and publishing. |
| ColdFix receipt reader | Read access to the receipt files it consumes. |

## Commands

| Command | Does |
| --- | --- |
| `coldbox run --repo OWNER/REPO --sha SHA [--profile NAME] [--ttl S] [--cpus N] [--memory SIZE] [--receipts DIR]` | Clones that exact commit inside a new job VM and runs `coldbox checks` there. A private repo is cloned with a token scoped to that repo with read-only contents. Other repos are cloned anonymously. Blocks until the VM is deleted or its deletion failed. A failed deletion leaves `vm.destroyed: false` in the receipt and exits 125; the reaper deletes the VM at its TTL. |
| `coldbox run [PATH] [--offline] [--ttl S] [--cpus N] [--memory SIZE] [--receipts DIR]` | Runs `coldbox checks` on the committed `HEAD` of the local repo at PATH (default `.`) in a new job VM. |
| `coldbox run [--offline] [--ttl S] [--cpus N] [--memory SIZE] [--receipts DIR] -- CMD…` | Runs CMD in a new job VM with no stdin. Its stdout and stderr, merged, go to the caller's stdout and to the console log. |
| `coldbox api version` | Prints `{"interface": "cold.exec/v0", "backend": "coldbox"}`. |
| `coldbox api submit --repo OWNER/REPO --sha SHA [--profile NAME]` | Queues a background run and prints `{"id": "cbx-…", "state": "queued", "receipt": PATH}` as one JSON line. The host admits API submissions in acceptance order as capacity opens. The queue holds up to 64 waiting requests per host. A full queue returns exit 75 and JSON with `state: refused` and `cause: queue_full`. Once admitted, the worker writes the same receipt as `run`. Its stderr goes to `<id>.submit.log` next to the receipt. |
| `coldbox api status ID` | Prints `{"id", "receipt", "state"}` as one JSON line. State is `queued` while awaiting admission, `running` once a receipt exists, `done` with a final receipt and `verdict`, or `failed` with an `error` when the worker ends without a receipt. After 30 minutes waiting, status records `cause: queue_timeout`. If a queued worker exits first, including across a host restart, status records `cause: worker_interrupted`. Either failure frees a queue place. Submit again to retry. |
| `coldbox api receipt ID` | Once `done`, prints the receipt JSON and exits as `run` would. Before that it prints nothing on stdout and exits 75; for `failed` it exits 125. |
| `coldbox api evidence ID` | Once `done`, prints one JSON line with `schema: "coldbox.evidence/v1"`, `interface: "cold.exec/v0"`, and the complete `coldbox.receipt/v1` object under `receipt`. It uses the same exit codes as `api receipt`. |
| `coldbox vm up [--ttl S] [--offline \| --network NAME] [--here] [--image INSTANCE/SNAPSHOT] [--cpus N] [--memory SIZE] [--disk SIZE]` | Starts a disposable VM and prints its name, `cbx-…`, alone on stdout. When this host is full it starts it on a peer CI host instead, unless `--here`. |
| `coldbox vm exec NAME -- CMD…` | Runs CMD in that VM as the `runner` user, in its home. Stdin, stdout and stderr pass through. Follows the VM to the peer that started it. |
| `coldbox vm down NAME` | Deletes the VM. |
| `coldbox vm list` | Lists this host's disposable VMs: name, status, minutes left, tab-separated. |
| `coldbox checks [PATH]` | Inside a VM: runs the repo's checks (see [Checks](#checks)). |

SIGTERM or SIGHUP during a job produces a `stopped` receipt. During cleanup,
the worker logs the signal and finishes the receipt. After an abrupt worker exit
with an unfinished receipt, `coldbox reap` finalizes the receipt.

Input rules, checked before any VM starts:

- `--repo` is `OWNER/REPO`. `--sha` is the full 40-character lowercase commit; a short or uppercase SHA is rejected, never
  resolved. They go together, cannot be combined with PATH or `-- CMD`, and cannot be `--offline`.
- `--ttl` is the VM's lifetime in seconds: 3600 by default for `run`, 7200 for `vm up`. The reaper deletes the VM when it
  runs out, and `vm exec` refuses an expired VM.
- `--memory` can only shrink a VM below the `coldbox` profile's limit.
- `vm up --disk` sets the root disk, up to the `coldbox` profile's root size. `--image` and `--network` take only
  names the host owner listed in `/etc/coldbox/vm-allow` (`image INSTANCE/SNAPSHOT` or `network NAME`, one per
  line) that exist on the host; anything else exits 1 before admission.
- `NAME` is the `cbx-…` value `vm up` printed. `ID` is the `id` that `api submit` printed; an ID with no submission or
  receipt on this host exits 1. `api` always uses `/var/lib/coldbox/receipts`, where `coldbox reap` finalizes receipts
  whose worker died.

Messages go to stderr as `coldbox: …`. `run` prints `coldbox: outcome [REASON_CODE]` before its final
`coldbox: VERDICT receipt PATH` line.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | `run`, `api receipt`, and `api evidence`: the receipt's verdict is `pass`. `api version`, `api submit`, `api status`: done. `vm up`, `vm down`, `vm list`: done. `checks`: every check passed. |
| 1 | `run`, `api receipt`, and `api evidence`: the verdict is `fail`. `checks`: a check failed, or nothing ran. Any command: refused with a `coldbox: …` message: the host is at its VM ceiling (`run` first waits up to 30 minutes for room), conflicting arguments, an unknown or expired VM name, a VM that could not be deleted. A refusal writes no receipt. |
| 2 | Malformed arguments, or a malformed `coldbox.toml` or host `checks.toml` read on the calling machine (`checks`, `detect`, `run PATH`). Fix the call; do not retry. `run --repo` reads `coldbox.toml` inside the VM, so there a bad one makes the receipt `incomplete` and `run` exits 125. |
| 75 | `api submit`: the queue is full. `api receipt` and `api evidence`: the submission is still `queued` or `running`. |
| 125 | `run`, `api receipt`, and `api evidence`: any other verdict (`incomplete`, `refused`, `canceled`, `idle`). `api receipt` and `api evidence`: a `failed` submission. Any command: Incus or the host failed. Record a harness failure, not a product failure. |
| CMD's | `run -- CMD` and `vm exec`: CMD's own exit code. `run -- CMD` returns 125 instead when the VM was not confirmed destroyed or CMD never finished. |

A `run` that exits 1 failed if it wrote a receipt and was refused if it did not. `run -- CMD` and `vm exec` pass through a
CMD that itself exits 125: for `run -- CMD` the receipt then has `exit: 125`, no `error` and `vm.destroyed: true`; for
`vm exec` there is no receipt, so a consumer that must tell them apart wraps CMD.

## Receipt

Each `run` writes one `coldbox.receipt/v1` JSON file, `/var/lib/coldbox/receipts/<id>.json` (or `--receipts DIR`), and its
console log next to it. [`receipt.md`](receipt.md) defines every field and the stability rule: within v1, fields are only
added; anything that would break a reader ships as `coldbox.receipt/v2`. Readers reject a `schema` they do not know and
ignore fields they do not know.

`run --repo/--sha` and `run PATH` write `mode: "local"` with `repo` and `sha`; `run -- CMD` writes `mode: "adhoc"`; a GitHub
Actions job writes `mode: "runner"`. Only `verdict: "pass"` passed.

Newly finalized receipts include the optional [`reason_code`](receipt.md#reason_code). Read it in `api receipt ID` or in
the Coldbox App check's summary and JSON evidence. Use `verdict` to decide success and `reason_code` to explain the
outcome when present. The App check can also report independent GitHub, revision or ColdVerify failures.

Clients that store terminal evidence can save the record from `api evidence ID` as one JSON object. Check `schema` and
`interface` before reading it, and match `receipt.id`, `receipt.repo`, and `receipt.sha` against the submission. Log and
artifact paths inside `receipt` refer to the Coldbox host.

## Guest markers

Inside a job VM:

| Marker | Present | Holds |
| --- | --- | --- |
| `/usr/local/bin/coldbox` | every job VM | the host's own `coldbox`, so `coldbox checks` is the host's version, not the repo's |
| `COLDBOX_JOB_DIR=/var/lib/coldbox/job` | every job command's environment | `checks.json` from `coldbox checks`; files left in `artifacts/` are copied next to the receipt |
| `/etc/coldbox/host` | runner jobs | the CI host that admitted the job, one line |
| `/etc/coldbox/admission.json` | runner jobs | the `coldbox.admission/v1` record, also in the receipt's `admission` |
| `/etc/coldbox/checks.toml` | when the host sets one | the host's default on/off switches for checks |

ColdVerify reads `/etc/coldbox/host` (`ci/verify_context.py`, `coldbox_guest`); without it, the machine is not a Coldbox
runner job. Disposable VMs from `vm up` are not job VMs and carry none of these markers.

On the host, `vmrun` reads `COLDBOX_NAME_PREFIX` from `/etc/coldbox/incus.env` (the Incus project, default `coldbox`) to
tag its VM, and `offload` reads `/etc/coldbox/reserve`, the CPU threads kept for Coldbox.

## Checks

A repo's checks are what `coldbox detect` finds for its languages, changed by its `coldbox.toml`:

```toml
[checks]          # true/false per check
verify = true
[commands]        # replace a detected command, or add a check of your own
verify = "coldverify --ci"
```

Coldbox has no built-in command for any other tool. A tool that runs as a check, such as ColdVerify, is a `[commands]`
entry in the repo's `coldbox.toml`, and its binary ships to job VMs as guest files under `/etc/coldbox/guest`. A repo
that has `.fleet/verify.sh` but no `verify` command, and has not switched `verify` off, fails `coldbox checks` with exit 2
and a message naming both fixes; its verify check never stops silently.

On `pull_request` events, `coldbox checks` runs the union of the base commit's checks and the pull request's, base
commands first, so a pull request cannot switch off or replace a check for itself (see the README's Checks section).

### Direct verification profiles

Declare profiles in the requested commit's `coldbox.toml` using enabled check names.

```toml
[profiles]
quick = ["setup", "test"]
```

Pass `--profile quick` to `run --repo … --sha …` or `api submit`, locally or over SSH. Coldbox runs the selected
checks in their normal order. Include `setup` when the selected checks depend on it. Omit `--profile` to run the full plan.
A passing receipt still requires at least one recorded test.

Coldbox resolves the profile after checkout in the job VM. An unknown profile or one that names a disabled or missing
check refuses the whole request before any check runs. After teardown, `api status` gives `state: done`, `verdict: refused`,
and a `cause` of `unknown_profile` or `unauthorized_profile`. Both status and the receipt carry the requested `profile` and
`sha` once a receipt exists. A refused receipt exits 125.

## Calls other tools make

These are the calls each consumer makes. Changing how Coldbox parses or answers any of them breaks that
consumer. Update the consumer in the same wave.

`vmrun`, an agent wrapper script:

```sh
coldbox vm up --ttl TTL --offline
coldbox vm up --ttl TTL
coldbox vm exec NAME -- bash -c CMD
coldbox vm down NAME
coldbox vm list
```

`offload`, a wrapper around `vmrun`, runs `vmrun` and reads `/etc/coldbox/reserve`; it calls `coldbox` only
through `vmrun`.

ColdStack's verify template (coldstack `ci/templates/verify-coldbox.yml`):

```yaml
runs-on: [self-hosted, coldbox]
run: coldbox checks
```

Agents, by hand (no product calls `coldbox run` from code yet):

```sh
coldbox run --repo OWNER/REPO --sha SHA
coldbox run --repo OWNER/REPO --sha SHA --ttl TTL --receipts DIR
coldbox run PATH
coldbox run --offline -- CMD
```

Agents and ColdVerify, through the API:

```sh
coldbox api submit --repo OWNER/REPO --sha SHA
coldbox api status ID
coldbox api receipt ID
```

Versioned API clients use these calls with the same submit and status commands.

```sh
coldbox api version
coldbox api evidence ID
```

## Replay a ColdPass evaluation

Use `scripts/coldpass-smoke.py` from this checkout on a host that admits disposable VMs.
Prepare a tar archive from a small ColdPass workspace that can run offline. Include the product files,
`.coldpass/probe.json`, and `.coldpass/claims.json` at the archive root. Use the checker from a
trusted, pinned ColdPass checkout. Keep probe commands and working directories relative to the workspace.

```sh
tar -C WORKSPACE -cf evaluation.tar .
python3 scripts/coldpass-smoke.py CHECKER/cold_check.py evaluation.tar > evaluation-receipt.json
```

The script runs the checker and product in one fresh local VM using `vm up --offline --here` and
`vm exec`. It records both input SHA-256 values, the VM name, the checker output, and whether
`vm down` confirmed deletion. Success requires both a working checker result and confirmed deletion.
Keep both inputs and the receipt to reproduce the evaluation. The VM's TTL covers abrupt termination.

ColdPass's queue continues to use its configured backend. To use Coldbox for queue jobs, ColdPass
must add an optional backend that retains the name printed by `vm up`, uses it for guest operations,
and records the result of `vm down` in evaluation evidence.

## Not yet

- `api` has no network listener: callers on another machine run it over SSH.
- At the VM ceiling `vm up` with `--here`, or once no peer could start the VM, exits 1 with no receipt instead of waiting or a distinct code. `run` waits up to 30 minutes, retrying every 30 seconds in no fixed order, then does the same.
- `run --repo` clones private repos only once the GitHub App has Contents: read.
