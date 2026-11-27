# Coldbox

CI on a computer you already own. Every job gets a fresh VM cloned from a warm snapshot, runs the checks you
switched on, writes a JSON receipt, and the VM is destroyed. A self-hosted GitHub Actions runner feeds it jobs.
It costs $0 per minute, against GitHub's $0.006 per minute.

Needs Linux with /dev/kvm. No macOS or Windows.

## What you get

```json
{
  "verdict": "pass",
  "tests_ran": 70,
  "vm": {"destroyed": true}
}
```

## Safety model (read this first)

A self-hosted runner executes whatever a workflow tells it to. Coldbox limits what that code can reach:

- **One job per VM, then gone.** Each runner is registered with a single-use [JIT config](https://docs.github.com/en/rest/actions/self-hosted-runners#create-configuration-for-a-just-in-time-runner-for-a-repository),
  takes exactly one job, and its VM is force-deleted afterwards. Coldbox checks that the VM is actually gone
  and records `vm.destroyed` in the receipt. A reaper timer (`coldbox-reap.timer`, every 60s and 60s after boot) deletes any job VM past its TTL, and at once any job VM
  whose worker is gone, such as one left STOPPED by a host reboot.
- **Private repos only by default.** `coldbox register` and every runner loop refuse a repo that is not private,
  or an org whose default runner group allows public repos. GitHub
  [warns](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners/about-self-hosted-runners#self-hosted-runner-security)
  that on public repos a fork's pull request can run code on your machine.
- **A public repo you own runs only with your opt-in, and only its writers' own code runs.**
  `coldbox register OWNER/REPO --allow-public` (the flag, not an environment variable) serves one public repo and
  records `COLDBOX_ALLOW_PUBLIC=1` in that repo's service. Jobs triggered by a user with write access
  then run at once, except fork pull requests (next item). Coldbox refuses the repo, and logs the fix, unless its
  Actions setting requires approval for all outside contributors, a second lock in case a fork check is ever missed:
  `gh api -X PUT repos/OWNER/REPO/actions/permissions/fork-pr-contributor-approval -f approval_policy=all_external_contributors`.
  Coldbox also checks each run itself, before it starts a VM for it and again in the job-started hook: a job runs only
  if it is not from a fork and its triggering user has write access. An outside user's `issue_comment`,
  `pull_request_target` or `workflow_run` job is never run. Each runner remembers the
  writer list it started with. If the setting is relaxed or write access changes, every autoscale pass (each 30 seconds)
  deregisters this host's idle runners whose list is out of date, so their workers start over with fresh rules; running
  jobs keep going. To serve an organization-owned public repo, configure its App with the expected sole member.
  At registration, during autoscaling, and when a waiting runner receives a job, Coldbox checks that the organization
  has exactly that member, the repo has no outside collaborators, and the approval policy still holds. The VM hook
  waits for the host's decision before workflow steps can start. An unreadable policy or membership list refuses the job.
- **Fork pull requests never run, on any repo.** No setting, opt-in or approval on GitHub changes this. The autoscaler
  starts no VM for a run whose head repo differs from the repo, and the job-started hook inside the VM fails any job
  that reaches one anyway before any step runs, when the pull request's (`pull_request`, `pull_request_target`) or
  `workflow_run`'s head repo differs from the base repo, also when the fork was deleted. A re-run keeps its original
  event, so re-running a fork's run is refused too. The job log, the runner service's journal (`not scaling for run
  …`) and the job's `job.json` (`refused`) all say `fork pull request: fork code never runs on Coldbox; a writer reviews
  it by hand`. So an outside pull request gets no CI on your hosts: a writer reads it and decides by hand. If a required
  check is skipped rather than failed for forks, GitHub shows it green, so never merge a fork's pull request on the
  check alone. On a private repo the hook also fails closed: events that can't show where the code came from, such as
  `merge_group` or `issue_comment`, are refused too. Only
  `push`, `workflow_dispatch`, `schedule`, `repository_dispatch`, `release`, `create` and `delete` run without a check.
  GitHub's `dynamic` jobs (Dependabot graph jobs, CodeQL default setup) run on a private repo unless the ref is
  `refs/pull/...` or a pull request in the event comes from another repo. The hook has no GitHub credential, so it
  can't ask GitHub where a `dynamic` job's code came from. A `dynamic` job that ran fork code with neither of those
  signs would run. This is accepted only while every repo Coldbox serves is private with no forks; recheck it if a
  served repo with `dynamic` workflows becomes public or a fork of a private one appears.
- **The example workflow keeps forks off your machine too.** [examples/coldbox.yml](examples/coldbox.yml) runs its job
  on Coldbox only and skips a fork's pull request, so fork code never reaches a runner and no job uses a GitHub-hosted
  one. It also stops a job after 60 minutes and leaves no token in `.git/config`. A new push to a pull request cancels
  that pull request's older run, so its VM is freed for the run that counts. Pushes and manual runs are never canceled.
  Copy it byte for byte: tests pin these guards.
- **No host mounts, no host secrets.** Before booting, Coldbox refuses any VM that has a device other than its own root disk
  and its NIC on the isolated bridge, or any `raw.*` config. Your `gh` token stays on the host. The only credential that enters
  the VM is the single-use runner config, passed on stdin. Jobs get GitHub's own per-job `GITHUB_TOKEN`, as on hosted runners.
- **Isolated network.** VMs sit on `coldboxbr0` (10.204.0.0/24, NAT, no IPv6) with ACL `coldbox-isolated`. They may reach the
  public internet only. LAN (RFC 1918), Tailscale/CGNAT (100.64/10), link-local/metadata, loopback and the host's own addresses are rejected,
  and so is traffic between VMs (port isolation). The host's addresses are re-read before every job VM starts and by the
  reaper every minute, so a new host address is blocked within a minute. `coldbox run --offline` gives a VM no NIC at all.
- **Proven live, weekly.** `coldbox selftest isolation` (run weekly by `coldbox-selftest.timer`) boots a job VM that tries
  to connect to every host address (bridge, LAN, tailnet and IPv6) while the host listens on them, and to public IPv6,
  which must fail as network unreachable. It must reach the public internet as a control. Other LAN and tailnet
  machines fall in the same rejected ranges but are not probed: with nothing known to listen there, a failed
  connection would prove nothing. It also checks that `nproc`
  equals the profile's `limits.cpu`, memory is at most `limits.memory`, the root block device is no larger than the
  profile's root disk, and writing one GiB more than that fails with no space left. A busy host retries it up to three
  times, 30 minutes apart. The results and the verdict land in the receipt's `isolation` field.
- **What it does not protect against:** a malicious workflow in a repo you trust still runs with that job's `GITHUB_TOKEN` and
  can use the internet. Treat write access to a registered repo like shell access to a disposable VM on your network edge.

## Requirements

- An x86_64 Linux machine running Ubuntu 24.04+ or Debian 13+, with hardware virtualization on (`/dev/kvm`).
  A VPS or cloud VM needs nested virtualization from its provider: [docs/vps.md](docs/vps.md) lists which providers
  offer it and what size of plan to buy. With no ZFS kernel module (Debian 13), install uses `dir` storage.
- Incus 6.0.6 or newer. The distro Incus (6.0.0 on Ubuntu 24.04, 6.0.4 on Debian 13, 6.0.5 on Ubuntu 26.04) crashes under
  parallel jobs ([lxc/incus#2865](https://github.com/lxc/incus/issues/2865)), so Coldbox needs the
  [Zabbly](https://github.com/zabbly/incus) Incus 6.0 LTS. `install.sh` asks before it adds the Zabbly apt repo and
  installs Incus from it; `--install-incus` says yes up front. Without that yes it refuses, adds no repository and
  prints the commands to do it yourself.
- 8 GB of RAM and about 80 GB of disk. `install.sh` refuses less RAM than the host's share (a reserve of 2 GiB, or an eighth of RAM above
  16 GiB, plus 1.5 GiB for the host's own idle use) plus one job VM (5.5 GiB with the default sizing) and less free space under `/var/lib` than
  the storage pool needs (`COLDBOX_POOL_SIZE` for ZFS, one `--disk` for `dir`), and stops with the fix. 8 GB runs one
  3 GiB job VM at a time.

macOS, Windows and ARM hosts are not supported: Coldbox needs Incus with KVM on a Linux host. An old laptop or desktop
with Linux on it is the target.

No telemetry. Network: host setup downloads OS packages and (with opt-in) the Zabbly Incus repo key/packages; image builds download the VM base image and toolchain/runner packages; runner registration and CI use the GitHub API; running jobs can reach public IPv4 destinations unless run with `--offline`.

### WSL2

WSL2 on a Windows 11 PC gave no `/dev/kvm`, so `install.sh` stops at its first check and `coldbox status` fails its
hardware virtualization check. Install Linux on a spare machine, or use a VPS with nested virtualization
([docs/vps.md](docs/vps.md)).

## Quickstart

### Run checks locally

Install Coldbox on a supported host.

```sh
git clone https://github.com/coldstack-dev/coldbox.git && cd coldbox && sudo ./install.sh
```

Open your project checkout. Run `coldbox detect .` to preview the suggested checks.
To create `coldbox.toml`, run this block from that checkout. It reports an existing
config and keeps it in place. If detection fails, the block exits non-zero.

```sh
(
  if [ -e coldbox.toml ] || [ -L coldbox.toml ]; then
    printf '%s\n' 'coldbox.toml already exists. Keeping it.'
    exit 0
  fi
  config=$(coldbox detect .) || exit "$?"
  staged=$(mktemp .coldbox.toml.XXXXXX) || exit "$?"
  trap 'rm -f -- "$staged"' EXIT
  printf '%s\n' "$config" > "$staged" && ln -T -- "$staged" coldbox.toml
)
```

Review the checks and commit any changes to `coldbox.toml` before running. Coldbox
runs the committed `HEAD`. This local run works without a GitHub account or App.

```sh
coldbox run .
```

Open the JSON receipt at the path printed by the run. Check the executed-test
count (`tests_ran`), `verdict`, and `vm.destroyed`. See the
[recorded one-test runs](docs/install-trials.md) for examples.

### Set up GitHub Actions

For GitHub CI, use an account with admin rights on the repo or organization you
register.

```sh
coldbox setup-app                                                                     # prints the GitHub App link
sudo coldbox setup-app --app-id 123456 --key ~/Downloads/coldbox.private-key.pem       # then install the App on the repo
coldbox detect --workflow ~/src/myrepo > coldbox.yml &&                                 # refuses if a workflow already
  [ ! -e ~/src/myrepo/.github/workflows/coldbox.yml ] &&                                # requires executed tests; never
  install -Dm644 coldbox.yml ~/src/myrepo/.github/workflows/coldbox.yml                 # overwrites; commit, push
coldbox register you/myrepo                                                            # 1 slot, or 0 (on demand) if none fits
```

Coldbox authenticates as a GitHub App you own, with only the permissions it needs: Administration read/write (to
add and remove runners), Actions read (to see queued jobs), Checks read/write (to post `coldbox verify`), Contents
read (to compare trees and clone private repos) and Metadata read. An App for org-wide runners also gets the
organization self-hosted runners permission. `coldbox setup-app` prints a link to GitHub's App form with exactly
those permissions filled in. After you create the App, `sudo coldbox setup-app --app-id <id> --key <pem>` checks the
key against GitHub and refuses an App with any other permission. It then puts the key at `/etc/coldbox/github-app.pem`
(mode 600, owned by you) and the App ID in `/etc/coldbox/github-app.env`. If setup stops between writes, rerun the same command with the same account, App ID and key.
It reports the missing path and completes the matching pair. Install the App on the repos you register.

For public repos in a single-member organization, run `coldbox setup-app --org ORG --sole-member LOGIN`.
Use the same two options when installing the downloaded key. This App adds organization Members read to the
repository permissions above. Install it on the selected repositories. Coldbox stores its files alongside the
default App as `github-app-ORG.pem` and `github-app-ORG.env`, using the lowercase organization login.

Every GitHub API call, including `gh` subprocesses, then uses a short-lived installation token. Tokens stay in process
memory and refresh five minutes before expiry. Targeted calls select the installation by owner; host-wide lookups and
`status` use the first available installation. No key or token is written to logs. A configured App that fails stops
the request without falling back.

Without the App, Coldbox refuses to call GitHub. `coldbox register OWNER/REPO --allow-personal-login` opts that runner
into your personal `gh auth login`, which can do anything your account can; other commands take
`COLDBOX_ALLOW_PERSONAL_LOGIN=1`. `coldbox status` names the active auth and warns on a personal login.

The JWT follows [GitHub's RS256 requirements](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-json-web-token-jwt-for-a-github-app)
and uses the host's `openssl` executable with Python's standard library.

`coldbox status` shows whether everything is healthy (with the fix for anything that is not), the registered repos,
running job VMs and the latest receipts.

Prefer an agent to do it? [prompts/0-setup.md](prompts/0-setup.md) is one copy-paste prompt for Claude Code, Codex and the
like that does all of the above for a repo. The other prompts in [prompts/](prompts) do one step each.

### Try the CLI without Incus

`coldbox --help` and the unit tests need only Python 3.11+ (the tests also PyYAML) and git, so they run on any Linux machine without Incus:

```sh
./coldbox --help
python3 -m venv .venv && . .venv/bin/activate
python3 -m pip install pyyaml
python3 -m unittest discover -s tests -q
```

### Existing workflows

Jobs other than `coldbox checks` run too. Change `runs-on: ubuntu-latest` to `runs-on: [self-hosted, coldbox]` and the
job runs in a fresh Coldbox VM. The [v1 Actions support matrix](docs/actions-support.md) lists supported labels,
verified workflows and actions, and the limits of job admission.

## Checks

`coldbox detect PATH` suggests checks from the files in a repo. `coldbox detect --workflow PATH` generates a workflow
that runs `coldbox checks` and then `coldbox require-tests`. The second step fails the workflow if no fresh test report
shows an executed test. Skipped tests do not count. For custom test commands, write a JUnit XML report under the
checkout. See [test evidence in receipts](docs/receipt.md#tests_ran) for the report formats.
To update an existing generated workflow, add `- run: coldbox require-tests` after the `coldbox checks` step.

`coldbox checks` on its own reports the exit status of the configured commands. It can pass a lint-only run.
Every detected check runs unless `coldbox.toml` or the host default switches it off. `go test` and `cargo test` exit 0
with zero tests, so their Coldbox checks require at least one passing test.
On a `pull_request` event, `coldbox checks` also plans from the base commit (`pull_request.base.sha` in the event file)
and runs both: every base check with its base command, then any check or command the pull request adds. A pull request
can add a check, but a check it switches off, renames or changes still runs its base command, with a notice that the
change takes effect after merge. The commands run in the pull request's tree, so a script they call is still the pull
request's own. A base commit missing from the checkout (use `fetch-depth: 0`) or an unreadable event fails with exit 2.
These are plain shell commands, not plugins:

| Repo has | setup | lint | test |
| --- | --- | --- | --- |
| Python (`pyproject.toml`, `setup.py`, `requirements.txt`, `*.py`) | `python3 -m pip install -r requirements.txt` in a virtual environment if the file exists | `python3 -m compileall`. If configured, `ruff check .` and `mypy` run in the virtual environment. Mypy also installs the project if `pyproject.toml` has `[project]`. | `python3 -m pytest -q` if a `test_*.py` or `*_test.py` file appears to define, import, inherit, or generate tests |
| Node (`package.json`) | `npm ci`, `pnpm` or `yarn install --frozen-lockfile` by lockfile (pnpm and yarn via `npx`, at the `packageManager` version; Yarn 2+ uses `--immutable`), else `npm install` | `npm run lint`, else `eslint .` if configured or a dependency; `tsc --build` for `tsconfig.json` with project references, otherwise `tsc --noEmit` if the config exists | `npm test` when a test script or its lifecycle hooks can run tests, else `vitest run` or `jest` if configured or a dependency |
| Go (`go.mod`) | | `gofmt -l` + `go vet ./...` | `go test -v ./...` if `*_test.go` exists; fails if no test passed |
| Rust (`Cargo.toml`) | | `cargo fmt --check` + `cargo clippy -D warnings` | `cargo test` if a `.rs` file has a `#[test]`; fails if no test passed |
| Shell (`*.sh`) | | `shellcheck` | |

When detected Python setup installs requirements, later commands use its virtual environment, including commands
under `[commands]`.

TypeScript build mode follows project references and may write the outputs configured by those projects.

Coldbox detects no `verify` command. A repo that verifies through another tool names it under `[commands]`, e.g.
`verify = "coldverify --ci"`. `verify = true` without a command is an error, and so is a `.fleet/verify.sh` with no
`verify` command unless `verify` is switched off: `coldbox checks` exits 2 rather than quietly dropping the check. A tool the job VM needs on its PATH
ships as guest files: a checkout at `/etc/coldbox/guest/opt/coldverify` is copied into the next job VM, which links
`/usr/local/bin/coldverify` to it unless the guest files already ship their own. [`docs/integration.md`](docs/integration.md)
is the interface other tools rely on.

```toml
[checks]          # true/false per check; unknown sections, non-booleans or an empty command are errors
verify = false
[commands]        # replace a detected command, or add a check of your own
test = "python3 -m pytest -q tests"
typecheck = "npx tsc --noEmit"
[vm]              # coldbox run only; CI jobs pick a size by label (see VM size)
size = "large"    # small, default or large
```

The host default comes from `install.sh`. In a terminal it shows an on/off menu of setup, lint, test and verify, all on to
start. Without a terminal, pass `--checks lint,test` (or `none`, or set `COLDBOX_CHECKS`); with neither, every detected
check stays on and an earlier choice is kept. The choice is saved as a `[checks]` table in `/etc/coldbox/checks.toml`,
`coldbox detect` and `coldbox checks` follow it, and each job VM gets a copy. A repo's own `coldbox.toml` still wins:
`lint = true` there runs lint even when the host default has it off.

Order: setup, lint, test, verify, then your own checks. The first failed check skips the rest (recorded `not_run`), and a repo with nothing to run fails.
In a job on a Coldbox runner, each failed check also becomes an error annotation, and one more annotation names up to ten
failed tests from the JUnit report Coldbox asks pytest for (not a `--junitxml` the project sets itself). The reason then
shows on the check run and the pull request, not only in the log.

### What is in the VM

Ubuntu 26.04 with git, Docker (with compose and buildx), Python 3.14 + pytest, Node 22 + npm, Go 1.26, Rust 1.93 +
clippy/rustfmt, shellcheck, gh, restic, rclone, rsync, bubblewrap, build-essential, curl, jq, passwordless sudo and the newest
actions-runner. Background apt updates are masked, so a job's `apt-get install` never waits on the dpkg lock; the weekly
base rebuild brings in security updates.
These toolchains are Ubuntu's own packages, so they are older than the ones on GitHub's hosted image. For a current Node, use
`actions/setup-node`, which works unchanged. Other setup actions (`actions/setup-python`, `actions/setup-go`,
`dtolnay/rust-toolchain`) are untested on Coldbox; see [docs/compatibility.md](docs/compatibility.md).

Job VMs pass the host CPU through, so a guest gets exactly the host's x86-64 level. Older Xeons such as Ivy Bridge stop at
x86-64-v2 (no AVX2), and binaries built for x86-64-v3 fail there with `CPU ISA level is lower than required`.
`actions/setup-python`'s Ubuntu 26.04 CPython builds are among them. On such a host, install Python with
`astral-sh/setup-uv` instead. Check a host with `/lib64/ld-linux-x86-64.so.2 --help | grep x86-64-v`.

`coldbox-refresh.timer` rebuilds the base VM every week with updated packages and the newest actions-runner, so jobs do not
start by updating the runner. A failed rebuild, such as a package mirror timing out, runs again after 30 minutes, up to 3
runs in all, and jobs keep using the old base until one passes. Run `sudo coldbox build-base` to do it now. Jobs that are
running keep their VM.

Before a new base replaces the old one, the rebuild clones a VM from it and runs a bundled Git and Python test as the
runner user. The test must pass and the clone must be deleted before promotion. The refresh log and
`/var/lib/coldbox/base-smoke.json` record the candidate snapshot, test count, and teardown result. If the smoke check
fails, the rebuild keeps the old base and `coldbox status` FAILs with the reason until a rebuild passes.

Each successful rebuild keeps one previous base as `base-prev`, including its `warm` snapshot. The next rebuild replaces
that previous base, so allow disk space for two retained bases and the new base during a build. To restore the previous
image, run `sudo coldbox build-base --rollback`. It takes the same lock as a rebuild, exchanges `base-dev` and `base-prev`,
and builds nothing. An interrupted rollback resumes on retry, including its matching package inventories.
After completion, repeated rollback commands do nothing until another rebuild starts. It refuses if no previous base exists.

Rebuild output lists added, removed and changed package versions and the actions-runner version change before PASS.
The first package inventory prints only a count. Read scheduled refresh output with `journalctl -u coldbox-refresh`.
Inventories live at `/var/lib/coldbox/base-packages.txt` and `/var/lib/coldbox/base-packages.prev.txt`; rollback exchanges
them with the bases. These inventories show changes but do not test whether the image works for a particular repo.

To give jobs `actions/setup-node`, `setup-python` or `setup-go` versions without a download, list them in
`/etc/coldbox/toolcache`, one `node|python|go VERSION` per line, such as `node 24` or `node 26.8.1`. Each rebuild
resolves a line to the newest stable build in that action's `versions-manifest.json` and installs it into
`/opt/hostedtoolcache`, where the runner's `RUNNER_TOOL_CACHE` points, so a matching `setup-*` step logs `Found in cache`.
The rebuild prints each resolved version and sha256, and the inventory lists it as `toolcache/node/24 24.x.y`, so a
prefix moving to a new patch shows in the rebuild's change list. The sha256 comes from GitHub's release API at build
time: it proves the download is what GitHub serves now, not that anyone reviewed that build. Python's own installer also
upgrades pip from PyPI during the build. A download, checksum or install failure fails the rebuild and keeps the current
base. Without the file, or with an empty one, nothing is pre-populated. A version not listed still downloads as before.
On a host below x86-64-v3, a `python` line fails the rebuild, because Python's installer runs the interpreter.

To add your own files to every job VM, put them under `/etc/coldbox/guest/` on the host at the path they should have in the
VM. Each newly started job VM receives them without a base rebuild. For example, `/etc/coldbox/guest/opt/tools/run` becomes `/opt/tools/run`. They are
copied owned by root with file modes kept. A managed clone skips the copy when the host overlay and the guest paths
still match the stamp recorded during the base build. Older bases and explicit `--image` VMs receive the full overlay.
The startup copy adds and replaces files.
Runner VMs already waiting for a job keep the overlay copied when they started.
A directory the overlay created takes the overlay's current mode at each copy. A directory the VM already had, such as
`/etc` or `/opt`, keeps its own mode and stays a symlink if it is one. A base built before this change does not know
which directories the overlay created, so mode changes reach job VMs after the next `sudo coldbox build-base`. To remove
files deleted from the overlay from future job VMs, run `sudo coldbox build-base`.

### VM size

`install.sh` sizes job VMs from the host: half its CPU threads (2 to 8) and half its RAM (2 to 16 GiB), with a 40 GiB disk.
To choose the size yourself, re-run it with flags, for example `sudo ./install.sh --cpus 4 --memory 8GiB --disk 60GiB`
(or set `COLDBOX_CPUS`, `COLDBOX_MEMORY`, `COLDBOX_DISK`). `coldbox register --slots N` runs up to N
jobs at once for a repo. It refuses a slot total, counting every registered slot, that would leave the host ceiling
(below) no room for one `large` VM beside all slots at their full `limits.memory`, and the error names the most
`--slots` that fit. Without `--slots`, it registers 1 slot, or 0 for a repo when no slot fits. On a 247 GiB host with 12 GiB VMs that allows 15 slots. `coldbox status` FAILs a host whose
registered slots break that bound. So a job no idle slot can take, such as a sized job or one for a repo with
`--slots 0`, waits at most until one running job finishes, never forever. It does not stop per-repo slots from
splitting capacity: an idle slot of a quiet repo cannot take a busy repo's job.
A slot's VM boots before its job arrives, so the job starts within seconds, and a new one boots as soon as the job ends.
Every job VM, idle or busy, is admitted by one rule (see the host ceiling below): an idle slot VM uses about 1 GiB of
RAM and no CPU, so it is charged at its real use in available memory, and at its full `limits.memory` only in the
ceiling, which bounds what all job VMs could use if every one took a job at once. A VM still booting reserves its full
`limits.memory` from available memory until it is ready.
`--slots 0` (repos only) makes a repo burst only: no VM waits for it, it holds none of that capacity while idle, and its jobs
run on bursts (below), so each job waits for the next 30-second poll, a VM boot, and longer while the host is at its ceiling or GitHub is slow. Register most repos with `--slots 0`
when many share one host. `coldbox status` lists them as `burst only`.

A job can ask for a smaller or larger VM. `small` halves the profile's vCPUs and memory. `large` doubles them, up to
the host's CPU threads and the most memory one VM may take there. In a workflow, replace the `coldbox` label with
`coldbox-small` or `coldbox-large`, for example `runs-on: [self-hosted, coldbox-large]`. Slots stay at the profile size, so
sized jobs always run on bursts. A sized
runner has no `coldbox` label, so `[self-hosted, coldbox]` jobs never land on it, but GitHub may still give it a job that
names only labels it has, such as `[self-hosted]`. For `coldbox run`, put
`[vm]` `size = "small"` (or `"default"`, `"large"`) in `coldbox.toml`; `--cpus` and `--memory` still override it.
Admission and bursts reserve each VM's own `limits.memory`, so small and large VMs share the host's room by size.

### Incus names

By default Coldbox creates Incus project and storage pool `coldbox`, bridge `coldboxbr0` on 10.204.0.0/24 and ACL
`coldbox-isolated`. If a host already uses any of those, install under other names and a free subnet, for example
`sudo ./install.sh --name-prefix cbx --subnet 10.205.0.1/24` (project and pool `cbx`, bridge `cbxbr0`, ACL `cbx-isolated`;
or set `COLDBOX_NAME_PREFIX`, `COLDBOX_SUBNET`). Before it installs anything, `install.sh` refuses a subnet that overlaps a
route the host already has (see `ip -4 route`) on any interface other than Coldbox's own bridge, and names `--subnet`. A
VPS whose provider network is in 10.204.0.0/24 would otherwise lose that range, and with it SSH. `install.sh` records them in `/etc/coldbox/incus.env`; the CLI, the
reaper and `uninstall.sh` read them from there, and re-runs keep them. To change them, run `uninstall.sh` first.

## Commands

To compare a host's build with a commit, run `git show <ref>:coldbox | sha256sum | cut -c1-12`.

| Command | What it does |
| --- | --- |
| `sudo ./install.sh [--install-incus] [--cpus N] [--memory SIZE] [--disk SIZE] [--checks LIST] [--name-prefix NAME] [--subnet CIDR]` | Asks which checks are on by default (or takes `--checks`), then installs Incus (adding the Zabbly repo when you agree, or with `--install-incus`), ZFS loop pool `coldbox` (plain directories with `COLDBOX_STORAGE=dir` or when the kernel has no ZFS module), bridge, ACL, profile sized from the host, base VM `base-dev` with snapshot `warm`, reaper and refresh timers and the CLI. Safe to re-run; `COLDBOX_REBUILD=1` rebuilds the base. |
| `coldbox --version` | Prints the version and the first 12 hex characters of the running file's SHA-256, for example `coldbox 0.2.0 (build 0123456789ab)`. Releases are tagged `vX.Y.Z`. |
| `coldbox status` | Prints the same version and build first, then health checks, registered repos with their GitHub visibility, job VMs and recent receipts. For each active runner supervisor, it reports the startup build as `current` or `stale` relative to the installed CLI. A supervisor without matching startup evidence reports `unknown`. A registered repo that is now public without `--allow-public`, or whose visibility cannot be read, is a FAIL. Exit 1 if a check fails. |
| `coldbox status --hosts` | Reports each configured host's installed build, verdict, orphan job VMs, last receipt and jobs in the last 24 h. Reports loaded supervisor builds and fleet health findings below the table. Exit 1 unless every host passes. Without `/etc/coldbox/ci-hosts.json`, it checks only this host. |
| `coldbox stats [--since 1h] [--receipts DIR]... [--json]` | Shows observed request outcomes, settled VM outcomes, queue wait, job throughput, timing coverage and host saturation in the window. Repeat `--receipts` to combine host data; a receipt found in more than one directory counts once, by its `id`. `--json` adds `savings`: `vm_minutes` for every VM in the window, and `billable_minutes` (each GitHub Actions runner job's wall time rounded up to a minute, as GitHub bills) times GitHub's Linux 2-core rate as `estimated_usd`. Runner jobs without a measured wall time (older receipts, or a failed GitHub lookup) are left out and counted in `unmeasured_jobs`. |
| `coldbox pool status [--json]` | Shows the warm pool of standby VMs for CI jobs: the configured and ready counts, the stamp a standby must carry, standby VMs by state and the oldest one's age, pending VM teardowns, and hit, miss and reap counts. It reads only. Exits 0 when the pool is healthy or off, and 1 when a pool VM is past its expiry or the pool size or stamp cannot be read. |
| `coldbox shell --repo PATH [--ttl S]` | Opens an interactive shell in an offline throwaway VM with the checkout's committed HEAD in `~/repo`, and destroys the VM when the shell exits. It writes a receipt with `mode: "shell"`. |
| `coldbox run PATH [--offline] [--receipts DIR]` | Copies the committed HEAD (`git archive`) into a fresh VM, runs `coldbox checks`, writes a receipt, destroys the VM. Exit 0 pass, 1 fail, 125 incomplete. `--receipts DIR` (either form of `run`) writes the receipt to DIR instead of `/var/lib/coldbox/receipts`, so stress and test runs stay out of the production receipts; the job still counts against the host ceiling. At the ceiling `run` waits up to 30 minutes for room, then exits 1. |
| `coldbox run --repo OWNER/REPO --sha SHA` | Like `coldbox run PATH`, but the VM clones that exact commit from GitHub. With GitHub App auth, Coldbox mints a token limited to that one repo with Contents: read and hands it to the clone over stdin; if there is no App, or it is not installed on the repo with Contents: read, Coldbox says so and clones anonymously, so only public repos work. Private clones stay off until the App is granted Contents: read. It uses the default VM size, not `[vm] size`. |
| `coldbox run [--ttl S] [--offline] -- CMD [ARG ...]` | Runs one command as `runner` in `/home/runner` of a fresh VM from `base-dev/warm`, streams its output (stderr merged into stdout), writes a receipt and destroys the VM; the reaper deletes it at `--ttl` (default 1h) if that fails. Exits with the command's exit code, or 125 if the VM did not boot, the TTL ran out or the VM could not be destroyed. |
| `coldbox selftest isolation [--ttl S] [--receipts DIR]` | Boots one job VM and proves the isolation and limits described under the safety model, writes a receipt with an `isolation` field and destroys the VM. Exits 0 for `pass`, 1 for `fail` (each problem is printed), 125 otherwise. `coldbox-selftest.timer` runs it weekly. |
| `coldbox vm up [--ttl S] [--offline \| --network NAME] [--image INSTANCE/SNAPSHOT] [--cpus N] [--memory SIZE] [--disk SIZE]` | Starts a disposable VM from `base-dev/warm` on the same isolated network and prints its name. `--image` starts from another snapshot in the Coldbox project, and `--network` puts `eth0` on another Incus network (still port-isolated), but only ones the host owner lists in `/etc/coldbox/vm-allow` as `image INSTANCE/SNAPSHOT` or `network NAME`. An image VM gets the `coldbox` profile's limits unless `--cpus`/`--memory` shrink them, and its `runner` must have passwordless sudo. `--memory` and `--disk` go up to the profile's sizes. The reaper deletes it after `--ttl` (default 2h). It refuses on the primary named in `/etc/coldbox/ci-hosts.json`. When this host is past its `/etc/coldbox/vm-max` limit or its job VM ceiling, it starts the VM on the other overflow host with the most room (never the primary) and prints that name; `--here` refuses instead. |
| `coldbox vm exec NAME -- CMD...` | Runs CMD in the VM as `runner` (passwordless sudo) in its home directory, passing stdin, stdout and the exit code through: `coldbox vm exec NAME -- bash -s < script.sh`. ssh rejoins words with spaces, so over ssh quote an argument that holds spaces twice: `ssh host coldbox vm exec NAME -- bash -c "'cd repo && make test'"`. |
| `coldbox vm down NAME` / `coldbox vm list` | Deletes the VM / lists ad-hoc VMs and their time left. `exec` and `down` touch only VMs that `vm up` made, and reach one on another overflow host by the host its name ends with. |
| `coldbox register OWNER/REPO [--slots N] [--allow-public] [--allow-personal-login]` or `ORG` | Starts `coldbox-runner@…` (systemd): N loops of one VM waiting for one job, receipt, destroy, repeat. For a repo it also autoscales (below); `--slots 0` runs only bursts. |
| `coldbox unregister OWNER/REPO` | Stops that service and deletes the `cbx-*` runners this host started on GitHub. Other hosts' runners stay online. |
| `coldbox serve OWNER/REPO --once` | The loop the service runs, in the foreground; `--once` stops after one VM and exits 0 for `pass`, 1 for `fail`, 125 otherwise (like `coldbox run`). |
| `coldbox reap [--keep-days 30]` | Deletes job VMs past their TTL (`--ttl`, default 6h for runners, 1h for `run`), job VMs other than `vm up` ones with no live worker (left behind by a reboot or a crash) regardless of TTL, and receipts, job logs, submission logs, and artifacts older than 30 days. Active or unreconciled jobs keep their evidence until the reaper can settle them. Every VM Coldbox creates carries `user.coldbox.managed=true` and `user.coldbox.expires` (Unix seconds, or `never` for the live `base-dev` and a base still being built); reap gives the retired `base-prev` 14 days for `build-base --rollback`, then deletes it and any other marked VM past its expiry. It finalizes each receipt in `/var/lib/coldbox/receipts` whose VM and worker are gone (`vm.destroyed: true`, `reconciled_by`). The reaper timer runs it every minute. `coldbox status` FAILs while such a VM or receipt exists, and on any receipt still at `vm.destroyed: false` past its TTL even if its worker is alive, and on any instance in the project without both marks, such as a hand-renamed old base. |
| `sudo coldbox build-base [--rollback]` | Rebuilds `base-dev/warm` with current packages and the newest actions-runner (what the weekly timer runs), or restores `base-prev` without rebuilding. Repeated rollback commands are safe until another rebuild starts. |
| `sudo ./uninstall.sh` | Keeps the GitHub App key and ID by default and reports each path. Use `sudo ./uninstall.sh --remove-app-credentials` to delete both installed files. Incus, installed packages and receipts are retained. It deletes only the Incus project, network, ACL and pool marked `user.coldbox.managed=true`; `install.sh` sets that mark and refuses to take over a same-named resource without it. The one exception is an unmarked pool whose every user is in Coldbox's own marked project (an install from before the mark): it marks that pool and changes nothing else. Likewise, it deletes only the UFW rules that `install.sh` added (listed in `/var/lib/coldbox/ufw-rules`). |

Stats counts observed requests accepted in the window and how many were assigned, reached a terminal
outcome (including incomplete), or got a definitive pass or fail. The definitive share uses observed
acceptances as its denominator. It combines receipts, direct submission records and logs, and GitHub
queue observations from the adjacent `queues/` directory. Queue observations retain job IDs for
30 days. GitHub job IDs join queue entries to receipts. A runner job without an ID appears in
coverage and may overlap a known queue entry, so it is excluded from the definitive rate. When an
acceptance timestamp is missing, stats uses the recorded job start or VM start. A direct submission
with only a log has unknown assignment. On the local host, a closed submission log with no queue
entry or receipt records an incomplete outcome. Copy each host's receipts and queue data under a
separate parent directory when combining reports. Unobserved requests remain unknown.

Timing totals show measured and missing job counts for VM ready, GitHub runner setup, Coldbox check
setup, test execution and VM teardown. Test time comes from executed Coldbox test checks or a host
suite receipt. Whole-job wall time, including workflow steps without explicit test instrumentation,
stays in the job wall column and in `split.job_wall_seconds` in JSON. The deprecated
`split.test_seconds` field remains an alias for job wall. The per-repo and per-host tables
show p50/p90/max for queue wait, VM ready and job wall. MISSING counts jobs without that
measurement. The report also includes settled VM verdict counts, the slowest workflow steps
and checks, the orphan count and host saturation.

The resource summary reports how many jobs have JUnit execution time, attributable disk
reclamation, and an observed dependency cache outcome. JUnit time sums the reported durations
of executed cases, so it can exceed wall time when tests run in parallel. Idle VM-hours count
time spent listening for work inside the selected window. Copy the console log beside each
runner receipt to include its idle time in a combined report. Cache-hit share uses jobs with a
known restore outcome. A job hits the cache when at least one dependency archive restores
successfully. The summary shows `unknown` when it has no attributable measurements.

To estimate cost per pass, set `per_vm_hour` under `[cost]` in `/etc/coldbox/stats.toml`.
`COLDBOX_STATS_CONFIG` selects another file for a single invocation. Stats multiplies the VM-hours
in the window, including idle and failed VMs, by the rate and divides by the number of passing
jobs settled in the window. The input and output use the same currency. Cost is `unknown` by
default.

### Autoscaling

Every 30 seconds a repo's runner service asks GitHub how many of its queued jobs a Coldbox runner can take (labels within
`self-hosted, linux, x64, dependabot, coldbox`; `dependabot` lets GitHub's Dependabot graph jobs for a private repo run
here) and how many of its `cbx-*` runners are free. For each queued job beyond the free runners it
starts one extra one-job VM runner, a burst. Every listed CI host does this the same way. Bursts and slots from all
repos share one host ceiling: CPU threads less the reserve, divided by 3 (`CORES_PER_JOB`; a busy job VM averaged 1.5
cores, p90 about 6, over 1,424 jobs), counting every Coldbox VM, idle or busy, and every worker whose VM is not up yet
(each worker records its VM name in `/var/lib/coldbox/workers`). All job VMs, idle or busy, at their full `limits.memory` plus the floor, 1.5 GiB for the host and the limits of other VMs must also fit in RAM, so jobs taken by idle slots can never overcommit it. Memory is admitted on what VMs really use: a VM starts only while the host's available RAM
(`MemAvailable` plus the clean ZFS ARC above its minimum, which ZFS evicts under pressure, counted up to the floor, less the `limits.memory` other non-job VMs have committed) stays above a floor of an eighth of RAM
(at least 2 GiB) after the full `limits.memory` of every VM still booting, this one included. A VM has booted once its runner log has output or its job marks it ready in `/var/lib/coldbox/ready`. VMs past boot are already
counted in `MemAvailable`, and the floor leaves room for them to grow. Storage is admitted the same way: a VM starts only while the
storage pool has free space for one full root disk (the profile's `size`) plus a tenth of the pool. Otherwise it prints
the free space and no burst starts. `install.sh` refuses a ZFS pool that
cannot hold one `--disk` plus that tenth after about 10 GiB of base images (56 GiB for the default 40 GiB disk, which is also the smallest default pool). On a 40-thread, 248 GiB host an idle warm VM used 1.2 GiB
of RAM and under a tenth of one CPU thread, and busy job VMs averaged 2.6 GiB of 12 GiB. It counts the larger of the
1-minute load average and a one-second sample of host CPU counters (I/O wait counts as busy), and adds none while that
leaves no free CPU thread or the host lacks the RAM for another VM above the floor. Busy VMs leave idle ticks in the
sample even when more tasks wait to run than the host has threads: on one 40-thread host the sample read 31 threads busy
while 50–94 tasks were runnable, load was 96 and tasks waited for CPU 54% of the time, so the sample alone admits jobs into a
CPU-starved host.
Hyperthreads count as free: on a 20-core, 40-thread host, jobs ran a median 24% slower at load 20–30 than below 20,
while a job left queued waited 13 minutes or more. Each burst started in the last 3 minutes counts
as one more busy CPU thread to reserve room before its CPU use appears in the sample. Unreadable or invalid counters stop bursts.
The journal includes raw load, measured CPU use, booting VMs and memory inputs for each scaling attempt.
A burst that gets no job within 10 minutes deregisters (GitHub refuses
that for a busy runner; other failures retry every minute), and its VM is destroyed with an `idle` receipt. The service polls with conditional requests, which do
not count against GitHub's API limit while nothing changes, and each service stops polling (so starts no bursts) once it has
seen fewer than 500 requests left before the limit resets. Services sharing one credential check this separately, so
together they can spend a little past that floor. Runners that are already starting still make their few registration and cleanup
calls. A free runner is one that is online and idle, or registered in the last 3 minutes and still booting; an offline runner
older than that does not count. `coldbox register` checks, under the same lock, only that every registered slot fits;
busy VMs don't block it. Org targets keep their fixed slots.

Stopping a runner service waits for its assigned jobs to finish, up to the VM lifetime of 6 hours.
Idle workers exit immediately. For code deploys, install the complete script to a sibling file and atomically rename it:

```sh
sudo install -m 755 coldbox /usr/local/bin/coldbox.next
sudo mv -f /usr/local/bin/coldbox.next /usr/local/bin/coldbox
```

Do not restart runner units for code deploys. Each persistent worker compares the installed script with its loaded code after admission, between jobs, and
re-execs if the code changed. A runner already registering or waiting for a job finishes that VM first.
The supervisor keeps its loaded scheduling code until its next service start; newly spawned workers load the installed script.
For the first deploy of this feature, existing workers need an idle-only restart to adopt it.
Unit-file changes need `systemctl daemon-reload`; restart each affected unit only after it is idle.
GitHub registration requests retry transient server and network failures up to five attempts with 1, 2, 4 and 8 second
backoff. Exhausted failures exit non-zero, and runner units restart even after an unexpected clean exit.

To force a stop after requesting one, send a second SIGTERM to the main process
with `sudo systemctl kill --kill-whom=main --signal=SIGTERM UNIT`. A forced stop records the GitHub run
identity when available and leaves an incomplete receipt.

A single host needs no CI host policy: without `/etc/coldbox/ci-hosts.json` it is its own primary with no overflow.
Multi-host admission is an optional module. Its one entry point is `fleet()` in `coldbox`, which returns nothing
without that file. Every SSH path (least-load ranking and `vm up`/`exec`/`down` forwarding to a peer) is
reached only through a policy that `fleet()` returned, so a single host never runs SSH. The module's tests are
in `CIAdmission` in `tests/test_coldbox.py`. Single-host paths use only `ci_host()`, `runner_admission()` and
`admission_check()`, and each of them treats a missing policy as "this host is its own primary".
To run CI on more hosts, write an operator-managed `/etc/coldbox/ci-hosts.json` on every runner host:

```json
{"primary": "ci-primary", "overflow": ["ci-overflow"]}
```

Use the actual kernel host names and a primary name usable as a trusted SSH alias. Only the primary and the
listed overflow hosts (distinct, none equal to the primary) may register or serve CI. An unreadable or invalid file
denies registration and worker startup. The list is membership only: every listed host registers its own slots, polls
every 30 seconds and starts bursts from its own room. Hosts that see the same queued job in the same poll may each
start a burst for it; the extra ones wait idle for another job and are released when their idle time ends, using
ceiling room until then. `vm up` forwarding needs noninteractive SSH access with trusted host keys
between the listed hosts.

Every runner guest receives root-owned `/etc/coldbox/host` and `/etc/coldbox/admission.json`, also included in its
host receipt as `admission`. After rollout, a workflow can validate host, repository, runner identity and policy
with `coldbox admission-check --expected-primary ci-primary`. The guard must run before checkout or any untrusted job
step. An existing fleet needs an explicitly authorized
policy rollout and retirement of old idle registrations before this protection covers it. Installing this code alone
does not remove runners registered by an older process.

A host that also runs other work can hold CPU threads back from bursts: `echo 20 | sudo tee /etc/coldbox/reserve`. Its
services then start no burst once the host has one job VM per 3 threads left after the reserve, and `coldbox status`
fails registered slots past that bound. They still start a burst only while load leaves a free thread on the whole host.
The load already includes the other work, so the reserve is not subtracted from it a second time. Ad-hoc VMs ignore it.
It is read at each poll, deleting it ends the reserve, and `coldbox status` shows it.

A host can also limit ad-hoc VMs, counted apart from CI: `echo 6 | sudo tee /etc/coldbox/vm-max`. `coldbox vm up` then
refuses while that many `vm up` VMs exist; they still count against the host's job VM ceiling. Without the file there is
no separate limit, and `coldbox status` shows it when set. `vm up` always refuses on the CI primary, so CI keeps its places.

A host can keep up to two booted VMs ready for a plain `vm up` or `vm up --offline`, so either is ready in about 2 s
instead of about 30 s: `echo offline offline | sudo tee /etc/coldbox/vm-warm` (any two of `online` and `offline`). A
`vm up` takes one under the host lock and it becomes that caller's ad-hoc VM; it is never returned to the pool. The
reaper boots a replacement, and so does the `vm up` that took one. A warm VM is deleted and replaced when the CLI, the
base snapshot, the `coldbox` profile or the guest overlay changes, or 6 h after it booted. Warm VMs count against the job
VM ceiling but not `vm-max`, and a cold `vm up` that would pass that ceiling deletes one to make room. A claimed VM is
handed out only after its agent answers and its network rules are set; otherwise it is deleted and the `vm up` boots a
fresh VM. `vm list`, `vm exec` and `vm down` never show warm VMs. A `vm up` with any other option boots a VM as before. Deleting the file deletes the warm VMs at the next reap.

CI jobs can also opt into a warm-standby VM pool (default `0`, off) by writing a count or `warm_pool_size = N` to
`/etc/coldbox/warm-pool` (or setting `COLDBOX_WARM_POOL_SIZE`). When enabled, `coldbox reap` and post-claim refills keep
up to `N` clean pre-booted VMs keyed by the CLI, base snapshot, `coldbox` profile, guest overlay content, and host checks
stamp (`user.coldbox.warm-job`). Any change to the base snapshot or guest overlay invalidates and reaps stale standby VMs.
Claims are atomic under the host lock, verify isolation and agent health, clear the warm tag permanently, and still destroy
the VM after a single job. Continuous `coldbox serve` workers also hand off post-job VM deletion to a bounded background
teardown reaper tracked under `STATE/teardown/` and drained on reload or shutdown, so the slot can start the next runner
immediately without losing crash recovery in `coldbox reap`.

A standby VM is prepared before any job exists. It holds the CLI and the guest overlay, and no token, repo or job input.
A job takes one only while it is running, carries the current stamp and has over a minute left. Once taken it loses its
pool mark for good, so no VM is handed to a second job, even when its delete fails. The next refill deletes a claim that
is not finished within 5 minutes or whose process died, a standby within 10 minutes of its 6 h TTL, one with an older
stamp and any beyond `N`. Each standby boot passes the same admission check as a job VM, so the pool never takes memory,
disk or a place a job could not have had, and a job that needs the room deletes a standby to get it. After a crash or a
reboot the next `coldbox reap` deletes what was left: a VM mid-boot, a VM mid-claim, a stopped standby and a VM whose job
ended without a delete. It deletes no VM that a live worker holds.

`coldbox pool status` shows the configured and ready counts, the pool stamp and base image, standby VMs by state
(`ready`, `booting`, `claimed`, `stale`, `retired`), the oldest one's age, the VM teardowns still pending, and the hits,
misses and reaps counted since `warm-pool.json` appeared in the state folder. A hit is a job VM taken from the pool. A
miss is a job VM that could have come from the pool and booted cold. Reaps are counted by cause: `stale` (the stamp
changed), `expired`, `excess`, `abandoned` (a crashed boot or claim), `yielded` (deleted to make room for a job) and
`unhealthy` (failed its checks when claimed). The command fails while a pool VM is past its expiry or the pool size or
stamp cannot be read, and `coldbox status` runs the same checks. Delete `warm-pool.json` to start the counts again.

Admission lasts for the runner VM lifetime configured with `--ttl`.
An idle runner VM is recycled when its TTL ends. A job that starts just before then can be cut off, so raise `--ttl` if your jobs are long.

Queue observations are saved under the state directory's `queues/OWNER/REPO.json` and displayed by
`coldbox status`, including observation time. The autoscaler logs `oldest waited N s` and `queue STALLED`
once when the oldest eligible queued job reaches `COLDBOX_QUEUE_STALL_SECONDS` (default 300, a positive
integer). It logs `queue CLEARED` when the next successful observation falls below the threshold or has
no queued jobs. Failed GitHub reads retain the last observation; status shows its observation time.
Completed runner receipts include `github.queue_wait_seconds` when GitHub job timing is available;
status and the runner log also show this duration.

Run `coldbox status --hosts` from your existing scheduled health check and use its exit status for
failure and recovery notifications. The check compares installed builds against the primary and
reports stale or unknown supervisor builds. For active repository supervisors, it flags stalled
queues and observations that are missing, unreadable, or more than five minutes old. Unreachable
peers and peers whose status cannot be read have separate findings.

If you already run `coldverify-deployed-check.service`, install the
[fleet health drop-in](ops/coldverify-deployed-check.service.d/coldbox-fleet.conf) in that unit's
drop-in directory and reload the user systemd manager. The unit runs fleet status after its
deployed-guest check and names each failed check in the journal. Its existing failure and
recovery hooks continue to handle notifications for the unit.

To clear a `BUILD DRIFT` finding, install the intended Coldbox revision on the primary and every
configured peer during an idle rollout. Upgrade the primary first so its fleet reader understands
the new stats exit codes from its peers. Run `coldbox status --hosts` again and check that every
row reports the intended build and there are zero `BUILD DRIFT` findings. The loaded supervisor
builds are listed separately, so check those rows too.

### JSON output

These commands emit one JSON object with `schema_version: 1`. Usage errors follow the CLI's
standard exit 2 behavior and write to stderr.

| Command | Exit 0 | Exit 1 | Exit 2 | Schema |
| --- | --- | --- | --- | --- |
| `coldbox status --json` | Health checks pass | A health check fails | Status cannot be read | [Status v1](docs/status.schema.json) |
| `coldbox status --hosts --json` | All hosts pass | At least one host fails, including build drift | No host reports failure and at least one is incomplete, or the fleet cannot be read | [Status v1](docs/status.schema.json) |
| `coldbox stats --json` | The report is readable with zero receipt-based orphans | At least one receipt indicates an orphan | Evidence is unreadable and no orphan is known, or stats cannot produce a report | [Stats v1](docs/stats.schema.json) |
| `coldbox pool status --json` | The warm pool is healthy or off | A pool VM is past its expiry, or the pool size or stamp cannot be read | The pool cannot be read | Fields below |

`coldbox pool status --json` adds `verdict`, `configured` (`null` when unreadable), `ready`, `standbys` (a count per
state), `overdue` (the names of pool VMs past their expiry), `stamp`, `base` (`snapshot`, `created_at`, `image`),
`oldest_standby_seconds`, `teardown` (`pending`, `abandoned` for records a dead worker left, `oldest_seconds`),
`metrics` (`since`, `hits`, `misses`, `reaped` by cause, `refill_failures`, and `last` with the newest event of each
kind) and `checks` in the shape status uses.

Stats still reports historical job outcomes. A failed job with a cleaned-up VM is a valid
observation. `unreadable_receipts` counts unreadable receipt files and unreadable input directories.

## Receipts and seams for the Cold tools

### The `coldbox verify` check

When a runner job named `verify` from `.github/workflows/verify.yml` finishes for a pull request, the host posts a
`coldbox verify` check run on the PR head through the GitHub App, with `external_id` set to the head commit's tree.
The host reads the job, the run and the trees from the GitHub API. The check is `success` only when GitHub reports a
successful job on the registered runner and the same head as the workflow run. The head's `.github/` and `coldbox.toml`
must match the base branch's, the Coldbox receipt must be `pass`, and the checkout's ColdVerify receipt
(`.cold/verify-receipt.json`) must report `PASS` for the head or its merge commit with the corresponding Git tree.
If a condition fails, the check summary gives the reason. The job VM can write the receipt, so a matching Git tree
only confirms that the reported tree belongs to the reported commit.

`coldbox status` audits the default branch of each active repository registration through the GitHub App.
`ENFORCED` means a branch rule requires the `coldbox verify` check from this App and the `verify` job has a
required `coldbox require-tests` step. `GAP` names missing enforcement. `UNVERIFIED` names evidence the audit
could not read. Both make status exit nonzero and appear in `coldbox status --hosts`. The audit uses PyYAML
on the host to read workflows. Its result covers the configured check requirement for ordinary merges.

To publish `coldbox verify (host suite)`, run `coldbox run --offline --trusted-suite FILE --verify-job ID PATH`
on the host. It runs the suite against the selected Actions job's PR head in a separate VM. A rule that requires
host-observed evidence must name `coldbox verify (host suite)` and select the Coldbox App. See [suite observations](docs/receipt.md#suite_observation).

A repository runner can also publish `coldverify receipt` after its `verify` job. Set
`COLDBOX_COLDVERIFY_HOST` in that runner unit's environment to an SSH alias with a dedicated identity for
Coldverify's restricted `host-receipt` command. A separate Coldbox process gets the suite manifest over SSH,
observes the job's base and head in separate offline VMs under the normal admission limits, and sends their
host receipts back for the verdict. Coldbox posts the returned payload on the job's head through the App.
The App key stays on the CI host.

Only the App can post the check, so a branch rule can require `coldbox verify` from that App alone. Coldbox does not
change any branch rule. When the VM left no job record, the host finds the job by its runner name in the
repo's recent pull request runs. After a worker exits, `coldbox reap` finishes receipts in the host state directory
once their VMs are gone. It publishes any pending check whose job it can identify, or records why it could not post.
If a check might already have reached GitHub, the reaper looks for it using the receipt ID and avoids a second POST.

Coldbox stays a separate repo. Other tools connect through two plain seams:

- **Receipt:** one JSON file per VM at `/var/lib/coldbox/receipts/<id>.json`, with the raw log next to it as `<id>.log`.
  Each file is written atomically, first as `incomplete` when the VM is requested and finally after the VM is destroyed.
  [docs/receipt.md](docs/receipt.md) documents schema `coldbox.receipt/v1`: verdicts, the GitHub job identity, per-test
  results from JUnit reports, and how to group receipts to find flaky tests. [examples/receipts/](examples/receipts/) has real
  receipts. `incomplete` means Coldbox could not tell; treat it as not passing.
  A job that writes files to `$COLDBOX_JOB_DIR/artifacts` in the VM gets them copied to `<id>.artifacts/` next to its
  receipt (up to 100 MiB and 1000 files), listed in the receipt's `artifacts`.
- **Cleanup (for Coldcleaner):** `coldbox reap` is idempotent and deletes only VMs in Incus project `coldbox`: `cbx-*` ones whose
  `user.coldbox.expires` has passed, whose name is older than a day with no readable expiry, or that no live worker owns
  (except `vm up` VMs), other VMs marked `user.coldbox.managed=true` whose expiry has passed (including `base-prev` 14 days after it was retired), plus receipts and their artifacts older than `--keep-days`. Use `sudo ./uninstall.sh` to remove the Coldbox services and managed Incus resources.

## Tested on

Test host: a fresh nested Ubuntu 24.04 VM (8 vCPUs, 16 GiB) started by the fresh-host acceptance test on a bare-metal Xeon E5-2660 v2 (40 threads) or Xeon E5-2430L v2 (24 threads) host. Each run installs Coldbox, registers a private test repo with one slot, dispatches a two-job workflow and needs a `pass` receipt with every job VM destroyed.

| What (measured 2026-11-12, three passing runs) | Time |
| --- | --- |
| `sudo ./install.sh --install-incus`, first run (Incus, packages, base VM, toolchains, runner) | 817 s, 914 s, 1,068 s |
| Fresh VM started until the first `pass` receipt (install, register, dispatch, job) | 1,319 s, 1,452 s, 1,488 s |
| Workflow dispatched until both jobs finished (in these runs the second job started after the first ended) | 348 s, 368 s, 411 s |
| First job queued until picked up (runner VM is booted before the job arrives) | 1 s, 1 s, 2 s |

A job VM takes a median of 56 s until ready (90% under 90 s), measured over 1,197 jobs in the 7 days to
2026-11-08 on a bare-metal host with 40 threads and Incus 6.0.7. In a nested-KVM cloud VM it took 103–110 s; see
[docs/vps.md](docs/vps.md#how-fast-it-is).

The same commit of a private Python repo (70 pytest tests, plus compileall and shellcheck) ran on Coldbox and on GitHub-hosted
`ubuntu-latest`:

| Runner | `coldbox checks` step, each run | Price per minute |
| --- | --- | --- |
| Coldbox, 4 vCPU VM | 86 s, 71 s | $0 (your electricity) |
| Coldbox, 8 vCPU VM (auto-sized), 2 slots | 101 s, 77 s | $0 |
| GitHub-hosted `ubuntu-latest` | 68 s, 71 s, 81 s | $0.006, each job billed as 2 min |

Coldbox is not faster per job on this repo. It is about even, and 25% slower in one run. The suite runs mostly on one core
(74 s directly on the host), so doubling the vCPUs did not help. Coldbox wins on cost, on queue time (about 1 s) and on running
jobs in parallel: with `--slots 2`, both jobs of one workflow started in the same second. One run of that suite failed a timing-
sensitive test (a subprocess timeout) and passed on re-run; on a desktop CPU shared with other work, expect that kind of flakiness
in tight timeouts.

## Limitations

- Linux x86_64 hosts only (Incus with KVM). No macOS, Windows (WSL2 included) or ARM hosts, and job VMs are Ubuntu 26.04 only.
- Not faster per job than hosted runners on single-threaded suites; the gains are cost, queue time and parallel slots.
- The only dependency caches are npm, pip, Go modules and cargo downloads, per repo and opt-in on each host. The folder must belong to the runner service's user:
  `sudo install -d -o "$(systemctl show -P User "coldbox-runner@$(systemd-escape OWNER/REPO).service")" /var/lib/coldbox/cache/OWNER/REPO`.
  After a job of that repo's runner passes on a push to the repo's default branch (the host asks GitHub, not the VM),
  the host saves the VM's `~/.npm` as `npm.tar`, `~/.cache/pip` as `pip.tar`, `~/go/pkg/mod` as `go.tar`,
  `~/.cargo/registry` as `cargo.tar` and `~/.cargo/git` as `cargo-git.tar` (each at most 2 GiB) without unpacking them. Each later job VM of that repo starts with them unpacked as the `runner` user. Pull request jobs read them and
  never write them. They are off for org runners and for repos served with `--allow-public`.
  `coldbox run --repo` and `coldbox api submit` restore the same cache when the GitHub App confirms
  the requested private repo and the exact SHA is the tip of one of its branches. Direct runs only restore archives.
  The Go build cache, cargo's `target/` and the rest of `~/.cargo` (installed binaries, config, credentials) are not saved.
- Each host starts VMs only on itself. Every listed CI host autoscales the same way (see [Autoscaling](#autoscaling)),
  but there is no shared pool of hosts.

## License

MIT, see [LICENSE](LICENSE). Security reports: [SECURITY.md](SECURITY.md).
