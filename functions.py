"""Sampling, clipping, label checks and export for the frog labeling workflow.

Collaborator (notebooks/03_frog_sampling.ipynb):
    load_config -> sample (inventory, segments, clips) -> annotate
    -> add_round (only if needed) -> export_source

Sampling, per site and round: the site's nights with audio inside HOURS and MONTHS are
split into nights_per_site equal groups in date order, and one night is drawn from each.
Each night's eligible audio, pooled across its files, is split into segments_per_night
equal strata with one random clip in each, at least min_gap_seconds from any other clip.
Later rounds use unsampled nights first. A night runs from night_starts_at to the same
hour the next day; a file with no time in its name is its own night.

Project lead:
    ingest_source (one call per received zip) -> build_dataset (one merged, labeled table)

Everything made for a dataset goes in exports/<source>/ (segments, clips, labels, settings),
so exporting is just zipping that folder. Every path is relative to the repo folder; the
notebooks change into it first.
"""
from __future__ import annotations

import re
import shutil
import tempfile
import zipfile
import zlib
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import soundfile as sf
import yaml

DESIGN_YAML = Path("frogs.yaml")  # sampling design, set by the project lead
ANNOTATOR_YAML = "annotator_config/config/frogs.yaml"  # views, instructions and form; paths are set in annotate()
EXPORTS_DIR = Path("exports")  # one folder per SOURCE; the audio folder is only ever read

# File names inside exports/<source>/
INVENTORY_CSV = "inventory.csv"
SEGMENTS_CSV = "segments.csv"  # the annotator loads this file
LOCK_YAML = "source.yaml"
CLIP_DIR = "clips"
LABEL_DIR = "labels"  # the annotator writes frog_labels_<initials>.csv here

AUDIO_EXTENSIONS = {".wav", ".flac", ".mp3", ".ogg"}
SEP = "__"  # joins the parts of an ID: source__site__recording__s000000
WINDOW_SECONDS = 3.0
HIGH_RES_SUBTYPES = {"PCM_24", "PCM_32", "FLOAT", "DOUBLE"}

DESIGN_KEYS = ["nights_per_site", "segments_per_night", "segment_seconds", "min_gap_seconds",
               "night_starts_at", "max_segments_per_site", "seed"]
# Settings that decide which segments exist. They can't change once sampling has started.
LOCKED_KEYS = ["source", "path_pattern", "recording_time", "sites", "hours", "months",
               "segments_per_night", "segment_seconds", "min_gap_seconds", "night_starts_at", "seed"]

SEGMENT_COLUMNS = ["seq", "id", "path", "start_time", "end_time", "source", "site", "recording",
                   "source_path", "offset_s", "local_time", "night", "stratum", "sample_rate", "round"]
LABEL_COLUMNS = ["frog_in_window", "window_start", "window_end", "species_1", "clarity",
                 "species_2", "clarity_2", "species_3", "clarity_3",
                 "insects", "birds_mammals", "rain", "wind", "water", "human", "handling",
                 "unsure_reason", "notes"]


# ─── Config ──────────────────────────────────────────────────────────────────

def load_config(source: str, contributor: str, audio_dir: str, path_pattern: str,
                time_format: str | None, file_timezone: str, local_timezone: str,
                sites: list | None = None, hours: list | None = None, months: list | None = None) -> dict:
    """Combine the notebook's step 1 settings with the sampling design in frogs.yaml.

    Stops on anything invalid, saying where to fix it.
    """
    design = yaml.safe_load(DESIGN_YAML.read_text()) or {}

    errors = []  # in the notebook
    if not source or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", str(source)):
        errors.append("SOURCE: use lowercase letters, digits and hyphens, e.g. 'ucb-ponds'")
    if not str(contributor or "").strip():
        errors.append("CONTRIBUTOR: enter your name")
    if not Path(audio_dir).is_dir():
        errors.append(f"AUDIO_DIR: folder not found: {Path(audio_dir).resolve()}")
    if "{site}" not in str(path_pattern):
        errors.append("PATH_PATTERN: must contain {site}")
    for name, tz in (("FILE_TIMEZONE", file_timezone), ("LOCAL_TIMEZONE", local_timezone)):
        if name == "FILE_TIMEZONE" and str(tz).lower() == "local":
            continue
        try:
            pd.Timestamp(0).tz_localize(tz or "")
        except Exception:
            errors.append(f"{name}: use a time zone name such as 'America/Los_Angeles' (got {tz!r})")
    for name, values, lo, hi in (("HOURS", hours, 0, 23), ("MONTHS", months, 1, 12)):
        if values is not None and not (isinstance(values, (list, tuple)) and len(values)
                                       and all(isinstance(v, int) and lo <= v <= hi for v in values)):
            errors.append(f"{name}: use a list of whole numbers from {lo} to {hi}, or None for all")
    if sites is not None and not (isinstance(sites, (list, tuple)) and len(sites)
                                  and all(isinstance(s, str) and s.strip() for s in sites)):
        errors.append('SITES: use a list of site names, e.g. ["POND3", "POND7"], or None for all')

    design_errors = []  # in frogs.yaml
    missing = [k for k in DESIGN_KEYS if k not in design]
    if missing:
        design_errors.append("missing: " + ", ".join(missing))
    else:
        for key in ("nights_per_site", "segments_per_night", "segment_seconds", "max_segments_per_site"):
            if not isinstance(design[key], int) or design[key] < 1:
                design_errors.append(f"{key}: must be a whole number of 1 or more")
        if not isinstance(design["min_gap_seconds"], (int, float)) or design["min_gap_seconds"] < 0:
            design_errors.append("min_gap_seconds: must be 0 or more")
        if not isinstance(design["night_starts_at"], int) or not 0 <= design["night_starts_at"] <= 23:
            design_errors.append("night_starts_at: must be a whole hour from 0 to 23")
        if design["segment_seconds"] < WINDOW_SECONDS:
            design_errors.append(f"segment_seconds: must be at least {WINDOW_SECONDS:g}")

    msg = []
    if errors:
        msg.append("Fix these settings in step 1 of the notebook:\n  " + "\n  ".join(errors))
    if design_errors:
        msg.append(f"Fix these settings in {DESIGN_YAML}:\n  " + "\n  ".join(design_errors))
    if msg:
        raise ValueError("\n".join(msg))
    return {**design, "source": source, "contributor": contributor, "audio_dir": audio_dir,
            "path_pattern": path_pattern,
            "recording_time": {"format": time_format or None, "file_timezone": file_timezone,
                               "local_timezone": local_timezone},
            "sites": sorted({_clean(s) for s in sites}) if sites else None,  # sorted, so the lock compares sets
            "hours": sorted(set(hours)) if hours else None,
            "months": sorted(set(months)) if months else None}


