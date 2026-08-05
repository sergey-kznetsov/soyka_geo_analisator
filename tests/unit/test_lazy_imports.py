import subprocess
import sys


def test_importing_factfinder_does_not_import_heavy_modules():
    code = """
import sys
import factfinder
assert 'transformers' not in sys.modules
assert 'flair' not in sys.modules
assert 'bertopic' not in sys.modules
print('ok')
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.strip() == "ok"
