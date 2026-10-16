import json
import subprocess
import sys


def test_version_prints_json():
    result = subprocess.run(
        [sys.executable, "-m", "stratum_engine.cli", "version"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"version": "0.1.0"}