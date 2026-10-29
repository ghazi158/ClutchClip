"""Backend for the editor: AI scoring of a recording, timeline edit operations, and export."""
import functools
import glob
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import numpy as np

FIGHT_PROMPTS = [
    "an intense first-person shooter gunfight with muzzle flash",
    "a first-person view shooting at an enemy player",
    "explosions and destruction in a first-person shooter",
    "a chaotic team fight in a video game",
]
BORING_PROMPTS = [
    "a video game menu or loadout screen",
    "a scoreboard or end of round screen",
    "walking through an empty corridor in a video game",
    "a loading screen",
    "spectating another player while dead",
    "a calm first-person view of a building with no enemies",
]
# mode include/exclude; keep `before` s of lead-in, up to `during` s of the moment itself (-1 = all), `after` s after it
DEFAULT_MOMENTS = [
    {"name": "Deaths, team wipes & respawn", "mode": "include", "enabled": False, "sensitivity": 50,
     "before": 3, "during": 5, "after": 8,
     "prompts": ["a video game death screen showing who eliminated you",
                 "a red bar at the bottom of the screen with a respawn countdown",
                 "large TEAM WIPED text on screen"]},
    {"name": "Round start", "mode": "include", "enabled": False, "sensitivity": 70,
     "before": 0, "during": 0, "after": 15,  # gameplay starts ~3-8 s after the last team intro
     "prompts": ["three characters posing on a bright colored background with the team name in large letters"]},
    {"name": "Cashouts", "mode": "include", "enabled": False, "sensitivity": 50,
     "before": 5, "during": 6, "after": 3,
     "prompts": ["large CASHOUT text on screen in a video game"]},
]
MOMENT_BACKGROUND = [  # what a moment competes against, so e.g. a scoreboard isn't mistaken for a death
    "first-person view running through a city map in a video game",
    "first-person view aiming a gun in a video game",
    "an intense first-person shooter gunfight",
    "a red screen with a large white logo",
    "a main menu screen with a character and store buttons",
    "large HALFTIME or FINAL ROUND text on screen",
    "a team loadout screen showing weapons and gadgets with a large countdown number",
    "a city name title card over an aerial view",
    "the word READY on a black screen",
    "a tournament bracket screen",
    "a scoreboard table with player names and team colors",
    "a results screen with BEST COMBAT, BEST OBJECTIVE and BEST SUPPORT score labels",
    "a black loading screen with small text",
    "skydiving from high in the sky down onto a city in a video game",
] + [p for m in DEFAULT_MOMENTS for p in m["prompts"]]
MOMENT_MERGE_GAP = 10  # detections closer than this are one moment (a round intro is several screens)
FRAME_W, FRAME_H = 398, 224  # 16:9; CLIP's preprocess center-crops to the crosshair area
MERGE_GAP, MIN_LEN, PAD_BEFORE, PAD_AFTER, SMOOTH = 8, 5, 3, 3, 5  # seconds
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
RES = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))  # bundled ffmpeg/weights/logo when frozen
CLIP_WEIGHTS = os.path.join(RES, "model", "open_clip_model.safetensors")
# per-user state (cache, settings); CLUTCHCLIP_HOME overrides it
APP_DIR = os.environ.get("CLUTCHCLIP_HOME") or os.path.join(
    os.environ.get("APPDATA") or os.path.expanduser("~/.local/share"), "ClutchClip")
CACHE_DIR = os.path.join(APP_DIR, "cache")
os.makedirs(CACHE_DIR, exist_ok=True)


def find_tool(name):
    # bundled copy first; winget updates PATH only for new shells, so fall back to its install folder
    bundled = os.path.join(RES, "ffmpeg", f"{name}.exe")
    path = (bundled if os.path.exists(bundled) else None) or shutil.which(name) or next(iter(glob.glob(os.path.expandvars(
        rf"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\*\bin\{name}.exe"))), None)
    if not path:
        raise RuntimeError(f"{name} not found. Install it with: winget install Gyan.FFmpeg")
    return path


class Cancelled(RuntimeError):
    """The user cancelled an export."""


class FFmpegError(subprocess.CalledProcessError):
    """Still a CalledProcessError (callers fall back on it), but the message says what ffmpeg complained about."""

    def __str__(self):
        return f"{os.path.basename(self.cmd[0])} failed: {(self.stderr or b'').decode(errors='replace')[-500:].strip()}"