def _dir(cfg: dict) -> Path:
    """exports/<source>/: everything the workflow makes for this SOURCE."""
    return EXPORTS_DIR / cfg["source"]


def _label_files(cfg: dict) -> list:
    return sorted((_dir(cfg) / LABEL_DIR).glob("*.csv"))


def _check_lock(cfg: dict, have_segments: bool) -> None:
    """Refuse to sample if a locked setting changed after the first round."""
    locked = {k: cfg[k] for k in LOCKED_KEYS}
    lock = _dir(cfg) / LOCK_YAML
    if have_segments and lock.exists():
        stored = (yaml.safe_load(lock.read_text()) or {}).get("locked", {})
        changed = [k for k in LOCKED_KEYS if k in stored and stored[k] != locked[k]]
        if changed:
            raise ValueError(
                f"These settings changed after sampling started: {', '.join(changed)}.\n"
                f"Change them back to the values in {lock}, or use a new SOURCE to start over.")
        return
    (_dir(cfg) / LABEL_DIR).mkdir(parents=True, exist_ok=True)
    lock.write_text(yaml.safe_dump(
        {"source": cfg["source"], "contributor": cfg["contributor"],
         "created": date.today().isoformat(), "locked": locked}, sort_keys=False))


# ─── Inventory ───────────────────────────────────────────────────────────────

def _clean(text: str) -> str:
    """Make text safe for IDs and file names: letters, digits, - and _ only, never '__'."""
    text = re.sub(r"[^A-Za-z0-9_-]", "-", str(text).strip())
    return re.sub(r"_{2,}", "_", text).strip("_") or "x"


def _pattern_regex(pattern: str) -> re.Pattern:
    """'{site}/{_}' -> regex. {_} skips a part; other {names} become columns."""
    out, names = "", set()
    for piece in re.split(r"(\{\w*\})", pattern.strip("/")):
        token = re.fullmatch(r"\{(\w*)\}", piece)
        if not token:
            out += re.escape(piece)
        elif token.group(1) in ("", "_"):
            out += r"[^/]*?"
        else:
            name = token.group(1)
            if name in names:
                raise ValueError(f"path_pattern: {{{name}}} appears twice")
            names.add(name)
            out += rf"(?P<{name}>[^/]+?)"
    return re.compile(out)


def _time_regex(fmt: str) -> re.Pattern:
    widths = {"%Y": r"\d{4}", "%y": r"\d{2}", "%m": r"\d{2}", "%d": r"\d{2}",
              "%H": r"\d{2}", "%M": r"\d{2}", "%S": r"\d{2}", "%j": r"\d{3}"}
    parts = re.split(r"(%[a-zA-Z])", fmt)
    return re.compile("".join(widths.get(p, re.escape(p)) for p in parts))


def _parse_time(stem: str, fmt: str | None, rx: re.Pattern | None):
    if not fmt:
        return pd.NaT
    m = rx.search(stem)
    if not m:
        return pd.NaT
    try:
        return pd.Timestamp(datetime.strptime(m.group(0), fmt))
    except ValueError:
        return pd.NaT


