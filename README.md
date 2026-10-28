# bioacoustic-review

Notebooks for listening to wildlife recordings, checking BirdNET detections and annotating your own audio, using the [jupyter_bioacoustic](https://github.com/SchmidtDSE/jupyter_bioacoustic) annotator.

## What's in the repo

There are three workflows. Each has its own notebook, data folder and annotator config:

| | 1. API detections | 2. Local recordings | 3. Frog sampling |
|---|---|---|---|
| Notebook | `notebooks/01_review_API_detections.ipynb` | `notebooks/02_review_local_recordings.ipynb` | `notebooks/03_frog_sampling.ipynb` |
| What you review | A random sample of detections at sites you pick on a map | Your own audio files, whole or split into clips | Random 10-second segments from your own recordings, labeled for frog calls (rules in `PROTOCOL.md`) |
| Audio comes from | S3, using your AWS keys (the API only gives the file paths) | `local_data/audio/` | `local_data/audio/`, or any folder you set in the notebook |
| Needs | `.env` with an API token and AWS keys | Nothing | Nothing |
| Clip list | `detection_data/filtered_detections.csv` | `local_data/metadata.csv` | `exports/<source>/segments.csv` |
| Answers saved to | `output/api_reviews.csv` | `output/local_reviews.csv` | `exports/<source>/labels/frog_labels_<initials>.csv` |
| Annotator config | `annotator_config/*/api_detections.yaml` | `annotator_config/*/local_data.yaml` | `annotator_config/*/frogs.yaml` |

```
notebooks/            the three workflows
annotator_config/
  projects/           which clip list to load and where to save answers
  config/             columns shown, spectrogram views, instructions
  forms/              the questions asked for each clip
frogs.yaml            frog sampling design, set by the project lead (don't edit)
functions.py          sampling, label checks and export for the frog workflow
PROTOCOL.md           frog labeling rules
species.csv           species list for the dropdowns in the forms
pixi.toml             environment: Python, JupyterLab, ffmpeg, jupyter-bioacoustic
.devcontainer/        optional Docker setup for VS Code
```

Git ignores `.env` and the `local_data/`, `detection_data/`, `output/` and `exports/` folders. You create `.env` and `local_data/audio/` yourself (step 3 below). The notebooks and the annotator create the rest. Spectrogram images from the annotator's **Capture** button are saved in `output/spectrograms/`. Notebook 03 never writes to your audio folder: everything it makes for a dataset (clips, segments, labels, settings) goes in `exports/<source>/`, and its export step zips that folder.

**Species:** `species.csv` sets the species options in both forms. The forms read its `common_name` column, so edit or replace the file to review other species. It currently lists 33 California frog and toad species, plus 3 groups for species whose calls can't be told apart (`MYLF`, `PACH`, `WETO`). Each row also has a 4-letter code, the scientific name, native or non-native status, and aliases.

## Get started

**1. Download the repo.**

```bash
git clone https://github.com/avanscoyoc/bioacoustic-review.git
cd bioacoustic-review
```

Without git, click **Code → Download ZIP** on GitHub and unzip it.

**2. Install [pixi](https://pixi.sh).** You only need to do this once. Close and reopen your terminal afterwards.

- macOS or Linux: `curl -fsSL https://pixi.sh/install.sh | sh`
- Windows (PowerShell): `powershell -ExecutionPolicy ByPass -c "irm -useb https://pixi.sh/install.ps1 | iex"`

Optional: if you use VS Code and have Docker installed (on Windows, macOS or Linux), you can skip installing pixi and use the dev container instead. Open the folder in VS Code and click **Reopen in Container**; pixi is already set up inside it. Inside the container, your audio has to be inside the repo folder.

**3. Add your data.**

- **Local recordings:** create `local_data/audio/` and copy your audio into it (`.wav`, `.flac`, `.mp3` or `.ogg`; subfolders are fine). To use audio stored somewhere else, set `AUDIO_DIR` in the first code cell of notebook 02, or in step 1 of notebook 03, instead. Write the path with `/`, even on Windows (e.g. `"D:/recordings"`).
- **API detections:** create a file named `.env` in the repo folder:
  ```
  API_TOKEN=<your __Secure-authjs.session-token cookie from Wildlife Sound Hub>
  AWS_ACCESS_KEY_ID=<your AWS access key>
  AWS_SECRET_ACCESS_KEY=<your AWS secret key>
  ```
  If your AWS credentials are temporary, also add `AWS_SESSION_TOKEN=<...>`.

**4. Run.** In a terminal, go to the repo folder and run:

```bash
pixi run jupyter
```

The first run installs everything, which takes a few minutes. On Windows, JupyterLab opens in your browser. On other systems, open the `http://127.0.0.1:8888/lab?token=...` link it prints.

Then:

1. Open a notebook in `notebooks/`.
2. Run the cells in order, setting the options as you reach them:
   - Notebook 01: pick sites on the map in step 1, then set `SPECIES_WORDS`, `MIN_CONFIDENCE`, `CLIPS_PER_SITE` and `ONE_CLIP_PER_RECORDING` in step 2. **Run All** stops at step 2 if you haven't picked any sites yet.
   - Notebook 02: set `AUDIO_DIR`, and `CHUNK_SECONDS` to split recordings into clips. Then run all the cells.
   - Notebook 03: fill in the settings in step 1 (your dataset name, initials, audio folder, file names, time zone, and optionally `SITES`, `HOURS` and `MONTHS`). Then run all the cells. Follow `PROTOCOL.md`.
3. The annotator appears at the bottom and saves each answer as you go.
4. When you're done, press Ctrl+C in the terminal.

The annotator only works in JupyterLab in a browser. It doesn't work in VS Code's notebook viewer. Notebooks 01 and 02 keep their answers in one file in `output/`, and notebook 03 in `exports/<source>/labels/`. The file updates as you add, change or delete answers, so they're still there when you stop and come back later.

## Troubleshooting

- **401 error in notebook 01's first cell:** your API token has expired. Copy a fresh one into `.env` and run the notebook again.
- **The link shows port 8889:** another Jupyter server is already running. Use the printed link, or stop the other server.
