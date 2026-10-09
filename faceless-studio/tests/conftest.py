import os
import shutil
import tempfile
from pathlib import Path

# Isolated app home for every test session (must be set before importing studio)
_home = Path(tempfile.mkdtemp(prefix="studio-test-"))
shutil.copytree(Path(__file__).resolve().parent.parent / "formats", _home / "formats")
os.environ["STUDIO_HOME"] = str(_home)
