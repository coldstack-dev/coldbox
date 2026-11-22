# Fresh install trials

Run each command from a Coldbox checkout on the test-VM host with Incus. Each
invocation provisions its own guest and network, then removes them.

```sh
VPS_SMOKE_GUEST=ubuntu VPS_SMOKE_MEMORY=8GiB scripts/vps-smoke.sh </dev/null
VPS_SMOKE_GUEST=debian VPS_SMOKE_MEMORY=8GiB scripts/vps-smoke.sh </dev/null
VPS_SMOKE_GUEST=no-kvm scripts/vps-smoke.sh </dev/null
```

Ubuntu 24.04 uses ZFS and Debian 13 uses directory storage. Each successful
install runs a local test fixture, checks its receipt for at least one executed
test and confirmed VM deletion, then checks isolation, a failing fixture, and
uninstall. The no-KVM control runs in an Ubuntu container without `/dev/kvm`.
It requires the installer to exit 1 with its nested virtualization diagnostic
before writing Coldbox configuration.

Launching the script counts as one manual step. Elapsed time includes cleanup.
Time to first proof runs from guest launch to the passing receipt check. The
last column is the cumulative share of supported trials that passed without
manual troubleshooting. The no-KVM control is outside that denominator.
Each attempt appends a row to this tracked file. Detailed logs stay in
the checkout's `.smoke/` directory.

The five trials below ran on source `2699f40`, based on `8aeefac`. The
branch later rebased onto `8d87df9`, which includes changes to test counting.

| Started (UTC) | Source | Guest | RAM | Outcome | Manual steps | Elapsed (s) | First proof (s) | Tests | `vm.destroyed` | App check | Diagnostic | Without troubleshooting |
| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- | --- |
| 2026-11-20T17:33:04Z | 2699f40 | ubuntu | 8GiB | pass | 1 | 1505 | 1132 | 1 | true | not run in guest: key stays on host by rule | - | 1/1 (100%) |
| 2026-11-20T17:58:09Z | 2699f40 | debian | 8GiB | pass | 1 | 1928 | 1556 | 1 | true | not run in guest: key stays on host by rule | - | 2/2 (100%) |
| 2026-11-20T18:30:17Z | 2699f40 | ubuntu | 8GiB | pass | 1 | 1501 | 1110 | 1 | true | not run in guest: key stays on host by rule | - | 3/3 (100%) |
| 2026-11-20T18:55:19Z | 2699f40 | debian | 8GiB | pass | 1 | 1363 | 1089 | 1 | true | not run in guest: key stays on host by rule | - | 4/4 (100%) |
| 2026-11-20T19:18:03Z | 2699f40 | no-kvm | 8GiB | refused | 1 | 48 | - | - | - | not run in guest: key stays on host by rule | /dev/kvm missing; Coldbox needs nested virtualization from its provider (installer exit 1) | 4/4 (100%) |
