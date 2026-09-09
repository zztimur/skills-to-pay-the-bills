#!/usr/bin/env python3
"""Run the wholly synthetic privacy demo without modifying the checkout."""
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    repo = Path(__file__).resolve().parents[3]
    scanner = repo / 'privacy-gate/scripts/privacy_gate.py'
    with tempfile.TemporaryDirectory(prefix='synthetic-privacy-demo-') as directory:
        fixture = Path(directory) / 'settings.txt'
        # Deliberately constructed test value; never a usable credential.
        fixture.write_text('api_key = "' + 'sk_live_' + 'Q7m2V9x4K8r6T3p5N1w0Z2y8' + '"\n')
        command = [sys.executable, str(scanner), 'scan', '--path', str(fixture), '--strict']
        for expected in (1, 0):
            if expected == 0:
                print('\nRemove the synthetic credential assignment and rerun.\n')
                fixture.write_text('# Read credentials from the process environment.\n')
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            print((result.stdout + result.stderr).replace(directory, '<synthetic-demo>'), end='')
            print(f'Exit: {result.returncode}')
            if result.returncode != expected:
                raise SystemExit(f'Expected exit {expected}; demonstration failed.')
    print('PASS: synthetic block and clear paths. No real credential was used.')


if __name__ == '__main__':
    main()
