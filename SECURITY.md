# Security

Coldbox runs other people's CI code on your machine. Each job gets a fresh VM that is destroyed afterwards, its network can
reach the internet but not your LAN, and by default it serves only private repos. It always refuses pull requests from
forks, on every repo, approved or not. A public repo is served only after its owner opts in with `--allow-public`; outside
contributors' pull requests then get no CI on your hosts and a writer reviews them by hand. Read the safety model in the
README before using `--allow-public`. Do not include tokens, private hostnames or
personal paths in reports.

## Origins Coldbox cannot tell apart

Coldbox refuses a job as a fork when the event shows a head repo that differs from the repo, or shows none for
`pull_request`, `pull_request_target` or `workflow_run`. These origins carry no head repo, so Coldbox can't tell
fork code from the repo's own:

| Origin | What Coldbox does | Why that is the limit |
|---|---|---|
| `merge_group` on an `--allow-public` repo | Runs it when a user with write access triggered it | The merge queue branch lives in the repo itself. It holds a pull request's commits only after a writer queued that pull request, and the same commits run on `push` once merged. On a private repo it is refused. |
| `issue_comment` on an `--allow-public` repo | Runs it when a user with write access triggered it | The workflow, not Coldbox, decides what the job checks out. A workflow that checks out a commented fork pull request runs fork code. On a private repo it is refused. |
| `coldbox run --repo` and `coldbox api submit` | Runs the named commit | Only someone with a shell on the host can call them. GitHub serves a fork pull request's head commit through the base repo (`refs/pull/N/head`), so Coldbox can't tell that commit from the repo's own. |
| `dynamic` on a private repo | Runs it unless the ref is `refs/pull/...` or the event shows a pull request from another repo | The job-started hook has no GitHub credential to ask where a `dynamic` job's code came from. See the README safety model. |

This project is unpublished and has no public security reporting channel. Report concerns to the maintainer through an
already established private channel. No support or response-time commitment is offered.
