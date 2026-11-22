# Coldbox detection accuracy

Detector build `b3f847aead9a`. Rerun from the repository root with

```sh
python3 scripts/measure-detection.py
```

Use the Coldbox guest toolchain and network access. See the script for prerequisites.

Tool versions for this run were `Python 3.14.4`, `pytest 9.0.2`, `node v22.22.1`, `npm 9.2.0`, `go version go1.26.8 linux/amd64`, `rustc 1.93.1 (01f6ddf75 2026-02-11) (built from a source tarball)`, `rustfmt 1.8.0`, `clippy 0.1.93`.

A fixture is correct when the generated checks match the expected plan, all checks exit successfully,
and Coldbox counts the expected number of executed tests. The counts describe this synthetic corpus.

| Language | Correct plans | Attempted plans |
| --- | ---: | ---: |
| Python | 2 | 2 |
| JavaScript | 3 | 3 |
| TypeScript | 1 | 1 |
| Go | 2 | 2 |
| Rust | 1 | 1 |
| **Total** | **9** | **9** |

| Fixture | Detected checks (abridged) | Exit | Expected tests | Discovered | Counted as executed | Result |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| pytest with requirements | setup: `python3 -m venv --system-site-packages "${TMPDIR:-/tmp}/coldbox-py" && . "${TMPDIR:-/tmp}/coldbox-py/bin/activate" && python3 -m pip install -r requirements.txt`, lint: `python3 -m compileall -q -x '/[.]\|node_modules' .`, test: `python3 -m pytest -q` | 0 | 1 | 1 | 1 | Correct |
| empty pytest file | lint: `python3 -m compileall -q -x '/[.]\|node_modules' .` | 0 | 0 | 0 | 0 | Correct |
| npm test with a Node suite | setup: `npm install`, test: `npm test` | 0 | 1 | 1 | 1 | Correct |
| npm test with a top-level Node test | setup: `npm install`, test: `npm test` | 0 | 1 | 1 | 1 | Correct |
| npm no-op test script | setup: `npm install` | 0 | 0 | 0 | 0 | Correct |
| project references and Vitest | setup: `npm install`, lint: `npx --yes -p typescript tsc --build`, test: `npx --yes vitest run` | 0 | 1 | 1 | 1 | Correct |
| Go test package | lint: `test -z "$(gofmt -l .)" && go vet ./...`, test: `go test -v ./...` | 0 | 1 | 1 | 1 | Correct |
| Go package without tests | lint: `test -z "$(gofmt -l .)" && go vet ./...` | 0 | 0 | 0 | 0 | Correct |
| Cargo unit tests | lint: `cargo fmt --check && cargo clippy --all-targets -- -D warnings`, test: `cargo test` | 0 | 2 | 2 | 2 | Correct |

A public repo is correct when the detected plan matches the expected plan, all checks pass,
and Coldbox counts at least one executed test. Each checkout uses the named commit.

**Public repos with correct plans: 4/4**

| Language | Repo and commit | Detected checks (abridged) | Exit | Discovered | Counted as executed | Result |
| --- | --- | --- | ---: | ---: | ---: | --- |
| Python | [benjaminp/six](https://github.com/benjaminp/six/tree/ebd9b3af90247b8858d415a05e96e9ee61e48d07) | lint: `python3 -m compileall -q -x '/[.]\|node_modules' .`, test: `python3 -m pytest -q` | 0 | 198 | 182 | Correct |
| JS/TS | [remcohaszing/remark-mdx-frontmatter](https://github.com/remcohaszing/remark-mdx-frontmatter/tree/b59e37754bd746040d927ba46ea25ee7f912f578) | setup: `npm ci`, lint: `npx --yes eslint .`, lint: `npx --yes -p typescript tsc --noEmit`, test: `npm test` | 0 | 9 | 9 | Correct |
| Go | [golang/time](https://github.com/golang/time/tree/fb013b3d305a26f5ef5350a6eaffc7c87a200383) | lint: `test -z "$(gofmt -l .)" && go vet ./...`, test: `go test -v ./...` | 0 | 37 | 37 | Correct |
| Rust | [rust-lang/semver](https://github.com/rust-lang/semver/tree/280ebcb6edac3aa4cdc545dbff8a26c5ac4861fe) | lint: `cargo fmt --check && cargo clippy --all-targets -- -D warnings`, test: `cargo test` | 0 | 38 | 38 | Correct |
