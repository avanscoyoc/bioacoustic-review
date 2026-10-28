"""Sampling, clipping, label checks and export for the frog labeling workflow.

Collaborator (notebooks/03_frog_sampling.ipynb):
    load_config -> build_inventory -> choose_nights or pick_nights_widget
    -> sample_segments (also cuts the 10 s clips) -> label -> check_labels -> export_source

Project lead:
    ingest_source (one call per received zip) -> build_dataset (one merged, labeled table)

Every path is relative to the repo folder; the notebooks change into it first.
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

# Fixed locations, shared with annotator_config/projects/frogs.yaml
WORK_DIR = Path("local_data")
INVENTORY_CSV = WORK_DIR / "inventory.csv"
SITES_CSV = WORK_DIR / "sites.csv"
SELECTION_CSV = WORK_DIR / "selection.csv"
SEGMENTS_CSV = WORK_DIR / "segments.csv"  # the annotator loads this file
LOCK_YAML = WORK_DIR / "source.yaml"
CLIP_DIR = WORK_DIR / "clips"
LABELS_GLOB = "output/frog_labels_*.csv"  # the annotator writes these files

AUDIO_EXTENSIONS = {".wav", ".flac", ".mp3", ".ogg"}
SEP = "__"  # joins the parts of an ID: source__site__recording__s000000
WINDOW_SECONDS = 3.0
HIGH_RES_SUBTYPES = {"PCM_24", "PCM_32", "FLOAT", "DOUBLE"}

DEFAULTS = {
    "source": None,
    "contributor": "",
    "audio_dir": "local_data/audio",
    "path_pattern": "{site}/{_}",
    "recording_time": {"format": "%Y%m%d_%H%M%S", "file_timezone": "UTC", "local_timezone": "UTC"},
    "night_starts_at": 12,
    "pick_nights": "auto",
    "nights_per_site": 5,
    "segments_per_night": 5,
    "segment_seconds": 10,
    "min_gap_seconds": 60,
    "max_segments_per_site": 75,
    "seed": 0,
    "focus": {"hours": None, "months": None},
}
# Settings that decide which segments exist. They can't change once sampling has started.
LOCKED_KEYS = ["source", "path_pattern", "recording_time", "night_starts_at",
               "segments_per_night", "segment_seconds", "min_gap_seconds", "seed"]

SEGMENT_COLUMNS = ["seq", "id", "path", "start_time", "end_time", "source", "site", "recording",
                   "night", "source_path", "offset_s", "local_time", "sample_rate", "round",
                   "block", "placement", "night_choice", "focused"]
LABEL_COLUMNS = ["frog_in_window", "window_start", "window_end", "species_1", "species_2",
                 "species_3", "calling_index", "clarity", "call_type", "species_outside",
                 "insects", "birds_mammals", "rain", "wind", "water", "human", "handling",
                 "unsure_reason", "notes"]


# ─── Config ──────────────────────────────────────────────────────────────────

def load_config(path: str) -> dict:
    """Read the sampling config, fill in defaults and stop on anything invalid."""
    with open(path) as f:
        user = yaml.safe_load(f) or {}
    cfg = {**DEFAULTS, **user}
    for key in ("recording_time", "focus"):
        cfg[key] = {**DEFAULTS[key], **(user.get(key) or {})}

    errors = []
    if not cfg["source"] or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", str(cfg["source"])):
        errors.append("source: use lowercase letters, digits and hyphens, e.g. 'ucb-ponds'")
    if "{site}" not in cfg["path_pattern"]:
        errors.append("path_pattern: must contain {site}")
    if not Path(cfg["audio_dir"]).is_dir():
        errors.append(f"audio_dir: folder not found: {Path(cfg['audio_dir']).resolve()}")
    if cfg["pick_nights"] not in ("auto", "manual"):
        errors.append("pick_nights: use auto or manual")
    for key in ("nights_per_site", "segments_per_night", "segment_seconds", "max_segments_per_site"):
        if not isinstance(cfg[key], int) or cfg[key] < 1:
            errors.append(f"{key}: must be a whole number of 1 or more")
    if not isinstance(cfg["min_gap_seconds"], (int, float)) or cfg["min_gap_seconds"] < 0:
        errors.append("min_gap_seconds: must be 0 or more")
    if cfg["segment_seconds"] < WINDOW_SECONDS:
        errors.append(f"segment_seconds: must be at least {WINDOW_SECONDS:g}")
    if errors:
        raise ValueError("Fix these settings in " + path + ":\n  " + "\n  ".join(errors))
    return cfg


def _check_lock(cfg: dict, have_segments: bool) -> None:
    """Refuse to sample if a locked setting changed after the first round."""
    locked = {k: cfg[k] for k in LOCKED_KEYS}
    if have_segments and LOCK_YAML.exists():
        stored = (yaml.safe_load(LOCK_YAML.read_text()) or {}).get("locked", {})
        changed = [k for k in LOCKED_KEYS if k in stored and stored[k] != locked[k]]
        if changed:
            raise ValueError(
                f"These settings changed after sampling started: {', '.join(changed)}.\n"
                f"Change them back to the values in {LOCK_YAML}. To start over instead, "
                f"move {WORK_DIR}/segments.csv, selection.csv, source.yaml and clips/ somewhere else.")
        return
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    LOCK_YAML.write_text(yaml.safe_dump(
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
    """List every audio file, read site and time from its path, and group files into nights."""
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

    # Recorder clock -> local time, then group into nights (before noon = previous night)
    t = pd.to_datetime(inv.start_file_time)
    if t.notna().any():
        local_tz = tcfg["local_timezone"]
        file_tz = local_tz if str(tcfg["file_timezone"]).lower() == "local" else tcfg["file_timezone"]
        t = (t.dt.tz_localize(file_tz, ambiguous="NaT", nonexistent="shift_forward")
             .dt.tz_convert(local_tz).dt.tz_localize(None))
    inv["start_local"] = t
    shifted = t - pd.Timedelta(hours=cfg["night_starts_at"])
    inv["night"] = np.where(t.notna(), shifted.dt.strftime("%Y%m%d"), "file-" + inv.recording)
    inv = inv.drop(columns=["start_file_time"]).sort_values(["site", "night", "start_local", "source_path"])
    inv = inv.reset_index(drop=True)

    WORK_DIR.mkdir(parents=True, exist_ok=True)
    inv.to_csv(INVENTORY_CSV, index=False)
    _update_sites_template(inv.site.unique())

    summary = inv.groupby("site").agg(
        files=("source_path", "size"), nights=("night", "nunique"),
        hours=("duration_s", lambda d: round(d.sum() / 3600, 1)),
        with_time=("start_local", lambda s: f"{s.notna().mean():.0%}"),
        pattern_ok=("pattern_ok", lambda s: f"{s.mean():.0%}"))
    print(f"{len(inv)} recordings at {inv.site.nunique()} sites -> {INVENTORY_CSV}")
    if unreadable:
        print(f"Skipped {len(unreadable)} unreadable or empty files, e.g. {unreadable[0]}")
    if not inv.pattern_ok.all():
        print(f"{(~inv.pattern_ok).sum()} files don't match path_pattern; their site is the top folder name. "
              f"Example: {inv.loc[~inv.pattern_ok, 'source_path'].iloc[0]}")
    if inv.start_local.isna().any():
        print(f"{inv.start_local.isna().sum()} files have no time in their name; each is treated as its own night.")
    print(summary.to_string())
    return inv


def _update_sites_template(sites) -> None:
    cols = ["site", "lat", "lon", "habitat", "notes"]
    old = pd.read_csv(SITES_CSV, dtype=str) if SITES_CSV.exists() else pd.DataFrame(columns=cols)
    new = pd.DataFrame({"site": [s for s in sites if s not in set(old.site)]})
    if len(new):
        pd.concat([old, new], ignore_index=True)[cols].to_csv(SITES_CSV, index=False)
        print(f"Fill in coordinates and habitat for {len(new)} new sites in {SITES_CSV}")


def _load_inventory(inv: pd.DataFrame | None = None) -> pd.DataFrame:
    if inv is None:
        inv = pd.read_csv(INVENTORY_CSV)
    inv = inv.copy()
    inv["start_local"] = pd.to_datetime(inv.start_local)
    inv["night"] = inv.night.astype(str)
    return inv


# ─── Choosing nights ─────────────────────────────────────────────────────────

def _existing():
    seg = (pd.read_csv(SEGMENTS_CSV, dtype={"night": str}) if SEGMENTS_CSV.exists()
           else pd.DataFrame(columns=SEGMENT_COLUMNS))
    sel = (pd.read_csv(SELECTION_CSV, dtype={"night": str}) if SELECTION_CSV.exists()
           else pd.DataFrame(columns=["site", "night", "round", "night_choice"]))
    return seg, sel


def _spread(items: list, k: int) -> list:
    """k items spread evenly through a sorted list."""
    if k <= 0:
        return []
    if len(items) <= k:
        return list(items)
    idx = np.unique(np.round(np.linspace(0, len(items) - 1, k)).astype(int))
    return [items[i] for i in idx]


def _night_table(inv: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """One row per site and night: hours, files, month, and whether focus allows it."""
    nights = inv.groupby(["site", "night"], as_index=False).agg(
        hours=("duration_s", lambda d: d.sum() / 3600), files=("source_path", "size"),
        start=("start_local", "min"))
    months = cfg["focus"]["months"]
    nights["in_focus"] = True
    if months:
        nights["in_focus"] = nights.start.dt.month.isin(months).fillna(False)
    return nights


def choose_nights(inv: pd.DataFrame, cfg: dict, new_round: bool = False) -> pd.DataFrame | None:
    """Pick nights for the next round, spread evenly across each site's deployment.

    Skips nights used in earlier rounds. If a site has no unused nights left, reuses
    old nights ('refill'): new segments then go in the gaps between earlier ones.
    """
    inv = _load_inventory(inv)
    seg, sel = _existing()
    if len(seg) and not new_round:
        print(f"Already sampled (round {int(seg['round'].max())}, {len(seg)} segments). "
              "To add more, use 'Sample another round' at the end of the notebook.")
        return None
    rnd = int(seg["round"].max()) + 1 if len(seg) else 1
    per_night, cap = cfg["segments_per_night"], cfg["max_segments_per_site"]
    nights = _night_table(inv, cfg)

    picks = []
    for site, g in nights.groupby("site"):
        room = cap - int((seg.site == site).sum())
        k = min(cfg["nights_per_site"], room // per_night)
        if k <= 0:
            print(f"{site}: reached max_segments_per_site ({cap}). Skipped.")
            continue
        g = g[g.in_focus].sort_values(["start", "night"])
        used = set(sel.loc[sel.site == site, "night"])
        fresh = [n for n in g.night if n not in used]
        chosen = [(n, "auto") for n in _spread(fresh, k)]
        if len(chosen) < k:
            old = [n for n in g.night if n in used]
            chosen += [(n, "refill") for n in _spread(old, k - len(chosen))]
        if len(chosen) < k:
            print(f"{site}: only {len(chosen)} of {k} nights available")
        picks += [{"site": site, "night": n, "round": rnd, "night_choice": c} for n, c in chosen]
    out = pd.DataFrame(picks, columns=["site", "night", "round", "night_choice"])
    print(f"Round {rnd}: {len(out)} nights at {out.site.nunique()} sites")
    return out


class NightPicker:
    """Checkbox picker for nights, one site at a time, pre-filled with the automatic choice."""

    def __init__(self, inv: pd.DataFrame, cfg: dict, new_round: bool = False):
        import ipywidgets as w
        self.confirmed = False
        self.auto = choose_nights(inv, cfg, new_round)
        if self.auto is None:
            self.widget = w.HTML("<b>Already sampled.</b> Nothing to pick.")
            return
        self.round = int(self.auto["round"].iloc[0]) if len(self.auto) else 1
        self.target = cfg["nights_per_site"]
        _, sel = _existing()
        nights = _night_table(_load_inventory(inv), cfg)
        nights = nights[nights.in_focus].sort_values(["site", "start", "night"])
        self.used = {(r.site, r.night) for r in sel.itertuples()}
        self.nights = {s: g for s, g in nights.groupby("site")}
        self.picked = {s: set(self.auto.loc[self.auto.site == s, "night"]) for s in self.nights}

        self.site = w.Dropdown(options=list(self.nights), description="Site")
        self.boxes = w.VBox(layout=w.Layout(max_height="320px", overflow_y="auto"))
        self.status = w.HTML()
        auto_btn, clear_btn = w.Button(description="Auto-pick this site"), w.Button(description="Clear this site")
        done_btn = w.Button(description="Use these nights", button_style="success")
        auto_btn.on_click(lambda _: self._set(set(self.auto.loc[self.auto.site == self.site.value, "night"])))
        clear_btn.on_click(lambda _: self._set(set()))
        done_btn.on_click(self._confirm)
        self.site.observe(lambda _: self._draw(), names="value")
        self.widget = w.VBox([w.HTML(f"<b>Round {self.round}:</b> tick {self.target} nights per site, "
                                     "spread across the deployment. Grey = used in an earlier round."),
                              self.site, self.boxes, w.HBox([auto_btn, clear_btn, done_btn]), self.status])
        self._draw()

    def _set(self, nights: set) -> None:
        self.picked[self.site.value] = nights
        self._draw()

    def _toggle(self, change, night):
        s = self.picked[self.site.value]
        s.add(night) if change["new"] else s.discard(night)
        self._status()

    def _draw(self) -> None:
        import ipywidgets as w
        site, boxes = self.site.value, []
        for r in self.nights[site].itertuples():
            label = (f"{r.night[:4]}-{r.night[4:6]}-{r.night[6:]}" if r.night[:1].isdigit() else r.night)
            used = (site, r.night) in self.used
            cb = w.Checkbox(value=r.night in self.picked[site], indent=False,
                            description=f"{label} · {r.hours:.1f} h · {r.files} file(s)" + (" · used" if used else ""),
                            layout=w.Layout(width="auto"),
                            style={"description_width": "initial", "text_color": "grey" if used else None})
            cb.observe(lambda c, n=r.night: self._toggle(c, n), names="value")
            boxes.append(cb)
        self.boxes.children = boxes
        self._status()

    def _status(self) -> None:
        site = self.site.value
        picked = sorted(self.picked[site])
        notes = []
        if len(picked) != self.target:
            notes.append(f"{len(picked)} of {self.target} nights ticked")
        days = pd.to_datetime([n for n in picked if n[:1].isdigit()], format="%Y%m%d")
        if len(days) > 1 and (np.diff(days.sort_values()).astype("timedelta64[D]").astype(int) == 1).any():
            notes.append("some nights are consecutive: spread them out if you can")
        total = sum(len(v) for v in self.picked.values())
        self.status.value = (f"{site}: " + ("; ".join(notes) if notes else "ok")
                             + f"<br>All sites: {total} nights" + (" · <b>confirmed</b>" if self.confirmed else ""))

    def _confirm(self, _=None) -> None:
        self.confirmed = True
        self._status()

    def selection(self) -> pd.DataFrame | None:
        """The ticked nights, once 'Use these nights' has been clicked."""
        if self.auto is None:
            return None
        if not self.confirmed:
            raise RuntimeError("Tick nights above and click 'Use these nights', then run this cell again.")
        rows = []
        for site, nights in self.picked.items():
            auto = set(self.auto.loc[self.auto.site == site, "night"])
            for n in sorted(nights):
                choice = "refill" if (site, n) in self.used else ("auto" if nights == auto else "manual")
                rows.append({"site": site, "night": n, "round": self.round, "night_choice": choice})
        return pd.DataFrame(rows, columns=["site", "night", "round", "night_choice"])

    def _ipython_display_(self):
        from IPython.display import display
        display(self.widget)


def pick_nights_widget(inv: pd.DataFrame, cfg: dict, new_round: bool = False) -> NightPicker:
    """Show the night picker. Read the result with .selection() after clicking 'Use these nights'."""
    return NightPicker(inv, cfg, new_round)


# ─── Sampling segments ───────────────────────────────────────────────────────

def _place(files: pd.DataFrame, n: int, seg_len: float, spacing: float,
           taken: dict, rng: np.random.Generator, hours) -> tuple[list, str]:
    """Choose up to n segment starts in one night.

    The night's files form one timeline split into n equal blocks, with one random
    segment per block. Each block keeps `spacing` seconds clear before the next one,
    so segments in the same file are always at least `spacing` apart. Nights too
    short for that (e.g. one 5-minute file) get evenly spaced segments instead.
    """
    glob, fidx, offs, cum = [], [], [], 0.0
    for i, f in enumerate(files.itertuples()):
        o = np.arange(0, int(np.floor(f.duration_s - seg_len)) + 1)
        for e in taken.get(f.recording, []):  # earlier rounds
            o = o[np.abs(o - e) >= spacing]
        if hours:
            if pd.isna(f.start_local):
                o = o[:0]
            else:
                o = o[np.isin((f.start_local + pd.to_timedelta(o, unit="s")).hour, hours)]
        glob.append(cum + o), fidx.append(np.full(len(o), i)), offs.append(o)
        cum += f.duration_s
    glob, fidx, offs = np.concatenate(glob), np.concatenate(fidx), np.concatenate(offs)
    if not len(glob):
        return [], "none"

    def free(chosen):
        ok = np.ones(len(glob), bool)
        for _, c in chosen:
            ok &= ~((fidx == fidx[c]) & (np.abs(offs - offs[c]) < spacing))
        return ok

    edges = np.linspace(0, cum, n + 1)
    strat = []
    for b in range(n):
        hi = edges[b + 1] - (spacing if b < n - 1 else 0)
        ok = np.flatnonzero((glob >= edges[b]) & (glob <= hi) & free(strat))
        if len(ok):
            strat.append((b, int(rng.choice(ok))))

    even = []
    for b, t in enumerate(np.linspace(glob.min(), glob.max(), n)):
        ok = np.flatnonzero(free(even))
        if not len(ok):
            break
        even.append((b, int(ok[np.argmin(np.abs(glob[ok] - t))])))

    best, how = (strat, "stratified") if len(strat) >= len(even) else (even, "even")
    return [(b, int(fidx[c]), int(offs[c])) for b, c in best], how


def _check_segments(seg: pd.DataFrame, spacing: float) -> None:
    dups = seg.id[seg.id.duplicated()]
    if len(dups):
        raise ValueError(f"Duplicate segment IDs, e.g. {dups.iloc[0]}")
    for (site, rec), g in seg.groupby(["site", "recording"]):
        d = np.diff(np.sort(g.offset_s.to_numpy(float)))
        if (d < spacing).any():
            raise ValueError(f"Segments closer than {spacing:g} s in {site}/{rec}")


def sample_segments(cfg: dict, inv: pd.DataFrame, nights: pd.DataFrame | None,
                    new_round: bool = False, force: bool = False) -> pd.DataFrame:
    """Sample segments in the chosen nights, cut their clips, and append them to segments.csv.

    Without new_round, an existing segments.csv is returned unchanged, so rerunning
    the notebook never adds segments by accident. With new_round, the previous round
    must be fully labeled first (or force=True).
    """
    seg_old, sel_old = _existing()
    _check_lock(cfg, have_segments=len(seg_old) > 0)
    if len(seg_old) and not new_round:
        print(f"Using {len(seg_old)} existing segments from {SEGMENTS_CSV} "
              f"(rounds 1-{int(seg_old['round'].max())}).")
        return seg_old
    if len(seg_old) and not force:
        last = int(seg_old["round"].max())
        labeled = set(_load_labels().id) if list(Path().glob(LABELS_GLOB)) else set()
        todo = seg_old[(seg_old["round"] == last) & ~seg_old.id.isin(labeled)]
        if len(todo):
            raise RuntimeError(f"Round {last} still has {len(todo)} unlabeled segments. "
                               "Finish them first, or pass force=True.")
    if nights is None or not len(nights):
        raise ValueError("No nights chosen. Run choose_nights or the night picker first.")

    inv = _load_inventory(inv)
    rnd = int(nights["round"].iloc[0])
    seg_len, per_night = cfg["segment_seconds"], cfg["segments_per_night"]
    spacing = seg_len + cfg["min_gap_seconds"]
    hours = cfg["focus"]["hours"]
    focused = bool(hours or cfg["focus"]["months"])
    taken = {(s, r): g.offset_s.astype(int).tolist() for (s, r), g in seg_old.groupby(["site", "recording"])}

    rows, short = [], []
    for nt in nights.itertuples():
        files = inv[(inv.site == nt.site) & (inv.night == nt.night)].reset_index(drop=True)
        rng = np.random.default_rng([cfg["seed"], rnd, zlib.crc32(f"{nt.site}|{nt.night}".encode())])
        site_taken = {r: taken.get((nt.site, r), []) for r in files.recording}
        placed, how = _place(files, per_night, seg_len, spacing, site_taken, rng, hours)
        if len(placed) < per_night:
            short.append(f"{nt.site} {nt.night}: {len(placed)} of {per_night}")
        for block, i, off in placed:
            f = files.iloc[i]
            sid = SEP.join([cfg["source"], f.site, f.recording, f"s{off:06d}"])
            rows.append({
                "id": sid, "path": (CLIP_DIR / f.site / f"{sid}.flac").as_posix(),
                "start_time": 0.0, "end_time": float(seg_len), "source": cfg["source"],
                "site": f.site, "recording": f.recording, "night": nt.night,
                "source_path": f.source_path, "offset_s": off,
                "local_time": (f.start_local + pd.Timedelta(seconds=off)) if pd.notna(f.start_local) else pd.NaT,
                "sample_rate": f.sample_rate, "round": rnd, "block": block + 1,
                "placement": how, "night_choice": nt.night_choice, "focused": focused})
    if short:
        print("Fewer segments than asked (not enough room or focus hours):\n  " + "\n  ".join(short))
    new = pd.DataFrame(rows, columns=[c for c in SEGMENT_COLUMNS if c != "seq"])
    if not len(new):
        raise RuntimeError("No segments could be placed in these nights.")
    start_seq = int(seg_old.seq.max()) + 1 if len(seg_old) else 1
    new.insert(0, "seq", range(start_seq, start_seq + len(new)))
    all_seg = pd.concat([seg_old, new], ignore_index=True) if len(seg_old) else new
    _check_segments(all_seg, spacing)

    # Clips first: if cutting stops halfway, segments.csv is unchanged and a rerun picks the same segments.
    new_src = new.assign(audio_file=[(Path(cfg["audio_dir"]) / p).as_posix() for p in new.source_path],
                         duration_s=float(seg_len))
    cut_clips(new_src, src="audio_file", start="offset_s", duration="duration_s", dest="path")
    all_seg.to_csv(SEGMENTS_CSV, index=False)
    pd.concat([sel_old, nights], ignore_index=True).to_csv(SELECTION_CSV, index=False)
    print(f"Round {rnd}: added {len(new)} segments ({new.site.nunique()} sites). "
          f"{len(all_seg)} segments in {SEGMENTS_CSV}.")
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

def _load_labels(files=None) -> pd.DataFrame:
    """All annotator output files, keeping each annotator's latest answer per segment."""
    files = sorted(Path().glob(LABELS_GLOB)) if files is None else files
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


