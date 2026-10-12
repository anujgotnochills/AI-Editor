"""Build script for packaging StyleCut Engine into engine.exe."""
import subprocess
import sys
from pathlib import Path


def build():
    repo_root = Path(__file__).parent.parent.resolve()
    print(f"Building StyleCut engine.exe from {repo_root}...")

    # Ensure pyinstaller is installed
    try:
        import PyInstaller
    except ImportError:
        print("PyInstaller not found. Installing pyinstaller...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        str(repo_root / "engine.spec"),
    ]
    print(f"Running: {' '.join(cmd)}")
    subprocess.check_call(cmd, cwd=repo_root)
    print("\n[SUCCESS] engine.exe created in dist/engine.exe")


if __name__ == "__main__":
    build()
