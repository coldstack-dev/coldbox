#!/usr/bin/env python3
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description='Replay a ColdPass evaluation in an offline Coldbox VM.')
    parser.add_argument('checker', type=Path, help='trusted cold_check.py from a pinned ColdPass checkout')
    parser.add_argument('bundle', type=Path, help='tar archive of a small evaluation workspace')
    args = parser.parse_args()
    vm = ['coldbox', 'vm']
    receipt = dict(checker_sha256=None, bundle_sha256=None, vm=None, destroyed=None,
                   evaluation_exit=None, evaluation=None)
    passed = False
    try:
        checker, bundle = args.checker.read_bytes(), args.bundle.read_bytes()
        receipt['checker_sha256'] = hashlib.sha256(checker).hexdigest()
        receipt['bundle_sha256'] = hashlib.sha256(bundle).hexdigest()
        started = subprocess.run([*vm, 'up', '--offline', '--here'], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, text=True, check=True)
        receipt['vm'] = started.stdout.strip()
        guest = [*vm, 'exec', receipt['vm'], '--', 'sh', '-ec']
        subprocess.run([*guest, 'cat > cold_check.py'], input=checker, stdout=sys.stderr, check=True)
        replay = subprocess.run([*guest, 'mkdir evaluation; cd evaluation; tar -xf -; '
                                 'COLDPASS_WORKSPACE="$PWD" python3 -I ../cold_check.py --probe'],
                                input=bundle, stdout=subprocess.PIPE)
        receipt['evaluation_exit'] = replay.returncode
        if replay.returncode == 0:
            evaluation = json.loads(replay.stdout)
            receipt['evaluation'] = evaluation
            passed = evaluation['status'] == 'PASS' and evaluation['details']['working'] is True
        else:
            print(replay.stdout.decode(errors='replace'), file=sys.stderr, end='')
    except (OSError, subprocess.CalledProcessError, ValueError, KeyError, TypeError) as error:
        print(f'coldpass-smoke: {error}', file=sys.stderr)
    finally:
        try:
            if receipt['vm'] is not None:
                receipt['destroyed'] = subprocess.run([*vm, 'down', receipt['vm']],
                                                      stdin=subprocess.DEVNULL, stdout=sys.stderr).returncode == 0
        except OSError as error:
            receipt['destroyed'] = False
            print(f'coldpass-smoke: {error}', file=sys.stderr)
        finally:
            print(json.dumps(receipt))
    return 0 if passed and receipt['destroyed'] else 1


if __name__ == '__main__':
    sys.exit(main())
