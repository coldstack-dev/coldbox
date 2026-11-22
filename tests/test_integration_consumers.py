import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = (ROOT / 'docs' / 'integration.md').read_text()
CALLS = DOC.split('\n## Calls other tools make\n')[1].split('\n## ')[0]
COLDBOX_CALL = re.compile(r'\bcoldbox (?=[a-z-])')
RUNS_ON = re.compile(r'^ *runs-on: (.*)$', re.M)
SHELL_WORDS = {'$name': [['NAME']], '$ttl': [['TTL']], '$offline': [['--offline'], []]}


GUEST_TEMPLATE = '/opt/coldstack/ci/templates/verify-coldbox.yml'


def consumer_file(variable, in_ci=None):
    path = os.environ.get(variable) or (in_ci if os.environ.get('GITHUB_ACTIONS') == 'true' else None)
    if not path:
        raise unittest.SkipTest(f'set {variable} to check the real file')
    return Path(path).read_text()


def shell_call(line):
    lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    words = []
    for word in lexer:
        if set(word) <= set('();<>|&'):
            break
        words.append(word)
    return words


def documented_forms(words):
    forms = [[]]
    for i, word in enumerate(words):
        options = [['CMD']] if i and words[i - 1] == '-c' else SHELL_WORDS.get(word, [[word]])
        forms = [form + option for form in forms for option in options]
    return {' '.join(form) for form in forms}


def coldbox_calls(text):
    text = re.sub(r'^\s*#.*$', '', text, flags=re.M)
    return [form for match in COLDBOX_CALL.finditer(text)
            for form in sorted(documented_forms(shell_call(text[match.start():].split('\n', 1)[0])))]


class VmrunAndOffload(unittest.TestCase):
    def setUp(self):
        self.vmrun = consumer_file('COLDBOX_CONTRACT_VMRUN')
        self.offload = (Path(os.environ['COLDBOX_CONTRACT_VMRUN']).resolve().parent / 'offload').read_text()

    def test_vmrun_makes_exactly_the_documented_calls(self):
        documented = re.search(r'^`vmrun`, .*?```sh\n(.*?)```', CALLS, re.S | re.M)[1].splitlines()
        self.assertTrue(documented)
        self.assertEqual(set(coldbox_calls(self.vmrun)), set(documented))

    def test_offload_calls_coldbox_only_through_vmrun(self):
        self.assertEqual(coldbox_calls(self.offload), [])
        self.assertIn('runs `vmrun`', CALLS)

    def test_host_files_they_read_are_the_documented_ones(self):
        key, incus_env = re.search(r'`vmrun` reads `(\w+)` from `([^`]+)`', DOC).groups()
        reserve = re.search(r'`offload` reads `([^`]+)`', DOC)[1]
        self.assertIn(f'{{COLDBOX_INCUS_ENV:-{incus_env}}}', self.vmrun)
        self.assertIn(f"'s/^{key}=//p'", self.vmrun)
        self.assertIn(f"RESERVE = '{reserve}'", self.offload)


class ColdStackTemplate(unittest.TestCase):
    def setUp(self):
        self.template = consumer_file('COLDBOX_CONTRACT_TEMPLATE', in_ci=GUEST_TEMPLATE)

    def test_routes_like_the_example_workflow_onto_the_documented_labels(self):
        documented = re.search(r'^runs-on: (\[.*\])$', CALLS, re.M)[1]
        example = RUNS_ON.findall((ROOT / 'examples' / 'coldbox.yml').read_text())
        self.assertEqual(example, [documented])
        self.assertEqual(RUNS_ON.findall(self.template), example)

    def test_runs_exactly_the_documented_coldbox_steps(self):
        documented = re.findall(r'^run: (.*)$', CALLS, re.M)
        self.assertTrue(documented)
        self.assertEqual(coldbox_calls(self.template), documented)


class ColdExec(unittest.TestCase):
    def test_versioned_api_exports_only_a_terminal_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, COLDBOX_STATE=directory)

            def api(*args):
                return subprocess.run([sys.executable, str(ROOT / 'coldbox'), 'api', *args], env=env,
                                      capture_output=True, text=True)

            version = api('version')
            self.assertEqual(version.returncode, 0, version.stderr)
            self.assertEqual(json.loads(version.stdout), {'interface': 'cold.exec/v0', 'backend': 'coldbox'})

            name = 'cbx-1-abcdef-test'
            path = Path(directory) / 'receipts' / f'{name}.json'
            path.parent.mkdir()
            receipt = dict(schema='coldbox.receipt/v1', id=name, mode='local', repo='me/app', sha='a' * 40,
                           verdict='incomplete', vm=dict(name=name, destroyed=False))
            path.write_text(json.dumps(receipt))
            pending = api('evidence', name)
            self.assertEqual((pending.returncode, pending.stdout), (75, ''))

            receipt['vm'].update(destroyed=True, destroyed_at='2026-01-01T00:00:00Z')
            for verdict, code in (('pass', 0), ('fail', 1), ('incomplete', 125)):
                with self.subTest(verdict=verdict):
                    receipt['verdict'] = verdict
                    path.write_text(json.dumps(receipt))
                    result = api('evidence', name)
                    self.assertEqual(result.returncode, code, result.stderr)
                    self.assertEqual(len(result.stdout.splitlines()), 1)
                    self.assertEqual(json.loads(result.stdout), {'schema': 'coldbox.evidence/v1',
                                                                 'interface': 'cold.exec/v0', 'receipt': receipt})
                    raw = api('receipt', name)
                    self.assertEqual(raw.returncode, code, raw.stderr)
                    self.assertEqual(json.loads(raw.stdout), receipt)
                    self.assertEqual(json.loads(path.read_text()), receipt)

            missing = 'cbx-2-abcdef-test'
            unknown = api('evidence', missing)
            self.assertEqual((unknown.returncode, unknown.stdout), (1, ''))
            (path.parent / f'{missing}.submit.log').touch()
            failed = api('evidence', missing)
            self.assertEqual((failed.returncode, failed.stdout), (125, ''))
