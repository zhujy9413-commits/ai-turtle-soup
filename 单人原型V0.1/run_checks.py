"""One offline regression command for the maintained host; no model or key access."""
import os
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
app = root/'app' if (root/'app/server.py').exists() else root
result = subprocess.run(
    [sys.executable, '-m', 'unittest', 'discover', '-s', str(app/'tests'), '-v'],
    cwd=root,
    env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
)
raise SystemExit(result.returncode)