def run(args, cancel=lambda: False):
    """Run a tool to completion and return its CompletedProcess. If cancel() turns true the process is killed."""
    # stdin=DEVNULL: a windowed .exe has no console stdin to inherit, which makes Popen fail with "invalid handle"
    proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=NO_WINDOW)
    while True:
        try:
            out, err = proc.communicate(timeout=0.25)
            break
        except subprocess.TimeoutExpired:
            if cancel():
                proc.kill()
                proc.communicate()
                raise Cancelled("Export cancelled.") from None
    if proc.returncode:
        raise FFmpegError(proc.returncode, args, out, err)
    return subprocess.CompletedProcess(args, 0, out, err)


def duration_of(video):
    out = run([find_tool("ffprobe"), "-v", "error", "-show_entries", "format=duration",
               "-of", "csv=p=0", video]).stdout
    return float(out)


def rank(x):
    # percentile rank 0..1, so scores adapt to each recording's volume/mix
    return np.argsort(np.argsort(x)) / max(len(x) - 1, 1)


def cache_path(video, kind=""):
    return os.path.join(CACHE_DIR, f"{os.path.basename(video)}_{os.path.getsize(video)}{kind}.npy")


def audio_features(pcm, seconds):
    pcm = pcm.astype(np.float32) / 32768
    win = 16000 // 20  # 50 ms windows, 20 per second
    rms = np.sqrt(np.mean(pcm[: len(pcm) // win * win].reshape(-1, win) ** 2, axis=1))
    rms = np.pad(rms, (0, max(0, seconds * 20 - len(rms))))[: seconds * 20].reshape(seconds, 20)
    loud = np.percentile(rms, 90, axis=1)  # gunfire = loud
    spiky = rms.std(axis=1)                # gunfire = bursty, unlike music/ambience
    return 0.5 * rank(loud) + 0.5 * rank(spiky)


def audio_scores(video, seconds):
    """Audio-only pass, for recordings whose embeddings were cached before scan() existed."""
    raw = run([find_tool("ffmpeg"), "-v", "error", "-i", video, "-map", "0:a:0", "-ac", "1",
               "-ar", "16000", "-f", "s16le", "-"]).stdout
    return audio_features(np.frombuffer(raw, np.int16), seconds)


@functools.cache
def load_clip():
    import open_clip
    model, _, preprocess = open_clip.create_model_and_transforms(
        "ViT-B-32", pretrained=CLIP_WEIGHTS if os.path.exists(CLIP_WEIGHTS) else "laion2b_s34b_b79k")
    return model.eval(), preprocess


def scan(video, progress=lambda p: None):
    """One read of the recording -> one CLIP embedding per second + the audio score, both cached.
    Audio and video come out of the same pass (on a hard drive the read is the floor), frames are
    decoded on the GPU, and a reader thread keeps the disk busy while CLIP runs.
    (Decoding only keyframes is ~2x faster but missed most cashouts/round starts, so every frame is decoded.)"""
    import torch
    from PIL import Image

    seconds = int(duration_of(video))
    model, preprocess = load_clip()
    with tempfile.TemporaryDirectory() as tmp:
        audio_raw = os.path.join(tmp, "audio.raw")
        proc = subprocess.Popen(
            [find_tool("ffmpeg"), "-v", "error", "-hwaccel", "d3d11va",
             "-i", video, "-map", "0:v:0", "-vf", f"fps=1,scale={FRAME_W}:{FRAME_H}", "-f", "rawvideo",
             "-pix_fmt", "rgb24", "pipe:1", "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-f", "s16le", audio_raw],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
        frames = queue.Queue(maxsize=256)  # reader thread keeps the disk busy while CLIP runs

        def reader():
            size = FRAME_W * FRAME_H * 3
            while len(buf := proc.stdout.read(size)) == size:
                frames.put(buf)
            frames.put(None)
        threading.Thread(target=reader, daemon=True).start()

        embs, batch, done = [], [], 0

        def flush():
            with torch.no_grad():
                img = model.encode_image(torch.stack(batch))
            embs.append((img / img.norm(dim=-1, keepdim=True)).numpy())
            batch.clear()

        while (buf := frames.get()) is not None:
            batch.append(preprocess(Image.fromarray(np.frombuffer(buf, np.uint8).reshape(FRAME_H, FRAME_W, 3))))
            if len(batch) == 32:
                flush()
            done += 1
            progress(min(done / seconds, 1))
        if batch:
            flush()
        if proc.wait():
            raise RuntimeError("ffmpeg couldn't read the recording (does it have an audio track?)")
        emb = np.concatenate(embs)
        audio = audio_features(np.fromfile(audio_raw, np.int16), seconds)
    np.save(cache_path(video), emb)
    np.save(cache_path(video, "_audio"), audio)
    return emb, audio


def clip_embeddings(video, progress=lambda p: None):
    """One normalised CLIP embedding per second of video, cached in ./cache."""
    if os.path.exists(cache_path(video)):
        return np.load(cache_path(video))
    return scan(video, progress)[0]


def vision_scores(video, seconds, progress):
    emb = clip_embeddings(video, progress)
    import open_clip
    import torch
    model, _ = load_clip()
    with torch.no_grad():
        text = model.encode_text(open_clip.get_tokenizer("ViT-B-32")(FIGHT_PROMPTS + BORING_PROMPTS))
    text = (text / text.norm(dim=-1, keepdim=True)).numpy()
    logits = 100 * emb @ text.T
    probs = np.exp(logits - logits.max(axis=1, keepdims=True))
    s = probs[:, : len(FIGHT_PROMPTS)].sum(axis=1) / probs.sum(axis=1)
    return np.concatenate([s, np.zeros(seconds)])[:seconds]


@functools.cache
def text_embedding(prompt):
    import open_clip
    import torch
    model, _ = load_clip()
    with torch.no_grad():
        t = model.encode_text(open_clip.get_tokenizer("ViT-B-32")([prompt]))[0]
    return (t / t.norm()).numpy()


def moment_prob(emb, prompts, competing=()):
    """Per-second probability that a frame shows one of `prompts` rather than anything else we know about."""
    others = [p for p in dict.fromkeys(MOMENT_BACKGROUND + list(competing)) if p not in prompts]
    text = np.stack([text_embedding(p) for p in list(prompts) + others])
    logits = 100 * emb @ text.T
    probs = np.exp(logits - logits.max(axis=1, keepdims=True))
    p = probs[:, : len(prompts)].sum(axis=1) / probs.sum(axis=1)
    return np.convolve(p, np.ones(3) / 3, mode="same")


def moment_runs(prob, sensitivity):
    """(start, end) seconds where prob clears the threshold; nearby runs merged."""
    hit = np.r_[prob >= 1 - sensitivity / 100, False]
    runs, start = [], None
    for i, h in enumerate(hit):
        if h and start is None:
            start = i
        elif not h and start is not None:
            if runs and start - runs[-1][1] <= MOMENT_MERGE_GAP:
                runs[-1] = (runs[-1][0], i)
            else:
                runs.append((start, i))
            start = None
    return runs


def moment_ranges(runs, duration, before, during, after):
    """Kept ranges per detected moment: lead-in, up to `during` s of it (-1 = all), and the aftermath."""
    out = []
    for s, e in runs:
        shown = e if during < 0 else min(e, s + during)
        if before or shown > s:
            out.append((max(0.0, s - before), shown))
        if after:
            out.append((e, min(duration, e + after)))
    return [r for r in out if r[1] - r[0] >= 1]


def union(ranges):
    """Merge overlapping (s, e, labels) ranges, combining their labels."""
    out = []
    for s, e, labels in sorted(ranges):
        if out and s <= out[-1][1] + 0.5:
            out[-1] = (out[-1][0], max(out[-1][1], e), out[-1][2] | labels)
        else:
            out.append((s, e, set(labels)))
    return out


def subtract(ranges, cuts):
    """Remove cut intervals from (s, e, labels) ranges; pieces under 2 s are dropped."""
    for c0, c1 in cuts:
        ranges = [piece for s, e, lb in ranges
                  for piece in ((s, min(e, c0), lb), (max(s, c1), e, lb)) if piece[1] - piece[0] >= 2]
    return ranges


def ai_ranges(video, score, duration, keep_percent, moments, fights=True):
    """AI suggestions as (start, end, why): fights (keep %) plus enabled include-moments, minus
    enabled exclude-moments. Also returns how many times each moment was found (enabled or not)."""
    ranges = [(s, e, {"Fight"}) for s, e in suggest(score, duration, keep_percent)] if fights else []
    cuts, found = [], []
    emb = clip_embeddings(video) if moments else None
    competing = [p for m in moments for p in m["prompts"]]
    for m in moments:
        runs = moment_runs(moment_prob(emb, m["prompts"], competing), m["sensitivity"]) if m["prompts"] else []
        found.append(len(runs))
        if not m["enabled"]:
            continue
        if m["mode"] == "exclude":
            cuts += [(max(0.0, s - m["before"]), e + m["after"]) for s, e in runs]
        else:
            ranges += [(s, e, {m["name"]}) for s, e in
                       moment_ranges(runs, duration, m["before"], m["during"], m["after"])]
    ranges = subtract(union(ranges), cuts)
    return [(s, e, " + ".join(sorted(lb))) for s, e, lb in union(ranges)], found


def find_segments(keep, duration):
    """Boolean per-second mask -> padded (start, end) pairs in seconds."""
    runs, start = [], None
    for i, k in enumerate(list(keep) + [False]):
        if k and start is None:
            start = i
        elif not k and start is not None:
            runs.append([start, i])
            start = None
    merged = []
    for r in runs:
        if merged and r[0] - merged[-1][1] <= MERGE_GAP:
            merged[-1][1] = r[1]
        else:
            merged.append(r)
    out = []
    for s, e in merged:
        if e - s < MIN_LEN:
            continue
        s, e = max(0, s - PAD_BEFORE), min(duration, e + PAD_AFTER)
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], e)
        else:
            out.append((s, e))
    return out


def analyze(video, progress=lambda p: None):
    """Smoothed per-second AI keep score (~0..1) for the whole recording, cached in ./cache."""
    cache = os.path.join(CACHE_DIR, f"{os.path.basename(video)}_{os.path.getsize(video)}_score.npy")
    if os.path.exists(cache):
        return np.load(cache)
    seconds = int(duration_of(video))
    if not os.path.exists(cache_path(video, "_audio")):
        if os.path.exists(cache_path(video)):  # embeddings cached by an older version: audio-only pass
            try:
                audio = audio_scores(video, seconds)
            except subprocess.CalledProcessError:  # no audio track
                audio = np.zeros(seconds)
            np.save(cache_path(video, "_audio"), audio)
        else:
            scan(video, progress)
    audio = np.load(cache_path(video, "_audio"))
    vision = vision_scores(video, seconds, progress)
    score = 0.5 * audio + 0.5 * vision
    score = np.convolve(score, np.ones(SMOOTH) / SMOOTH, mode="same")
    np.save(cache, score)
    return score


def suggest(score, duration, keep_percent):
    return find_segments(score >= np.quantile(score, 1 - keep_percent / 100), duration)


def fmt(t):
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


# --- timeline operations on the project dict (clips play back to back, in list order) ---

def new_clip(a, b, status, why=""):
    return {"id": uuid.uuid4().hex[:8], "in": float(a), "out": float(b), "status": status, "volume": 1.0,
            "why": why}


def starts(clips):
    return list(np.cumsum([0] + [c["out"] - c["in"] for c in clips[:-1]])) if clips else []


def seq_length(clips):
    return sum(c["out"] - c["in"] for c in clips)


def locate(clips, t):
    """Timeline time -> (clip index, source time), clamped to the timeline."""
    for i, c in enumerate(clips):
        d = c["out"] - c["in"]
        if t < d or i == len(clips) - 1:
            return i, c["in"] + min(max(t, 0), d)
        t -= d


def split_clip(clips, i, s):
    c = clips[i]
    if not c["in"] + 0.05 < s < c["out"] - 0.05:
        return False
    clips[i:i + 1] = [{**c, "out": s}, {**c, "id": uuid.uuid4().hex[:8], "in": s}]
    return True


def insert_by_source(clips, clip):
    """Insert so the timeline stays in recording order around it; returns the index."""
    i = next((j for j, c in enumerate(clips) if c["in"] > clip["in"]), len(clips))
    clips.insert(i, clip)
    return i


def resuggest(clips, ranges):
    """Replace untouched AI suggestions with `ranges` (start, end, why); accepted/edited/manual clips stay put."""
    kept = [c for c in clips if c["status"] != "suggested"]
    for s, e, why in ranges:
        if not any(c["in"] < e and s < c["out"] for c in kept):
            insert_by_source(kept, new_clip(s, e, "suggested", why))
    return kept


# --- export ---

def music_filter(music):
    """filter_complex mixing music clips (inputs 1..n) under the edited video's audio (input 0)."""
    chains = []
    for k, m in enumerate(music, 1):
        d = m["out"] - m["in"]
        f = f"[{k}:a]atrim={m['in']:.3f}:{m['out']:.3f},asetpts=PTS-STARTPTS,volume={m['volume']:.3f}"
        if m["fade_in"] > 0:
            f += f",afade=t=in:d={m['fade_in']:.3f}"
        if m["fade_out"] > 0:
            f += f",afade=t=out:st={max(0, d - m['fade_out']):.3f}:d={m['fade_out']:.3f}"
        chains.append(f + f",adelay={int(m['start'] * 1000)}:all=1[m{k}]")
    labels = "[0:a]" + "".join(f"[m{k}]" for k in range(1, len(music) + 1))
    return ";".join(chains + [f"{labels}amix=inputs={len(music) + 1}:duration=first:normalize=0[a]"])


SIZE_MBPS = {"high": 24, "standard": 16, "small": 10}  # H.264 at 1080p60; YouTube suggests ~12


def video_mbps(size, height, fps):
    """Target bitrate, scaled from the 1080p60 figure by pixel count and frame rate."""
    return SIZE_MBPS[size] * (height / 1080) ** 2 * (fps / 60) ** 0.75


def encoder_args(encoder, mbps):
    rate = ["-b:v", f"{mbps:.1f}M", "-maxrate", f"{mbps * 1.5:.1f}M", "-bufsize", f"{mbps * 2:.1f}M"]
    if encoder == "h264_amf":
        return ["-c:v", "h264_amf", "-quality", "balanced", "-rc", "vbr_peak", *rate]
    return ["-c:v", "libx264", "-preset", "veryfast", *rate]


def probe_video(src):
    """(codec, height, fps) of the first video stream."""
    out = run([find_tool("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries",
               "stream=codec_name,height,r_frame_rate", "-of", "csv=p=0", src]).stdout.decode().strip()
    codec, height, rate = out.split(",")[:3]
    num, den = rate.split("/")
    return codec, int(height), round(int(num) / int(den))


def render(project, out, settings, progress=lambda p: None, cancel=lambda: False):
    """settings: height (None = source), fps, size ('high'/'standard'/'small'), encoder ('h264_amf'/'libx264').
    Re-encoding on the GPU beats copying the original video here: OBS files are ~600 MB/min, so a copy is
    both bigger (7.4 GB vs 1.3 GB for a 10 min edit) and slower to write than a 16 Mbps re-encode."""
    ffmpeg, clips, src = find_tool("ffmpeg"), project["clips"], project["source"]
    if not clips:
        raise RuntimeError("The timeline is empty.")
    _, src_h, src_fps = probe_video(src)
    music = project["music"]
    height, fps = settings.get("height") or src_h, settings.get("fps") or src_fps
    total, done, state = seq_length(clips), [0.0], {"enc": settings.get("encoder", "h264_amf")}
    used = {}  # part number -> encoder that made it
    audio = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]

    def cut(n, c, part, force=None):
        head = [ffmpeg, "-y", "-v", "error"]
        span = ["-ss", f"{c['in']:.3f}", "-i", src, "-t", f"{c['out'] - c['in']:.3f}", "-map", "0:v:0", "-map", "0:a:0?"]
        tail = ["-af", f"volume={c['volume']:.3f}", *audio, part]
        while True:
            enc = force or state["enc"]
            vf = [f"fps={fps}"] if fps != src_fps else []
            if enc == "h264_amf" and height == src_h:  # decode -> encode without leaving the GPU
                cmd = [*head, "-hwaccel", "d3d11va", "-hwaccel_output_format", "d3d11", *span,
                       *(["-vf", ",".join(vf)] if vf else [])]
            else:  # GPU decode, resize on the CPU (scale_d3d11 fails on this driver)
                cmd = [*head, "-hwaccel", "d3d11va", *span,
                       "-vf", ",".join(vf + [f"scale=-2:{height}", "format=yuv420p"])]
            try:
                run([*cmd, *encoder_args(enc, video_mbps(settings["size"], height, fps)), *tail], cancel)
                used[n] = enc
                return
            except subprocess.CalledProcessError:
                if enc == "libx264":
                    raise
                state["enc"] = "libx264"  # AMD encoder unavailable: fall back to CPU for the rest

    with tempfile.TemporaryDirectory(dir=os.path.dirname(os.path.abspath(out))) as tmp:
        parts = [os.path.join(tmp, f"{n:04d}.mp4") for n in range(len(clips))]

        def job(n):
            if cancel():
                raise Cancelled("Export cancelled.")
            cut(n, clips[n], parts[n])
            done[0] += clips[n]["out"] - clips[n]["in"]
            progress(0.95 * done[0] / total)

        # 2 clips at a time keeps the GPU decoder busy while the other clip seeks on the drive
        with ThreadPoolExecutor(2) as pool:
            list(pool.map(job, range(len(clips))))
        if len(set(used.values())) > 1:  # the GPU encoder failed midway: joining mixed encodes can break, so redo them
            for n in [n for n, enc in used.items() if enc != "libx264"]:
                cut(n, clips[n], parts[n], force="libx264")

        listfile = os.path.join(tmp, "list.txt")
        with open(listfile, "w") as f:
            f.writelines(f"file '{os.path.basename(p)}'\n" for p in parts)
        joined = os.path.join(tmp, "joined.mp4")
        run([ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", listfile, "-c", "copy", joined], cancel)
        if music:
            inputs = [x for m in music for x in ("-i", m["path"])]
            run([ffmpeg, "-y", "-v", "error", "-i", joined, *inputs,
                 "-filter_complex", music_filter(music), "-map", "0:v", "-map", "[a]",
                 "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out], cancel)
        else:
            run([ffmpeg, "-y", "-v", "error", "-i", joined, "-c", "copy", "-movflags", "+faststart", out], cancel)
    progress(1)


def selftest():
    keep = [False] * 100
    for i in list(range(10, 20)) + list(range(25, 30)) + list(range(50, 52)) + list(range(70, 80)):
        keep[i] = True
    # 10-20 and 25-30 merge (gap 5), 50-52 too short, 70-80 kept; padded 3s each side
    assert find_segments(keep, 100) == [(7, 33), (67, 83)], find_segments(keep, 100)
    assert find_segments([True] * 10, 10) == [(0, 10)]
    assert find_segments([False] * 10, 10) == []

    clips = [new_clip(10, 20, "suggested"), new_clip(50, 55, "accepted")]
    assert starts(clips) == [0, 10] and seq_length(clips) == 15
    assert locate(clips, 3) == (0, 13) and locate(clips, 12) == (1, 52) and locate(clips, 99) == (1, 55)
    assert split_clip(clips, 0, 14) and [(c["in"], c["out"]) for c in clips] == [(10, 14), (14, 20), (50, 55)]
    assert not split_clip(clips, 0, 10.01)  # too close to an edge
    assert insert_by_source(clips, new_clip(30, 35, "manual")) == 2

    out = resuggest([new_clip(0, 5, "suggested"), new_clip(60, 62, "manual")], [(47, 73, "Fight")])
    assert [c["status"] for c in out] == ["manual"], out  # 47-73 overlaps the manual clip, old suggestion dropped
    out = resuggest([new_clip(0, 5, "accepted")], [(47, 73, "Fight")])
    assert [(c["in"], c["out"], c["why"]) for c in out] == [(0, 5, ""), (47, 73, "Fight")], out

    # moments: runs 10-14 and 20-22 merge (gap 6 <= 10); 50-53 separate
    prob = np.zeros(100)
    prob[[10, 11, 12, 13, 20, 21, 50, 51, 52]] = 0.9
    runs = moment_runs(prob, 50)
    assert runs == [(10, 22), (50, 53)], runs
    # round-start style: skip the screens, keep 10 s after; death style: 3 s before, 5 s of it
    assert moment_ranges(runs, 100, 0, 0, 10) == [(22, 32), (53, 63)]
    assert moment_ranges(runs, 100, 3, 5, 0) == [(7, 15), (47, 53)]
    assert moment_ranges([(95, 99)], 100, 0, -1, 10) == [(95, 99), (99, 100)]
    assert union([(0, 10, {"Fight"}), (8, 20, {"Round start"}), (30, 40, {"Fight"})]) == \
        [(0, 20, {"Fight", "Round start"}), (30, 40, {"Fight"})]
    assert subtract([(0, 20, {"Fight"})], [(5, 6), (18, 30)]) == [(0, 5, {"Fight"}), (6, 18, {"Fight"})]

    assert video_mbps("standard", 1080, 60) == 16 and round(video_mbps("standard", 720, 60), 1) == 7.1

    f = music_filter([{"path": "m.mp3", "start": 2, "in": 1, "out": 11, "volume": 0.5, "fade_in": 1, "fade_out": 2}])
    assert "atrim=1.000:11.000" in f and "afade=t=out:st=8.000:d=2.000" in f and "adelay=2000:all=1" in f
    assert f.endswith("[0:a][m1]amix=inputs=2:duration=first:normalize=0[a]"), f
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        print("Run editor.py (or run.bat) to open the editor. Flag here: --selftest")
