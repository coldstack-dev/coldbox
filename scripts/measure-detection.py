#!/usr/bin/env python3
"""Measure Coldbox detection on fixtures and pinned public repositories.

From the repo root, run:
    python3 scripts/measure-detection.py

Requires the Coldbox guest toolchain (Python 3.11+ with venv and pytest,
Node.js with a JUnit reporter and npm, Go, and Rust with rustfmt and Clippy).
The public repositories and their dependencies require network access.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
COLDBOX = [sys.executable, str(ROOT / 'coldbox')]
COMPILE = "python3 -m compileall -q -x '/[.]|node_modules' ."
PYTEST = 'python3 -m pytest -q'
VENV = ('python3 -m venv --system-site-packages "${TMPDIR:-/tmp}/coldbox-py" && '
        '. "${TMPDIR:-/tmp}/coldbox-py/bin/activate" && ')
NODE_TEST = 'node --test --test-reporter=junit test.cjs > report.xml'
GO_LINT = 'test -z "$(gofmt -l .)" && go vet ./...'
RUST_LINT = 'cargo fmt --check && cargo clippy --all-targets -- -D warnings'
WHEEL = 'coldbox_fixture_dep-1.0-py3-none-any.whl'

FIXTURES = [
    ('Python', 'pytest with requirements', {
        'requirements.txt': f'./{WHEEL}\n',
        'test_dependency.py': 'from coldbox_fixture_dep import VALUE\n\ndef test_value():\n    assert VALUE == 42\n',
    }, [('setup', VENV + 'python3 -m pip install -r requirements.txt'), ('lint', COMPILE), ('test', PYTEST)], 1),
    ('Python', 'empty pytest file', {
        'tests/test_x.py': '',
    }, [('lint', COMPILE)], 0),
    ('JavaScript', 'npm test with a Node suite', {
        'package.json': json.dumps({'scripts': {'test': NODE_TEST}}),
        'test.cjs': "const { describe, it } = require('node:test');\n"
                    "const assert = require('node:assert/strict');\n"
                    "describe('arithmetic', () => it('addition', () => assert.equal(1 + 1, 2)));\n",
    }, [('setup', 'npm install'), ('test', 'npm test')], 1),
    ('JavaScript', 'npm test with a top-level Node test', {
        'package.json': json.dumps({'scripts': {'test': NODE_TEST}}),
        'test.cjs': "require('node:test')('addition', () => require('node:assert/strict').equal(1 + 1, 2));\n",
    }, [('setup', 'npm install'), ('test', 'npm test')], 1),
    ('JavaScript', 'npm no-op test script', {
        'package.json': json.dumps({'scripts': {'test': 'echo no-tests-ran'}}),
    }, [('setup', 'npm install')], 0),
    ('TypeScript', 'project references and Vitest', {
        'package.json': json.dumps({'devDependencies': {'typescript': '5.7.3', 'vitest': '3.2.6'}}),
        'tsconfig.json': '{"files":[],"references":[{"path":"./app"}]}\n',
        'app/tsconfig.json': json.dumps({'compilerOptions': {'composite': True, 'outDir': '../dist', 'types': []},
                                        'files': ['value.ts']}),
        'app/value.ts': 'export const value: number = 42;\n',
        'value.test.mjs': "import { expect, test } from 'vitest';\n"
                          "import { value } from './dist/value.js';\n"
                          "test('built reference', () => expect(value).toBe(42));\n",
        'vitest.config.mjs': "export default { test: { reporters: ['default', 'junit'],"
                             " outputFile: 'report.xml', maxWorkers: 2 } };\n",
    }, [('setup', 'npm install'), ('lint', 'npx --yes -p typescript tsc --build'),
        ('test', 'npx --yes vitest run')], 1),
    ('Go', 'Go test package', {
        'go.mod': 'module example.com/fixture\n\ngo 1.18\n',
        'value_test.go': 'package fixture\n\nimport "testing"\n\nfunc TestValue(t *testing.T) {}\n',
    }, [('lint', GO_LINT), ('test', 'go test -v ./...')], 1),
    ('Go', 'Go package without tests', {
        'go.mod': 'module example.com/fixture\n\ngo 1.18\n',
        'value.go': 'package fixture\n\nconst Value = 42\n',
    }, [('lint', GO_LINT)], 0),
    ('Rust', 'Cargo unit tests', {
        'Cargo.toml': '[package]\nname = "fixture"\nversion = "0.1.0"\nedition = "2021"\n',
        'src/lib.rs': 'pub fn value() -> u32 {\n    42\n}\n\n'
                      '#[test]\nfn check_value() {\n    assert_eq!(value(), 42);\n}\n\n'
                      '#[test]\nfn check_sum() {\n    assert_eq!(value() + 1, 43);\n}\n',
    }, [('lint', RUST_LINT), ('test', 'cargo test')], 2),
]

PUBLIC_REPOS = [
    ('Python', 'benjaminp/six', 'ebd9b3af90247b8858d415a05e96e9ee61e48d07',
     [('lint', COMPILE), ('test', PYTEST)]),
    ('JS/TS', 'remcohaszing/remark-mdx-frontmatter', 'b59e37754bd746040d927ba46ea25ee7f912f578',
     [('setup', 'npm ci'), ('lint', 'npx --yes eslint .'), ('lint', 'npx --yes -p typescript tsc --noEmit'),
      ('test', 'npm test')]),
    ('Go', 'golang/time', 'fb013b3d305a26f5ef5350a6eaffc7c87a200383',
     [('lint', GO_LINT), ('test', 'go test -v ./...')]),
    ('Rust', 'rust-lang/semver', '280ebcb6edac3aa4cdc545dbff8a26c5ac4861fe',
     [('lint', RUST_LINT), ('test', 'cargo test')]),
]


def write_fixture(root, files):
    for name, source in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    if 'requirements.txt' in files:
        with ZipFile(root / WHEEL, 'w') as wheel:
            wheel.writestr('coldbox_fixture_dep.py', 'VALUE = 42\n')
            info = 'coldbox_fixture_dep-1.0.dist-info/'
            wheel.writestr(info + 'METADATA', 'Metadata-Version: 2.1\nName: coldbox-fixture-dep\nVersion: 1.0\n')
            wheel.writestr(info + 'WHEEL', 'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
            wheel.writestr(info + 'RECORD', '')


def checkout(root, repo, sha):
    for command in (['git', 'init', '-q'],
                    ['git', 'fetch', '-q', '--depth', '1', f'https://github.com/{repo}.git', sha],
                    ['git', 'checkout', '-q', '--detach', sha]):
        subprocess.run(command, cwd=root, check=True, timeout=90, stdin=subprocess.DEVNULL)


def measure(root, name, expected, expected_count=None):
    work = root.parent
    job = work / 'job'
    job.mkdir()
    env = dict(os.environ, COLDBOX_JOB_DIR=str(job), COLDBOX_HOST_CHECKS=str(work / 'host-checks.toml'),
               TMPDIR=str(work), npm_config_audit='false', npm_config_fund='false', CI='true')
    if expected_count is not None:
        env.update(PIP_NO_INDEX='1', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    for key in ('GITHUB_EVENT_NAME', 'GITHUB_ACTIONS', 'PYTEST_ADDOPTS', 'COLDBOX_JUNIT'):
        env.pop(key, None)
    run = subprocess.run([*COLDBOX, 'checks', str(root)], env=env, capture_output=True, text=True, timeout=300)
    if run.returncode:
        print(f'{name} exited {run.returncode}', file=sys.stderr)
        print(run.stdout + run.stderr, file=sys.stderr, end='')
    checks = job / 'checks.json'
    steps = json.loads(checks.read_text())['checks'] if checks.exists() else []
    junit = json.loads(subprocess.check_output([*COLDBOX, 'junit', str(job / 'junit'), str(root)], text=True))
    count = junit['total'] - junit['skipped']
    actual = [(step['check'], step['command'].partition(' 2>&1 | awk ')[0]) for step in steps]
    count_ok = count > 0 if expected_count is None else count == expected_count
    correct = actual == expected and run.returncode == 0 and count_ok
    test_statuses = {step['status'] for step in steps if step['check'] == 'test'}
    zero = ''
    if count == 0 and 'fail' in test_statuses:
        zero = ' (test command failed, zero tests counted)'
    elif count == 0 and 'pass' in test_statuses:
        zero = ' (test command passed, zero tests counted)'
    return actual, run.returncode, junit['total'], count, zero, correct


def main():
    versions = []
    for command in ([sys.executable, '--version'], [sys.executable, '-m', 'pytest', '--version'],
                    ['node', '--version'], ['npm', '--version'], ['go', 'version'], ['rustc', '--version'],
                    ['cargo', 'fmt', '--version'], ['cargo', 'clippy', '--version']):
        version = subprocess.check_output(command, text=True, timeout=30).strip()
        if command[0] in ('node', 'npm'):
            version = f'{command[0]} {version}'
        versions.append(f'`{version}`')
    scratch = ROOT / '.smoke'
    scratch.mkdir(exist_ok=True)
    rows = []
    public = []
    with tempfile.TemporaryDirectory(prefix='detection-', dir=scratch) as tmp:
        for index, (language, name, files, expected, expected_count) in enumerate(FIXTURES):
            root = Path(tmp) / f'fixture-{index}' / 'repo'
            root.mkdir(parents=True)
            write_fixture(root, files)
            actual, exit_code, discovered, count, zero, correct = measure(
                root, name, expected, expected_count)
            rows.append((language, name, expected_count, actual, exit_code, discovered, count, zero, correct))
        for language, repo, sha, expected in PUBLIC_REPOS:
            root = Path(tmp) / language.replace('/', '-') / 'repo'
            root.mkdir(parents=True)
            try:
                checkout(root, repo, sha)
                result = measure(root, repo, expected)
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                print(f'{repo}: {error}', file=sys.stderr)
                result = ([], '-', '-', '-', ' (could not finish)', False)
            public.append((language, repo, sha, *result))
    build = hashlib.sha256((ROOT / 'coldbox').read_bytes()).hexdigest()[:12]
    print(f'# Coldbox detection accuracy\n\nDetector build `{build}`. Rerun from the repository root with')
    print('\n```sh\npython3 scripts/measure-detection.py\n```\n')
    print('Use the Coldbox guest toolchain and network access. See the script for prerequisites.\n')
    print(f'Tool versions for this run were {", ".join(versions)}.\n')
    print('A fixture is correct when the generated checks match the expected plan, all checks exit successfully,')
    print('and Coldbox counts the expected number of executed tests. The counts describe this synthetic corpus.\n')
    print('| Language | Correct plans | Attempted plans |\n| --- | ---: | ---: |')
    for language in dict.fromkeys(row[0] for row in rows):
        results = [row[-1] for row in rows if row[0] == language]
        print(f'| {language} | {sum(results)} | {len(results)} |')
    print(f'| **Total** | **{sum(row[-1] for row in rows)}** | **{len(rows)}** |\n')
    print('| Fixture | Detected checks (abridged) | Exit | Expected tests | Discovered | Counted as executed | Result |')
    print('| --- | --- | ---: | ---: | ---: | ---: | --- |')
    for _, name, expected_count, actual, exit_code, discovered, count, zero, correct in rows:
        checks = ', '.join(f'{check}: `{command.strip()}`' for check, command in actual).replace('|', r'\|')
        result = 'Correct' if correct else f'Mismatch{zero}'
        print(f'| {name} | {checks} | {exit_code} | {expected_count} | {discovered} | {count} | {result} |')
    print('\nA public repo is correct when the detected plan matches the expected plan, all checks pass,')
    print('and Coldbox counts at least one executed test. Each checkout uses the named commit.\n')
    correct_public = sum(row[-1] for row in public)
    print(f'**Public repos with correct plans: {correct_public}/{len(public)}**\n')
    print('| Language | Repo and commit | Detected checks (abridged) | Exit | Discovered | Counted as executed | Result |')
    print('| --- | --- | --- | ---: | ---: | ---: | --- |')
    for language, repo, sha, actual, exit_code, discovered, count, zero, correct in public:
        checks = ', '.join(f'{check}: `{command.strip()}`' for check, command in actual).replace('|', r'\|').replace('\n', ' ')
        result = 'Correct' if correct else f'Mismatch{zero}'
        print(f'| {language} | [{repo}](https://github.com/{repo}/tree/{sha}) | {checks} | {exit_code} | {discovered} | {count} | {result} |')
    return 0 if all(row[-1] for row in rows) and correct_public >= 3 else 1


if __name__ == '__main__':
    sys.exit(main())
