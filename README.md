# ClutchClip

<img src="assets/logo.png" width="96" align="right" alt="ClutchClip logo">

Turn a long gameplay recording into a highlight reel. ClutchClip watches your footage, suggests the best parts,
and lets you review, trim and export them. Built around *The Finals* recordings, but it works on any footage.

## Install

1. Download **ClutchClip-windows.zip** from the [Releases page](https://github.com/ghazi158/ClutchClip/releases).
2. Right-click the zip → **Extract All**.
3. Open the extracted **ClutchClip** folder and double-click **ClutchClip.exe**.

That's it. There's nothing else to install and no internet connection is needed.

> Windows may show a blue "Windows protected your PC" box the first time because the app isn't code-signed.
> Click **More info → Run anyway**.

## Use

1. Drop a recording onto the window (or press **Ctrl+O**). OBS recordings (`.mkv`, `.mp4`) work well.
2. Wait while the AI analyses it. The first time takes a while; reopening is instant.
3. Review the suggested clips (hatched green). Press **A** to accept one, **Del** to remove one, or drag the edges to trim.
4. **Add music** if you like, then click **Export**.

Open **Moments** (right panel) to tell the AI to also keep deaths, round starts or cashouts, cut menus and scoreboards,
or add your own by describing what appears on screen.

Everything ClutchClip saves about you (analysis cache and settings) is in `%APPDATA%\ClutchClip`.
Your projects (`.aeproj`) are saved wherever you choose.

## Requirements

- Windows 10 or 11, 64-bit
- About 4 GB of free disk space for the app, more for the analysis cache
- An AMD GPU is used for fast export; other computers fall back to the CPU automatically (slower)

---

## For developers

Run from source:

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python editor.py [recording-or-project]
```

You also need [ffmpeg](https://ffmpeg.org/) on `PATH` (`winget install Gyan.FFmpeg`); the CLIP weights (~600 MB)
download on the first analysis. Run the tests with `python auto_editor.py --selftest`.

Build the Windows app:

```bash
.venv\Scripts\pip install pyinstaller
.venv\Scripts\python build.py --zip
```

This creates `dist/ClutchClip/ClutchClip.exe` and `dist/ClutchClip-windows.zip`, bundling ffmpeg and the CLIP weights.
`python make_logo.py` redraws the logo and icon in `assets/`. Set `CLUTCHCLIP_HOME` to move the per-user data folder.

### Known limitations

- Windows only; export prefers an AMD hardware encoder (`h264_amf`) and decoding assumes Direct3D 11.
- Moment prompts are tuned for *The Finals*.

## License

MIT. Bundled third-party software is listed in [THIRD_PARTY.md](THIRD_PARTY.md). Not affiliated with Embark Studios or *The Finals*.
