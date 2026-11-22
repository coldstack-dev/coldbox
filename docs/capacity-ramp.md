# Measure a concurrency limit

Run `scripts/capacity-ramp.py` on the operations host. Install the reviewed CLI on each
participating host using the normal idle-only rollout before starting. Copy the script
to the same absolute project path on each host. Use SSH names and passwordless sudo.
Existing caps are observed and preserved. There is no calendar-based start hold.

For example, with project paths and SSH aliases chosen for your environment:

```sh
python3 scripts/capacity-ramp.py run --host primary --host standby-a --host standby-b \
  --remote-script /srv/project/scripts/capacity-ramp.py --output .smoke/ramp-results
```

The command only observes existing CI. It never creates, renews, removes,
or applies a cap. It records naturally observed busy levels and their exposure in
the host report, with raw CPU/load, OOM, receipt outcomes, job creation/start/end
timestamps, completions/hour and queue/duration P90 in the measurement directory.
It does not generate demand or claim unobserved levels were tested.

The gate stops observation on sustained CPU pressure, memory pressure, low
available memory, OOM kills, or receipt errors. Existing jobs continue untouched.
High CPU utilization or load alone does not stop observation.

For separately authorized trials, add `--trials --limits` followed by at least two
increasing positive integer limits. These are test points, not a recommended cap.
The measured hardware ceiling bounds the requested points. Each point runs against
existing CI demand for the requested window. The tool never generates jobs.

Trial acquisition also requires all three load averages below half the available
CPU threads, or the positive threshold supplied with `--max-load`.
Each trial cap is acquired or changed only after an idle check under the host's
admission lock, including active job logs. The driver waits up to 15 minutes for
idle between limits. It never restarts or kills workers. Existing foreign caps
are preserved. Temporary leases last the window plus six hours of follow-through
and five minutes of margin. They are never renewed while busy. Every measurement
sample must still show the owned, unexpired cap at the intended limit.
Cleanup removes the owned cap only while idle. If busy or unreachable, the report
records deferred cleanup and the temporary lease expires naturally. The tool never
installs a permanent cap. `--apply` and the old `cap` action remain unsupported.

Throughput is the number of successful completions whose finish timestamp lies in
[window_start, window_end], divided by window seconds. Both boundaries are included,
regardless of when a job started. Failures, skipped or cancelled jobs, and jobs
finishing outside the window do not contribute to throughput or its qualification count.

A qualified trial needs at least 30 of those successful completions, 1800 seconds,
at least three at-limit samples, saturation for at least 90% of the window,
unchanged CLI and no safety violation. Adjacent samples establish saturation only
when both are at the limit and at most 60 seconds apart. Compared points must have
the same repository/workflow/job/labels mix, with each share within five percentage
points. Compare each qualified point with the preceding qualified point using
successful completions divided by window seconds.
A gain greater than 5% advances; a smaller gain, tie or decline stops. Increased
failure rate or duration P90 above 120% of the preceding point also stops.
Queue wait remains reported but no longer decides throughput improvement.
After a qualified stopping point, `measured_cap` names the previous measured
limit. Unsafe or incomplete evidence selects no cap. If every requested limit
improves, the result is INCOMPLETE because no plateau was observed. Test further
limits only through another explicitly authorized run. The passive report's
busy-level groupings cannot substitute for distinct-limit trials.

Jobs still running at the cutoff are followed to completion for up to six hours.
Missing completions make the measurement unsafe. Jobs started within the window
remain in duration, failure and job-mix diagnostics even when they finish later;
their post-window successes never increase throughput or qualify a trial. Jobs
started earlier that finish within the window are included in those diagnostics too.
Jobs are matched by receipt VM ID to GitHub runner name and run attempt. Queue wait is
GitHub created-to-started time; duration is started-to-completed time. Paginated App
responses are reused per run attempt when all returned jobs are complete.
A response is fetched again only if the matching receipt job is absent or unfinished. Coverage is the receipt cohort, so jobs
without receipts remain outside the denominator. Missing matches mark the measurement unsafe.
The historical baseline is p90 420 seconds over 883 jobs. Workload changes and other
host work mean this is an observational comparison, not an isolated benchmark.

Raw samples every 30 seconds include CPU counters, CPU/memory PSI, MemAvailable,
OOM count and busy/warm VM counts. Summaries include p50/p90 wait and duration,
failure counts, coverage, CLI digest and API request counts. Stop on an OOM, an
error receipt or an incomplete receipt whose worker is no longer active or whose cleanup is finalized, memory full PSI avg10 >=1%, memory below the host reserve,
or CPU PSI some avg10 >=50% in two consecutive samples. No limit is inferred from an unobserved level.
Preserve all results under the project. Reusing an output directory with reports is
refused to protect evidence.

## Check admission budgets and request completion

### Verify the build after an idle-only swap

Before a maintenance pause, confirm that the other CI hosts are admitting jobs.
On the target host, pass the approved candidate, its full SHA-256 digest, and
a new backup path to the rollout script.

```sh
sudo python3 scripts/rollout-coldbox.py "$target_host" "$candidate" "$sha256" "$backup"
```

The script holds `autoscale.lock` for at most 30 minutes, including the wait
for zero active jobs. It saves the installed executable, swaps at zero active
jobs, and queues `systemctl restart --no-block` before releasing the lock.
Old supervisors may need the lock to finish shutting down. The script verifies
the original supervisor set, each current systemd invocation's loaded build,
and the executable digest after releasing the lock. On verification failure,
it reacquires the lock and waits for zero active jobs before restoring the
digest-verified pre-swap executable. The rollback follows the same restart
and release order. Run the fixture after the script exits successfully.