def build_inventory(cfg: dict) -> pd.DataFrame:
    """List every audio file at the chosen SITES and read site and local start time from its path."""
    root = Path(cfg["audio_dir"])
    rx = _pattern_regex(cfg["path_pattern"])
    tcfg = cfg["recording_time"]
    trx = _time_regex(tcfg["format"]) if tcfg["format"] else None

    rows, unreadable = [], []
    for path in sorted(root.rglob("*")):
        if path.suffix.lower() not in AUDIO_EXTENSIONS or path.name.startswith("."):
            continue  # not audio, or a hidden macOS "._" copy
        rel = path.relative_to(root)
        try:
            info = sf.info(path)
        except Exception:
            unreadable.append(rel.as_posix())
            continue
        if info.duration <= 0:
            unreadable.append(rel.as_posix())
            continue
        m = rx.fullmatch(rel.with_suffix("").as_posix())
        fields = dict(m.groupdict()) if m else {"site": rel.parts[0] if len(rel.parts) > 1 else "unknown"}
        rows.append({
            "source_path": rel.as_posix(),
            "site": _clean(fields.pop("site")),
            "recording": _clean(path.stem),
            "folder": _clean(path.parent.name),
            "start_file_time": _parse_time(path.stem, tcfg["format"], trx),
            "duration_s": round(info.duration, 3),
            "sample_rate": info.samplerate,
            "channels": info.channels,
            "subtype": info.subtype,
            "pattern_ok": bool(m),
            **{k: _clean(v) for k, v in fields.items()},
        })
    if not rows:
        raise FileNotFoundError(f"No readable audio files in {root.resolve()}")
    inv = pd.DataFrame(rows)

    # Two recorders at one site can write the same file name: add the folder name in front.
    dup = inv.duplicated(["site", "recording"], keep=False)
    inv.loc[dup, "recording"] = inv.loc[dup, "folder"] + "-" + inv.loc[dup, "recording"]
    still = inv[inv.duplicated(["site", "recording"], keep=False)]
    if len(still):
        raise ValueError("These files would get the same recording ID:\n"
                         + "\n".join(still.source_path.head(10)))

    if cfg["sites"]:
        unknown = sorted(set(cfg["sites"]) - set(inv.site))
        if unknown:
            raise ValueError(f"SITES: not found in AUDIO_DIR: {', '.join(unknown)}. "
                             f"Sites found: {', '.join(sorted(inv.site.unique()))}")
        print(f"Sampling {len(cfg['sites'])} of {inv.site.nunique()} sites (SITES)")
        inv = inv[inv.site.isin(cfg["sites"])]

    # Recorder clock -> local time
    t = pd.to_datetime(inv.start_file_time)
    if t.notna().any():
        local_tz = tcfg["local_timezone"]
        file_tz = local_tz if str(tcfg["file_timezone"]).lower() == "local" else tcfg["file_timezone"]
        t = (t.dt.tz_localize(file_tz, ambiguous="NaT", nonexistent="shift_forward")
             .dt.tz_convert(local_tz).dt.tz_localize(None))
    inv = inv.assign(start_local=t).drop(columns=["start_file_time"])
    # Time order within each site; recordings with no time follow, in file-name order
    inv = inv.sort_values(["site", "start_local", "source_path"]).reset_index(drop=True)

    _dir(cfg).mkdir(parents=True, exist_ok=True)
    inv.to_csv(_dir(cfg) / INVENTORY_CSV, index=False)

    summary = inv.groupby("site").agg(
        files=("source_path", "size"), hours=("duration_s", lambda d: round(d.sum() / 3600, 1)),
        with_time=("start_local", lambda s: f"{s.notna().mean():.0%}"),
        pattern_ok=("pattern_ok", lambda s: f"{s.mean():.0%}"))
    print(f"{len(inv)} recordings at {inv.site.nunique()} sites -> {_dir(cfg) / INVENTORY_CSV}")
    if unreadable:
        print(f"Skipped {len(unreadable)} unreadable or empty files, e.g. {unreadable[0]}")
    if not inv.pattern_ok.all():
        print(f"{(~inv.pattern_ok).sum()} files don't match path_pattern; their site is the top folder name. "
              f"Example: {inv.loc[~inv.pattern_ok, 'source_path'].iloc[0]}")
    too_short = (inv.duration_s < cfg["segment_seconds"]).sum()
    if too_short:
        print(f"{too_short} files are shorter than {cfg['segment_seconds']} s, so no segments can come from them.")
    if inv.start_local.isna().any():
        note = " HOURS and MONTHS can't apply to them." if cfg["hours"] or cfg["months"] else ""
        print(f"{inv.start_local.isna().sum()} files have no time in their name, so each counts as its own "
              f"night.{note}")
    print(summary.to_string())
    return inv


def _load_inventory(inv: pd.DataFrame) -> pd.DataFrame:
    inv = inv.copy()
    inv["start_local"] = pd.to_datetime(inv.start_local)
    return inv


# ─── Sampling segments ───────────────────────────────────────────────────────

def _existing(cfg: dict) -> pd.DataFrame:
    seg_csv = _dir(cfg) / SEGMENTS_CSV
    return (pd.read_csv(seg_csv, dtype={"night": str}) if seg_csv.exists()
            else pd.DataFrame(columns=SEGMENT_COLUMNS))


def _pick_spread(items: list, k: int, rng: np.random.Generator) -> list:
    """k items from an ordered list: split it into k equal groups and pick one at random from each."""
    if k <= 0:
        return []
    if len(items) <= k:
        return list(items)
    return [items[rng.choice(g)] for g in np.array_split(np.arange(len(items)), k)]


