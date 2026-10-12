"""StyleCut source package."""
import os
import shutil

# Ensure ffmpeg/ffprobe from standard Windows paths are discoverable if not in PATH
if not shutil.which("ffmpeg"):
    for candidate in [r"C:\ffmpeg\bin"]:
        if os.path.exists(os.path.join(candidate, "ffmpeg.exe")):
            os.environ["PATH"] = candidate + os.pathsep + os.environ.get("PATH", "")
            break

