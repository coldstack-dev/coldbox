# AGENTS.md

Agent guide for setting up and running Coldbox. For end-to-end setup, follow [prompts/0-setup.md](prompts/0-setup.md) (confirming every `ASK` gate with the user first) and read the [README](README.md) safety model.

- **Install (Linux x86_64 with `/dev/kvm` only):** `git clone https://github.com/coldstack-dev/coldbox.git && cd coldbox && sudo ./install.sh`
- **Verify (Coldbox's `doctor` health check):** `coldbox status --json` (also `coldbox stats --json`, `coldbox pool status --json`)
- **First command:** `coldbox detect .` to preview checks, then commit `coldbox.toml` and run `coldbox run .`
- **Contributor tests (no Incus needed):** `python3 -m unittest discover -s tests -v` and `shellcheck $(git ls-files '*.sh')` (see [CONTRIBUTING.md](CONTRIBUTING.md))

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Pass / healthy |
| `1` | Check or job failed, health check failed, or call refused |
| `2` | Usage error, invalid `coldbox.toml`, or unreadable status |
| `75` | `api submit` queue full, or `api receipt`/`evidence` still running |
| `125` | Incomplete verdict, or host/Incus error |

## Approval rules

- Never pass `--apply`, `--yes`, `--install-incus`, `--allow-public`, or `--allow-personal-login` without the user's explicit approval.
- Never modify host packages, run `sudo ./install.sh`, run `sudo ./uninstall.sh`, or start persistent services via `coldbox register` without explicit user approval; never edit `install.sh` to skip checks.
- Fork pull requests never run on Coldbox. Check `verdict`, `tests_ran`, and `vm.destroyed` in the receipt before reporting a pass.