def _secs(t) -> np.ndarray:
    """Local clock times as seconds (NaN for no time), whatever the datetime resolution."""
    return np.asarray((t - pd.Timestamp(0)) / pd.Timedelta(seconds=1), float)


def _far(x: np.ndarray, taken, spacing: float) -> np.ndarray:
    """True where x is at least `spacing` from every value in `taken`."""
    taken = np.sort(np.asarray(taken, float))
    if not len(taken):
        return np.ones(len(x), bool)
    i = np.searchsorted(taken, x)
    return ((np.abs(x - taken[np.maximum(i - 1, 0)]) >= spacing)
            & (np.abs(taken[np.minimum(i, len(taken) - 1)] - x) >= spacing))


def _nights(files: pd.DataFrame, seg_len: float, spacing: float, taken: dict, cfg: dict) -> dict:
    """Every eligible whole-second clip start at one site, by night: {night: [(file index, offsets, times)]}.

    A start is eligible if the clip fits in the file, its local time is inside HOURS and
    MONTHS, and it is at least `spacing` from earlier clips (`taken`). Times are seconds
    on the local clock, so clips in neighbouring files keep their distance too. A file
    with no time is its own night, with times None. Nights come in date order.
    """
    out = {}
    for i, f in enumerate(files.itertuples()):
        o = np.arange(0, int(np.floor(f.duration_s - seg_len)) + 1)  # empty if the file is too short
        if pd.isna(f.start_local):
            o = o[_far(o, taken["offsets"].get(f.recording, []), spacing)]
            if len(o):
                out[f"file-{f.recording}"] = [(i, o, None)]
            continue
        t = f.start_local + pd.to_timedelta(o, unit="s")
        keep = _far(_secs(t), taken["times"], spacing)
        if cfg["hours"]:
            keep &= np.isin(t.hour, cfg["hours"])
        if cfg["months"]:
            keep &= np.isin(t.month, cfg["months"])
        o, t = o[keep], t[keep]
        # A file can run past night_starts_at, so split it where the night changes
        night = (t - pd.Timedelta(hours=cfg["night_starts_at"])).normalize()
        for part in np.split(np.arange(len(o)), np.flatnonzero(np.diff(night.asi8)) + 1):
            if len(part):
                out.setdefault(f"{night[part[0]]:%Y-%m-%d}", []).append((i, o[part], _secs(t[part])))
    return dict(sorted(out.items()))


def _sample_night(pieces: list, n: int, spacing: float, done: list, rng: np.random.Generator) -> list:
    """Up to n clips in one night, as (stratum, file index, offset).

    The night's eligible starts, pooled across its files in time order, are split into
    n equal strata, and one start is drawn at random from each. Each draw rules out the
    starts within `spacing` of it, as do this round's clips in other nights (`done`,
    clock times), so a stratum with nothing left gives no clip.
    """
    fi = np.concatenate([np.full(len(o), i) for i, o, _ in pieces])
    off = np.concatenate([o for _, o, _ in pieces])
    t = np.concatenate([np.full(len(o), np.nan) if ts is None else ts for _, o, ts in pieces])
    free = np.isnan(t) | _far(t, done, spacing)
    chosen = []
    for s, idx in enumerate(np.array_split(np.arange(len(off)), n), start=1):
        idx = idx[free[idx]]
        if not len(idx):
            continue
        c = rng.choice(idx)
        chosen.append((s, int(fi[c]), int(off[c])))
        free &= ~(((fi == fi[c]) & (np.abs(off - off[c]) < spacing)) | (np.abs(t - t[c]) < spacing))
    return chosen


def _check_segments(seg: pd.DataFrame, spacing: float) -> None:
    dups = seg.id[seg.id.duplicated()]
    if len(dups):
        raise ValueError(f"Duplicate segment IDs, e.g. {dups.iloc[0]}")
    for (site, rec), g in seg.groupby(["site", "recording"]):
        d = np.diff(np.sort(g.offset_s.to_numpy(float)))
        if (d < spacing).any():
            raise ValueError(f"Segments closer than {spacing:g} s in {site}/{rec}")
    for site, g in seg.groupby("site"):
        d = pd.to_datetime(g.local_time).dropna().sort_values().diff().dt.total_seconds()
        if (d < spacing).any():
            raise ValueError(f"Segments closer than {spacing:g} s apart in time at {site}")


def _check_labeled(cfg: dict, seg: pd.DataFrame) -> None:
    """Stop if the last round still has unlabeled segments."""
    last = int(seg["round"].max())
    todo = seg[(seg["round"] == last) & ~seg.id.isin(_load_labels(_label_files(cfg)).id)]
    if len(todo):
        raise RuntimeError(f"Round {last} still has {len(todo)} unlabeled segments. Label them in step 2 first.")


