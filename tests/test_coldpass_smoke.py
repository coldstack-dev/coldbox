import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'coldpass-smoke.py'
ADAPTER = """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

command = sys.argv[1:]
with Path('calls').open('a') as log:
    log.write(json.dumps(command) + '\\n')
if command == ['vm', 'up', '--offline', '--here']:
    print('cbx-test')
elif command[:4] == ['vm', 'exec', 'cbx-test', '--']:
    if command[-1] == 'cat > cold_check.py':
        if sys.stdin.buffer.read() != b'trusted checker':
            sys.exit(9)
        sys.exit(0)
    if sys.stdin.buffer.read() != b'evaluation bundle':
        sys.exit(9)
    working = os.environ['WORKING'] == '1'
    print(json.dumps({'status': 'PASS' if working else 'FAIL', 'details': {'working': working}}))
elif command == ['vm', 'down', 'cbx-test']:
    sys.exit(int(os.environ['DELETE_EXIT']))
else:
    sys.exit(9)
"""


class ColdPassSmoke(unittest.TestCase):
    def replay(self, working, delete_exit):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter = root / 'coldbox'
            adapter.write_text(ADAPTER)
            adapter.chmod(0o755)
            checker = b'trusted checker'
            bundle = b'evaluation bundle'
            (root / 'cold_check.py').write_bytes(checker)
            (root / 'bundle.tar').write_bytes(bundle)
            result = subprocess.run([sys.executable, str(SCRIPT), 'cold_check.py', 'bundle.tar'], cwd=root,
                                    env=dict(os.environ, PATH=f'{tmp}:{os.environ["PATH"]}',
                                             WORKING=str(int(working)), DELETE_EXIT=str(delete_exit)),
                                    capture_output=True, text=True, timeout=10)
            self.assertTrue(result.stdout, f'missing evaluation receipt: {result.stderr}')
            calls = [json.loads(line) for line in (root / 'calls').read_text().splitlines()]
        self.assertEqual(calls, [['vm', 'up', '--offline', '--here'],
                                 ['vm', 'exec', 'cbx-test', '--', 'sh', '-ec', 'cat > cold_check.py'],
                                 ['vm', 'exec', 'cbx-test', '--', 'sh', '-ec',
                                  'mkdir evaluation; cd evaluation; tar -xf -; '
                                  'COLDPASS_WORKSPACE="$PWD" python3 -I ../cold_check.py --probe'],
                                 ['vm', 'down', 'cbx-test']])
        self.assertEqual(json.loads(result.stdout), {
            'checker_sha256': hashlib.sha256(checker).hexdigest(),
            'bundle_sha256': hashlib.sha256(bundle).hexdigest(),
            'vm': 'cbx-test',
            'destroyed': delete_exit == 0,
            'evaluation_exit': 0,
            'evaluation': {'status': 'PASS' if working else 'FAIL', 'details': {'working': working}},
        })
        return result.returncode

    def test_reports_checker_pass_and_confirmed_cleanup(self):
        self.assertEqual(self.replay(working=True, delete_exit=0), 0)

    def test_cleans_up_after_a_checker_failure_even_when_checker_exits_zero(self):
        self.assertEqual(self.replay(working=False, delete_exit=0), 1)

    def test_cleanup_failure_fails_the_run_even_after_a_checker_pass(self):
        self.assertEqual(self.replay(working=True, delete_exit=1), 1)