def check_labels(cfg: dict | None = None) -> pd.DataFrame:
    """Show labeling progress per site and return a table of rows to fix."""
    seg = pd.read_csv(SEGMENTS_CSV, dtype={"night": str})
    lab = _load_labels()
    issues = _label_issues(seg, lab)

    first = lab.drop_duplicates("id")[["id", "frog_in_window"]]
    m = seg[["id", "site"]].merge(first, on="id", how="left")
    prog = (m.assign(answer=m.frog_in_window.fillna("not labeled"))
             .pivot_table(index="site", columns="answer", values="id", aggfunc="size", fill_value=0))
    prog.insert(0, "segments", prog.sum(axis=1))
    print(f"{lab.annotator.nunique()} annotator(s), {first.id.isin(seg.id).sum()} of {len(seg)} segments labeled")
    print(prog.to_string())
    if "not labeled" not in prog and prog.get("yes", pd.Series(0)).sum() == 0 and len(lab):
        print("\nNo frogs in any segment so far. You can add segments with 'Sample another round'.")
    if len(issues):
        print(f"\n{len(issues)} answers to fix (find the id in the annotator's table):")
    else:
        print("\nNo problems found.")
    return issues


# ─── Export (collaborator) ───────────────────────────────────────────────────

def export_source(cfg: dict, out_dir: str = "exports", force: bool = False) -> Path:
    """Package clips, segments, labels and settings into exports/<source>_<date>.zip."""
    issues = check_labels(cfg)
    if len(issues) and not force:
        raise RuntimeError("Fix the answers listed above first (or pass force=True).")
    src = cfg["source"]
    dest = Path(out_dir) / src
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "labels").mkdir(parents=True)

    seg = pd.read_csv(SEGMENTS_CSV, dtype={"night": str})
    shutil.copytree(CLIP_DIR, dest / "clips")
    seg.assign(path=[Path(p).relative_to(WORK_DIR).as_posix() for p in seg.path]).to_csv(
        dest / "segments.csv", index=False)
    for p in Path().glob(LABELS_GLOB):
        shutil.copy2(p, dest / "labels" / p.name)
    shutil.copy2(SITES_CSV, dest / "sites.csv")
    sites = pd.read_csv(SITES_CSV)
    if sites[["lat", "lon"]].isna().any().any():
        print(f"Note: some sites in {SITES_CSV} have no coordinates.")

    try:
        from importlib.metadata import version
        jb = version("jupyter-bioacoustic")
    except Exception:
        jb = "unknown"
    info = yaml.safe_load(LOCK_YAML.read_text())
    info.update(exported=date.today().isoformat(), jupyter_bioacoustic=jb,
                segments=len(seg), labeled=int(seg.id.isin(_load_labels().id).sum()))
    (dest / "source.yaml").write_text(yaml.safe_dump(info, sort_keys=False))

    zip_path = Path(shutil.make_archive(str(Path(out_dir) / f"{src}_{date.today():%Y%m%d}"),
                                        "zip", root_dir=out_dir, base_dir=src))
    shutil.rmtree(dest)
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

        missing = [f for f in ("source.yaml", "segments.csv", "sites.csv", "clips") if not (src_dir / f).exists()]
        if missing:
            raise ValueError(f"Missing from {path}: {', '.join(missing)}")
        info = yaml.safe_load((src_dir / "source.yaml").read_text())
        name = info["source"]
        seg = pd.read_csv(src_dir / "segments.csv", dtype={"night": str})
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
        for f in ("segments.csv", "sites.csv", "source.yaml"):
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
        s = pd.read_csv(d / "segments.csv", dtype={"night": str})
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
    keep = ["window_id", "id", "clip_path", "start", "end", "source", "site", "site_key", "night",
            "round", "focused", "status", "species"] + [f"sp_{c}" for c in all_codes]
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