def sample_segments(cfg: dict, inv: pd.DataFrame, new_round: bool = False, force: bool = False) -> pd.DataFrame:
    """Sample a round of clips at every site, cut them, and append them to segments.csv.

    Without new_round, an existing segments.csv is returned unchanged, so rerunning
    the notebook never adds segments by accident. With new_round, the previous round
    must be fully labeled first (or force=True).
    """
    d = _dir(cfg)
    seg_old = _existing(cfg)
    _check_lock(cfg, have_segments=len(seg_old) > 0)
    if len(seg_old) and not new_round:
        print(f"Using {len(seg_old)} existing segments from {d / SEGMENTS_CSV} "
              f"(rounds 1-{int(seg_old['round'].max())}).")
        return seg_old
    if len(seg_old) and not force:
        _check_labeled(cfg, seg_old)
    if len(seg_old) and "night" not in seg_old:
        raise RuntimeError(f"{d / SEGMENTS_CSV} was sampled with an older design. Use a new SOURCE to start over.")

    inv = _load_inventory(inv)
    rnd = int(seg_old["round"].max()) + 1 if len(seg_old) else 1
    seg_len, per_night, cap = cfg["segment_seconds"], cfg["segments_per_night"], cfg["max_segments_per_site"]
    spacing = seg_len + cfg["min_gap_seconds"]

    rows, summary, capped = [], [], []
    for site, files in inv.groupby("site"):
        files = files.reset_index(drop=True)  # already in time (or file-name) order
        old = seg_old[seg_old.site == site]
        k = min(cfg["nights_per_site"], (cap - len(old)) // per_night)
        if k <= 0:
            capped.append(site)
            continue
        taken = {"offsets": {r: g.offset_s.astype(int).tolist() for r, g in old.groupby("recording")},
                 "times": _secs(pd.to_datetime(old.local_time).dropna())}
        nights = _nights(files, seg_len, spacing, taken, cfg)
        rng = np.random.default_rng([cfg["seed"], rnd, zlib.crc32(site.encode())])
        # Unsampled nights first; nights from earlier rounds only if there aren't enough
        picked = _pick_spread([n for n in nights if n not in set(old.night)], k, rng)
        picked += _pick_spread([n for n in nights if n in set(old.night)], k - len(picked), rng)
        done, got = [], 0  # clock times of this round's clips at the site
        for night in picked:
            for s, i, off in _sample_night(nights[night], per_night, spacing, done, rng):
                f = files.iloc[i]
                sid = SEP.join([cfg["source"], f.site, f.recording, f"s{off:06d}"])
                t = (f.start_local + pd.Timedelta(seconds=off)) if pd.notna(f.start_local) else pd.NaT
                if pd.notna(t):
                    done.append(_secs(t))
                rows.append({
                    "id": sid, "path": f"{CLIP_DIR}/{f.site}/{sid}.flac",  # relative to exports/<source>/
                    "start_time": 0.0, "end_time": float(seg_len), "source": cfg["source"],
                    "site": f.site, "recording": f.recording, "source_path": f.source_path, "offset_s": off,
                    "local_time": t, "night": night, "stratum": s, "sample_rate": f.sample_rate, "round": rnd})
                got += 1
        summary.append({"site": site, "eligible_nights": len(nights), "nights": len(picked),
                        "segments": got, "asked": k * per_night})
    if capped:
        print(f"Reached max_segments_per_site ({cap}): {', '.join(capped)}")
    new = pd.DataFrame(rows, columns=[c for c in SEGMENT_COLUMNS if c != "seq"])
    if not len(new) and not summary:
        return seg_old  # every site is full
    if not len(new):
        raise RuntimeError("No segments could be sampled. Check SITES, HOURS and MONTHS.")
    new = new.sort_values(["site", "local_time", "source_path", "offset_s"]).reset_index(drop=True)
    start_seq = int(seg_old.seq.max()) + 1 if len(seg_old) else 1
    new.insert(0, "seq", range(start_seq, start_seq + len(new)))
    all_seg = pd.concat([seg_old, new], ignore_index=True) if len(seg_old) else new
    _check_segments(all_seg, spacing)

    # Clips first: if cutting stops halfway, segments.csv is unchanged and a rerun picks the same segments.
    new_src = new.assign(audio_file=[(Path(cfg["audio_dir"]) / p).as_posix() for p in new.source_path],
                         duration_s=float(seg_len), clip_file=[(d / p).as_posix() for p in new.path])
    cut_clips(new_src, src="audio_file", start="offset_s", duration="duration_s", dest="clip_file")
    all_seg.to_csv(d / SEGMENTS_CSV, index=False)

    per_site = pd.DataFrame(summary).set_index("site")
    print(f"Round {rnd}: added {len(new)} segments. {len(all_seg)} segments in {d / SEGMENTS_CSV}.")
    print(per_site.drop(columns="asked").to_string())
    if (per_site.segments < per_site.asked).any():
        print(f"Fewer segments than asked where a site has fewer than {cfg['nights_per_site']} nights with "
              f"audio in HOURS and MONTHS, or a night has too little audio.")
    return all_seg


# ─── Clips ───────────────────────────────────────────────────────────────────

def cut_clips(table: pd.DataFrame, src: str, start: str, duration: str, dest: str,
              overwrite: bool = False) -> None:
    """Cut one clip per row: `duration` seconds of `src` from `start`, saved as FLAC at `dest`.

    Keeps the original sample rate and channels. Existing clips are skipped.
    """
    made = skipped = 0
    for s, t0, dur, out in table[[src, start, duration, dest]].itertuples(index=False):
        out = Path(out)
        if out.exists() and not overwrite:
            skipped += 1
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        with sf.SoundFile(s) as f:
            sr = f.samplerate
            f.seek(int(round(float(t0) * sr)))
            n = int(round(float(dur) * sr))
            data = f.read(n, dtype="float32", always_2d=True)
            subtype = "PCM_24" if f.subtype in HIGH_RES_SUBTYPES else "PCM_16"
        if len(data) < n:
            print(f"Short clip ({len(data) / sr:.2f} s): {out}")
        tmp = out.with_name(out.stem + ".part.flac")
        sf.write(tmp, data, sr, format="FLAC", subtype=subtype)
        tmp.replace(out)
        made += 1
    print(f"Clips: {made} written, {skipped} already there")


# ─── Labels ──────────────────────────────────────────────────────────────────

def _load_labels(files: list) -> pd.DataFrame:
    """These annotator output files, keeping each annotator's latest answer per segment."""
    parts = []
    for p in files:
        try:
            df = pd.read_csv(p, dtype=str)
        except pd.errors.EmptyDataError:
            continue
        if "annotator" not in df:
            df["annotator"] = Path(p).stem.split("_")[-1]
        parts.append(df)
    if not parts:
        return pd.DataFrame(columns=["id", "annotator"] + LABEL_COLUMNS)
    lab = pd.concat(parts, ignore_index=True)
    lab = lab.drop_duplicates(["id", "annotator"], keep="last")  # edits are appended
    for c in LABEL_COLUMNS:
        if c not in lab:
            lab[c] = np.nan
    lab[["window_start", "window_end"]] = lab[["window_start", "window_end"]].apply(pd.to_numeric, errors="coerce")
    return lab


def _label_issues(seg: pd.DataFrame, lab: pd.DataFrame) -> pd.DataFrame:
    seg_len = seg.set_index("id").end_time.astype(float)
    issues = []
    for r in lab.itertuples():
        if r.id not in seg_len.index:
            issues.append((r.id, r.annotator, "label for a segment not in segments.csv"))
            continue
        if r.frog_in_window in ("yes", "unsure"):
            if pd.isna(r.window_start) or pd.isna(r.window_end):
                issues.append((r.id, r.annotator, f"'{r.frog_in_window}' but no window placed"))
            elif r.window_start < -0.05 or r.window_end > seg_len[r.id] + 0.05:
                issues.append((r.id, r.annotator, "window outside the clip"))
            elif abs(r.window_end - r.window_start - WINDOW_SECONDS) > 0.05:
                issues.append((r.id, r.annotator, "window is not 3 s"))
        if r.frog_in_window == "yes" and pd.isna(r.species_1):
            issues.append((r.id, r.annotator, "'yes' but no species"))
    return pd.DataFrame(issues, columns=["id", "annotator", "issue"])


def check_labels(cfg: dict) -> pd.DataFrame:
    """Show labeling progress per site and return a table of rows to fix."""
    seg = pd.read_csv(_dir(cfg) / SEGMENTS_CSV)
    lab = _load_labels(_label_files(cfg))
    issues = _label_issues(seg, lab)

    first = lab.drop_duplicates("id")[["id", "frog_in_window"]]
    m = seg[["id", "site"]].merge(first, on="id", how="left")
    prog = (m.assign(answer=m.frog_in_window.fillna("not labeled"))
             .pivot_table(index="site", columns="answer", values="id", aggfunc="size", fill_value=0))
    prog.insert(0, "segments", prog.sum(axis=1))
    print(f"{lab.annotator.nunique()} annotator(s), {first.id.isin(seg.id).sum()} of {len(seg)} segments labeled")
    print(prog.to_string())
    if "not labeled" not in prog and prog.get("yes", pd.Series(0)).sum() == 0 and len(lab):
        print("\nNo frogs in any segment so far. You can add segments with step 3 (Resample).")
    if len(issues):
        print(f"\n{len(issues)} answers to fix (find the id in the annotator's table):")
    else:
        print("\nNo problems found.")
    return issues


# ─── Notebook steps (collaborator) ───────────────────────────────────────────

def sample(cfg: dict) -> None:
    """Step 1: list recordings, then sample the first round of segments (or reuse earlier ones)."""
    sample_segments(cfg, build_inventory(cfg))


def annotate(cfg: dict, annotator: str):
    """Step 2: open the labeling tool. Labels save to exports/<source>/labels/frog_labels_<annotator>.csv."""
    if not str(annotator or "").strip() or annotator == "XX":
        raise ValueError("Set ANNOTATOR to your initials in step 1.")
    d = _dir(cfg)
    if not (d / SEGMENTS_CSV).exists():
        raise RuntimeError("Nothing to label yet. Run step 1 first.")
    from jupyter_bioacoustic import BioacousticAnnotator
    # segments.csv lists clip paths relative to exports/<source>/, so audio_prefix points the tool there.
    ann = BioacousticAnnotator(config=ANNOTATOR_YAML, project_name=f"Frog windows: {cfg['source']}",
                               data_path=(d / SEGMENTS_CSV).as_posix(), data_index_column="id",
                               audio_column="path", audio_prefix=d.as_posix(),
                               output_path=(d / LABEL_DIR / f"frog_labels_{annotator}.csv").as_posix(),
                               output_index_column="id", annotator=annotator)
    ann.open()
    return ann  # keep a reference, so the tool stays connected


def add_round(cfg: dict, add: bool) -> None:
    """Step 3: sample another round, once every segment so far is labeled. Does nothing unless add."""
    if not add:
        print("Set ADD_ROUND = True above to sample another round.")
        return
    if not len(_existing(cfg)):
        raise RuntimeError("Nothing sampled yet. Run step 1 first.")
    sample_segments(cfg, pd.read_csv(_dir(cfg) / INVENTORY_CSV), new_round=True)


# ─── Export (collaborator) ───────────────────────────────────────────────────

def export_source(cfg: dict, force: bool = False) -> Path:
    """Step 4: check the labels, then zip exports/<source>/ into exports/<source>_<date>.zip."""
    issues = check_labels(cfg)
    if len(issues) and not force:
        raise RuntimeError("Fix the answers listed above first (or pass force=True).")
    d = _dir(cfg)
    seg = pd.read_csv(d / SEGMENTS_CSV)

    try:
        from importlib.metadata import version
        jb = version("jupyter-bioacoustic")
    except Exception:
        jb = "unknown"
    info = yaml.safe_load((d / LOCK_YAML).read_text())
    info.update(exported=date.today().isoformat(), jupyter_bioacoustic=jb, segments=len(seg),
                labeled=int(seg.id.isin(_load_labels(_label_files(cfg)).id).sum()))
    (d / LOCK_YAML).write_text(yaml.safe_dump(info, sort_keys=False))

    zip_path = Path(shutil.make_archive(str(EXPORTS_DIR / f"{d.name}_{date.today():%Y%m%d}"),
                                        "zip", root_dir=EXPORTS_DIR, base_dir=d.name))
    print(f"Send this file: {zip_path.resolve()}")
    return zip_path


# ─── Project lead ────────────────────────────────────────────────────────────

def ingest_source(path: str, dataset_root: str = "dataset") -> Path:
    """Check a received zip (or folder) and copy it into dataset/sources/<source>/.

    Re-sending is safe: new clips are added, and labels and segments.csv are replaced.
    """
    root = Path(dataset_root)
    with tempfile.TemporaryDirectory() as tmp:
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as z:
                z.extractall(tmp)
            tops = [p for p in Path(tmp).iterdir() if p.is_dir()]
            if len(tops) != 1:
                raise ValueError("The zip should contain exactly one folder")
            src_dir = tops[0]
        else:
            src_dir = Path(path)

        missing = [f for f in ("source.yaml", "segments.csv", "clips") if not (src_dir / f).exists()]
        if missing:
            raise ValueError(f"Missing from {path}: {', '.join(missing)}")
        info = yaml.safe_load((src_dir / "source.yaml").read_text())
        name = info["source"]
        seg = pd.read_csv(src_dir / "segments.csv")
        problems = []
        if seg.id.duplicated().any():
            problems.append("duplicate segment IDs")
        if not seg.id.str.startswith(name + SEP).all():
            problems.append(f"IDs that don't start with '{name}{SEP}'")
        no_clip = [p for p in seg.path if not (src_dir / p).exists()]
        if no_clip:
            problems.append(f"{len(no_clip)} segments without a clip, e.g. {no_clip[0]}")
        lab = _load_labels(sorted((src_dir / "labels").glob("*.csv")))
        if (~lab.id.isin(seg.id)).any():
            problems.append("labels for segments not in segments.csv")
        if problems:
            raise ValueError(f"{name}: " + "; ".join(problems))

        dest = root / "sources" / name
        if (dest / "source.yaml").exists():
            old = yaml.safe_load((dest / "source.yaml").read_text())
            if old.get("contributor") != info.get("contributor"):
                raise ValueError(f"A source named '{name}' already exists from {old.get('contributor')!r}. "
                                 "Ask the sender to choose a different source name.")
        shutil.copytree(src_dir / "clips", dest / "clips", dirs_exist_ok=True)
        if (dest / "labels").exists():
            shutil.rmtree(dest / "labels")
        shutil.copytree(src_dir / "labels", dest / "labels")
        for f in ("segments.csv", "source.yaml"):
            shutil.copy2(src_dir / f, dest / f)
    print(f"{name}: {len(seg)} segments, {lab.id.nunique()} labeled, "
          f"{lab.annotator.nunique()} annotator(s) -> {dest}")
    return dest


def _species_codes(species_csv: str) -> dict:
    sp = pd.read_csv(species_csv)
    codes = dict(zip(sp.common_name, sp.code))
    codes["unknown_frog"] = "UNKNOWN"
    return codes


def _kappa(a: pd.Series, b: pd.Series) -> float:
    cats = sorted(set(a) | set(b))
    po = (a.to_numpy() == b.to_numpy()).mean()
    pe = sum((a == c).mean() * (b == c).mean() for c in cats)
    return float("nan") if pe == 1 else (po - pe) / (1 - pe)


def build_dataset(dataset_root: str = "dataset", species_csv: str = "species.csv",
                  cut: bool = False) -> pd.DataFrame:
    """Merge every received source into one table of labeled 3 s windows.

    Writes dataset/build/v<date>/: segments.csv, labels.csv (every answer from every
    annotator), windows.csv (one row per usable window, with species columns) and
    review.csv (unsure answers and disagreements between annotators).
    With cut=True, also cuts each window into build/.../clips/<source>/<window_id>.flac.
    """
    root = Path(dataset_root)

    segs, labs = [], []
    for d in sorted((root / "sources").iterdir()):
        s = pd.read_csv(d / "segments.csv")
        s["clip_path"] = [f"sources/{d.name}/{p}" for p in s.path]
        segs.append(s)
        labs.append(_load_labels(sorted((d / "labels").glob("*.csv"))))
    seg = pd.concat(segs, ignore_index=True)
    lab = pd.concat(labs, ignore_index=True)
    lab = lab[lab.id.isin(seg.id)]

    codes = _species_codes(species_csv)
    status = {"yes": "positive", "no": "negative", "unsure": "unsure", "unusable": "excluded"}
    seg_len = seg.set_index("id").end_time.astype(float)
    lab["status"] = lab.frog_in_window.map(status).fillna("excluded")
    lab["species"] = lab[["species_1", "species_2", "species_3"]].apply(
        lambda r: tuple(sorted({codes.get(v, v) for v in r.dropna()})), axis=1)
    center = (lab.id.map(seg_len) - WINDOW_SECONDS) / 2
    lab["start"] = np.where(lab.status == "negative", center, lab.window_start)

    # Agreement on double-labeled segments (answer only; species and window are checked below)
    pairs = lab.groupby("id").filter(lambda g: len(g) > 1).groupby("id").head(2)
    if len(pairs):
        a = pairs.groupby("id").nth(0).set_index("id").status
        b = pairs.groupby("id").nth(1).set_index("id").status.reindex(a.index)
        agreement = (f"Double-labeled: {len(a)} segments. Same answer: {(a == b).mean():.0%} "
                     f"(kappa {_kappa(a, b):.2f})")

    def consensus(g):
        if len(g) == 1:
            return g.iloc[0][["status", "species", "start"]]
        same = g.status.nunique() == 1 and g.species.nunique() == 1
        if same and g.status.iloc[0] in ("positive", "unsure"):
            same = (g.start.max() - g.start.min()) <= WINDOW_SECONDS / 2  # windows overlap by half
        if not same:
            return pd.Series({"status": "disagreement", "species": (), "start": np.nan})
        return pd.Series({"status": g.status.iloc[0], "species": g.species.iloc[0], "start": g.start.mean()})

    con = lab.groupby("id").apply(consensus, include_groups=False).reset_index()
    if len(pairs):
        full = (~con[con.id.isin(a.index)].status.eq("disagreement")).mean()
        print(f"{agreement}. Same answer, species and window: {full:.0%}")
    win = seg.merge(con, on="id", how="inner")
    win["site_key"] = win.source + SEP + win.site
    win["window_id"] = [f"{i}{SEP}w{int(round(s * 1000)):05d}" if pd.notna(s) else ""
                        for i, s in zip(win.id, win.start)]
    win["end"] = win.start + WINDOW_SECONDS

    all_codes = sorted({c for sp in win.species for c in sp})
    for c in all_codes:
        win[f"sp_{c}"] = win.species.apply(lambda sp, c=c: int(c in sp))
    win["species"] = win.species.apply(lambda sp: ";".join(sp))

    out = root / "build" / f"v{date.today():%Y%m%d}"
    out.mkdir(parents=True, exist_ok=True)
    seg.to_csv(out / "segments.csv", index=False)
    lab.assign(species=lab.species.apply(";".join)).to_csv(out / "labels.csv", index=False)
    usable = win[win.status.isin(["positive", "negative"])]
    keep = ["window_id", "id", "clip_path", "start", "end", "source", "site", "site_key",
            "round", "status", "species"] + [f"sp_{c}" for c in all_codes]
    usable[keep].to_csv(out / "windows.csv", index=False)
    win[win.status.isin(["unsure", "disagreement"])][["id", "clip_path", "status", "source", "site"]].to_csv(
        out / "review.csv", index=False)
    (out / "build_info.yaml").write_text(yaml.safe_dump(
        {"built": datetime.now().isoformat(timespec="seconds"), "sources": sorted(seg.source.unique()),
         "species_csv": species_csv}, sort_keys=False))

    if cut:
        clips = usable.assign(src=[(root / p).as_posix() for p in usable.clip_path], dur=WINDOW_SECONDS,
                              dest=[(out / "clips" / src / f"{w}.flac").as_posix()
                                    for src, w in zip(usable.source, usable.window_id)])
        cut_clips(clips, src="src", start="start", duration="dur", dest="dest")

    print(f"{len(usable)} windows -> {out}/windows.csv  ({(win.status == 'unsure').sum()} unsure, "
          f"{(win.status == 'disagreement').sum()} disagreements in review.csv)")
    print(usable.pivot_table(index="source", columns="status", values="window_id",
                             aggfunc="size", fill_value=0).to_string())
    return usable[keep]