On the primary, the first check timed out with all 39 units still stopping.
All 39 loaded the target build after release. That attempt started on
`ce650e4ed5d9` and used the separately staged `92fcde5047c4` for rollback.
The live retry used the corrected lock order in a one-off rollout script.
It held the lock for 12 minutes 26 seconds while jobs drained, then verified
all 39 units about 13 seconds after release.

### Start the fleet audit

On each CI host, run the audit against the installed build after the idle-only
rollout. Start the audits together for a common observation period. Run as the
Coldbox service user or with sudo when required to read local state.

```sh
python3 scripts/capacity-ramp.py audit --seconds 86400 --output .smoke/admission-audit
```

Each sample records the charged CPU threads, committed RAM, committed disk, and
their limits. CPU uses the scheduler's charge per VM, including warm VMs. RAM
uses worker sizes and other VM commitments, with the host share reserved. Disk
uses pool usage plus pending root disk reservations, with a tenth of the pool
reserved. A ready marker or a receipt showing a completed boot clears the
reservation for that worker for the rest of its lifetime. A final receipt for
an absent, destroyed VM also clears the charge while the worker exits. The audit
checks available memory against the host floor. `summary.json` contains the peaks with their corresponding limits,
the smallest memory margin, and the build and supervisor builds on that host.

Use `samples.jsonl` to inspect the observed values and sampling times. An
over-admission is a sample with at least one commitment above its limit.
`capacity_passed` describes the host's sampled capacity and supervisor build.
For the fleet's request outcomes, use `coldbox stats --json --since 24h` with one
`--receipts` directory per host. Handle the report's [exit codes](../README.md#json-output)
before using the counts.
Keep each host's `queues` directory alongside its `receipts` directory so stats
can match jobs seen in one queue to receipts from a peer. Check that the combined
`requests.terminal` equals `requests.accepted` and that all four host summaries
show the same build, zero over-admissions, and zero floor violations.
Check the reported builder VM peak. The CPU check uses the CI reserve even
when an ad-hoc builder VM shares the host, so an overage with builders present
needs the sample for context. A failed capacity probe records an incomplete
audit and preserves all earlier samples.

The four-host observation window started on **2026-11-20 at 20:53:14.394 UTC**.
The primary reported 39 supervisors on `c628b8a1dfcf`. Each of the three
overflow hosts reported 12 on the same build. The primary fixture passed one
test and its VM was destroyed.

At the 21:46 UTC readback, all four audits were running. The 415 samples,
including burst load, showed zero over-admissions and zero memory floor
violations. All 60 observed requests had reached a terminal state. Evaluate
the full window after it closes on November 21 at 20:53 UTC. The audit files
are under `.smoke/p7b-c628/window/audit` in each host's project checkout.

Each overflow host passed a burst with three Actions sizes and four FIFO
direct submissions. Across 416 samples, all 21 requests ended terminal and
the audit found zero over-admissions. Actions and direct jobs overlapped on
each overflow host.

On the primary, 12 base VMs charged 36 of 40 CPU threads, leaving room for
one more 3-thread VM. The fixture jobs ran serially in that slot. All 14
requests across two bursts ended terminal, direct admissions stayed FIFO,
and the audit found zero over-admissions. Peak committed CPU was 39 of 40
threads. P7 uses the test-host mixed burst below and the four-host window.

### Check a mixed burst on the test host

1. Prepare a private fixture workflow with small, default, and large jobs.
   Use the `coldbox-small`, `coldbox`, and `coldbox-large` labels.
2. Start `audit --burst cohort.json --seconds 720 --output .smoke/mixed-burst`
   before triggering the workflow and running `coldbox api submit` for a pinned
   revision. Keep enough requests waiting to observe at least two direct IDs
   in the queue and an Actions runner overlapping a direct worker.
3. After the jobs settle, write `cohort.json` atomically with the fixture `repo`
   and `sha`, the GitHub run's `jobs` array, and a `direct` array of submission
   IDs in acceptance order.
   The audit stops when it sees the file or reaches the time limit.

The audit matches Actions job IDs to final, destroyed VM receipts. It checks
the observed reservation for each runner against its expected size. It checks
direct admission order using the timestamps on host worker records written
under the admission lock. A missing outcome, missing size, missing overlap,
budget violation, unlisted request for the fixture revision, or unproven FIFO
order makes the audit exit nonzero.

The test fixture gave its direct workers a temporary `COLDBOX_RESERVE` file
that held them in the queue until the audit saw their order. The fixture then
restored the host's normal reserve in that file.

On November 20, 2026, build `92fcde5047c4` finished all seven requests and
destroyed all seven VMs. Four direct submissions were admitted in FIFO order
and overlapped the three Actions sizes. Across 133 samples, CPU peaked at 9 of
30 charged threads, RAM at 73.6 of 159.9 GiB, and disk at 53.2 of 86.8 GiB.
Available memory stayed at least 66.7 GiB above the floor. The audit recorded
zero over-admissions and zero non-terminal requests.

A second burst ran candidate `5f0d5cc0cb10` alongside older CI supervisors.
All seven fixture requests finished and the direct admissions followed FIFO
order. The audit found 15 samples above the disk budget while runner VMs were
booting. The supervisors that started those background runners predated disk
reservation accounting. Verify the loaded supervisor builds before treating
a burst as proof for a single build.
