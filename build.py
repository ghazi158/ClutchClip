"""Builds dist/ClutchClip/ClutchClip.exe (a self-contained folder) and, with --zip, dist/ClutchClip-windows.zip.
Needs the dev setup from the README plus: pip install pyinstaller. ffmpeg must be installed (winget install Gyan.FFmpeg)."""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STAGE = os.path.join(HERE, "build_assets")


def stage():
    """Copy what the app bundles but git doesn't hold: ffmpeg/ffprobe and the CLIP weights."""
    import auto_editor as ae
    from huggingface_hub import hf_hub_download
    shutil.rmtree(STAGE, ignore_errors=True)
    os.makedirs(os.path.join(STAGE, "ffmpeg"))
    os.makedirs(os.path.join(STAGE, "model"))
    for tool in ("ffmpeg", "ffprobe"):
        shutil.copy(ae.find_tool(tool), os.path.join(STAGE, "ffmpeg", f"{tool}.exe"))
    weights = hf_hub_download("laion/CLIP-ViT-B-32-laion2B-s34B-b79K", "open_clip_model.safetensors")
    shutil.copy(weights, os.path.join(STAGE, "model", "open_clip_model.safetensors"))


def torchvision_binaries():
    """torchvision's compiled ops (_C_stable.pyd etc.) are loaded by path, so PyInstaller's analysis misses them."""
    import glob
    import torchvision
    folder = os.path.dirname(torchvision.__file__)
    return [a for f in glob.glob(os.path.join(folder, "*.pyd")) + glob.glob(os.path.join(folder, "*.dll"))
            for a in ("--add-binary", f"{f}{os.pathsep}torchvision")]


def build():
    sep = os.pathsep
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "editor.py", "--noconfirm", "--clean", "--windowed",
        "--name", "ClutchClip", "--icon", os.path.join("assets", "clutchclip.ico"),
        "--add-data", f"assets{sep}assets",
        "--add-data", os.path.join("build_assets", "ffmpeg") + f"{sep}ffmpeg",
        "--add-data", os.path.join("build_assets", "model") + f"{sep}model",
        "--collect-data", "open_clip",
        *torchvision_binaries(),
        *[a for m in ("tkinter", "matplotlib", "IPython", "pytest", "tensorboard") for a in ("--exclude-module", m)],
    ], cwd=HERE, check=True)
    out = os.path.join(HERE, "dist", "ClutchClip")
    for f in ("LICENSE", "THIRD_PARTY.md"):
        shutil.copy(os.path.join(HERE, f), out)


if __name__ == "__main__":
    stage()
    build()
    if "--zip" in sys.argv:
        shutil.make_archive(os.path.join(HERE, "dist", "ClutchClip-windows"), "zip",
                            os.path.join(HERE, "dist"), "ClutchClip")
    print("done:", os.path.join(HERE, "dist", "ClutchClip", "ClutchClip.exe"))
