# bioacoustic-review

Notebooks for listening to wildlife recordings, checking BirdNET detections and annotating your own audio, using the [jupyter_bioacoustic](https://github.com/SchmidtDSE/jupyter_bioacoustic) annotator.

## What's in the repo

There are two workflows. Each has its own notebook, data folder and annotator config:

| | 1. CDFW detections | 2. Local recordings |
|---|---|---|
| Notebook | `notebooks/01_review_CDFW_detections.ipynb` | `notebooks/02_review_local_recordings.ipynb` |
| What you review | A random sample of detections at sites you pick on a map | Your own audio files, whole or split into clips |
| Audio comes from | S3, via the Wildlife Sound Hub API | `local_data/audio/` |
| Needs | `.env` with an API token and AWS keys | Nothing |
| Clip list | `detection_data/filtered_detections.csv` | `local_data/metadata.csv` |
| Answers saved to | `output/cdfw_reviews.csv` | `output/reviews_local_data.csv` |
| Annotator config | `annotator_config/*/cdfw_detections.yaml` | `annotator_config/*/local_data.yaml` |

```
notebooks/            the two workflows
annotator_config/
  projects/           which clip list to load and where to save answers
  config/             columns shown, spectrogram views, instructions
  forms/              the questions asked for each clip
species.csv           species list for the dropdowns in both forms
pixi.toml             environment: Python, JupyterLab, ffmpeg, jupyter-bioacoustic
.devcontainer/        optional Docker setup for VS Code
```

Git ignores `.env`, `local_data/`, `detection_data/` and `output/`. The notebooks create these folders when they run.

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

Another option on any OS with Docker, including Intel Macs (`pixi.toml` doesn't include them as a platform): open the folder in VS Code, click **Reopen in Container**, and skip this step. Inside the container, your audio has to be inside the repo folder.

**3. Add your data.**

- **Local recordings:** create `local_data/audio/` and copy your audio into it (`.wav`, `.flac`, `.mp3` or `.ogg`; subfolders are fine). To use audio stored somewhere else, set `AUDIO_DIR` in the notebook instead.
- **CDFW detections:** create a file named `.env` in the repo folder:
  ```
  API_TOKEN=<your __Secure-authjs.session-token cookie from Wildlife Sound Hub>
  AWS_ACCESS_KEY_ID=<your AWS access key>
  AWS_SECRET_ACCESS_KEY=<your AWS secret key>
  ```
  If your AWS credentials are temporary, also add `AWS_SESSION_TOKEN=<...>`.

**4. Run.** Navigate to the repo folder, run:

```bash
pixi run jupyter
```

The first run installs everything, which takes a few minutes. On Windows, JupyterLab opens in your browser. On other systems, open the `http://127.0.0.1:8888/lab?token=...` link it prints.

Then:

1. Open a notebook in `notebooks/`.
2. Set the options near the top:
   - Notebook 01: `SPECIES_WORDS`, `MIN_CONFIDENCE`, `CLIPS_PER_SITE`. You also pick sites on the map.
   - Notebook 02: `AUDIO_DIR`, and `CHUNK_SECONDS` to split recordings into clips.
3. Run the cells from top to bottom. The annotator appears at the bottom and saves each answer as you go.
4. When you're done, press Ctrl+C in the terminal.

The annotator only works in JupyterLab in a browser. It doesn't work in VS Code's notebook viewer. Annotations are output to a single file in outputs and overwritten with any additions or deletions, even when you stop a session and come back later.

## Troubleshooting

- **401 error in notebook 01's first cell:** your API token has expired. Copy a fresh one into `.env` and run the notebook again.
- **The link shows port 8889:** another Jupyter server is already running. Use the printed link, or stop the other server.
