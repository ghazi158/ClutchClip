# ClutchClip

AI-assisted video editor that turns a long gameplay recording into a highlight reel. Built around
*The Finals* recordings, but the detection is prompt-based, so you can teach it other moments.

- **Fight detection**: a CLIP model scores every second of video, combined with audio loudness/burstiness (gunfire).
- **Moments**: tick extra things to keep (deaths, round starts, cashouts) or cut (menus, scoreboards), or add your
  own by describing what's on screen in plain text.
- **Timeline editor**: review AI suggestions, trim, split, reorder, add music with fades, then export.
- **Learn your style** (optional): train a small model on videos you've already edited.

## Requirements

- Windows 10/11 (macOS/Linux are not supported yet: see [Known limitations](#known-limitations))
- Python 3.10+
- [ffmpeg](https://ffmpeg.org/) on `PATH` (`winget install Gyan.FFmpeg`)
- About 600 MB of disk for the CLIP weights, downloaded on first analysis

## Run

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python editor.py [recording-or-project]
```

On Windows you can also double-click `run.bat` or drag a recording onto it.
The first analysis of a recording is slow; results are cached and reopening is instant.

## Train on your own style (optional)

Put videos you've already edited in `%APPDATA%\ClutchClip\training`, then drag one or more *raw* recordings onto
`train.bat` (or run `python auto_editor.py --train raw1.mkv raw2.mkv`). The editor uses the resulting model automatically.

## Where your data lives

Everything user-specific is in `%APPDATA%\ClutchClip` (override with the `CLUTCHCLIP_HOME` environment variable):
`cache/`, `moments.json`, `shortcuts.json`, `style_model.npz`, `training/`.
Projects are saved as `.aeproj` files wherever you choose.

## Development

```bash
python auto_editor.py --selftest
```

## Known limitations

- Export uses an AMD hardware encoder (`h264_amf`) and falls back to CPU x264; decoding assumes Direct3D 11 (Windows).
- Moment prompts are tuned for *The Finals*.

## License

MIT. Not affiliated with Embark Studios or *The Finals*.
