# non-avian-validation

Review BirdNET detections in [jupyter_bioacoustic](https://github.com/SchmidtDSE/jupyter_bioacoustic).

## Setup (once)

1. Open the repo in VS Code and click **Reopen in Container**.
2. Create a `.env` file in the repo root:
   ```
   API_TOKEN=<value of your API session token cookie>
   AWS_ACCESS_KEY_ID=<your AWS access key>
   AWS_SECRET_ACCESS_KEY=<your AWS secret key>
   ```
   The AWS keys let the notebook read the audio straight from S3. If they're temporary credentials, also add `AWS_SESSION_TOKEN=<...>`.

## Run

1. In the VS Code terminal, run:
   ```bash
   pixi run jupyter
   ```
2. Ctrl/Cmd+click the `http://127.0.0.1:8888/lab?token=...` link it prints.
3. In the browser, open `notebooks/01_review_CDFW_detections.ipynb`.
4. Edit the filters (`SPECIES`, `SITES`, `RECORDING_IDS`, `MIN_CONFIDENCE`) if needed.
5. **Run → Run All Cells**. The annotator appears at the bottom.
6. When done, press Ctrl+C in the terminal.

The annotator only works in JupyterLab in the browser, not in VS Code's notebook viewer.

## Windows (no container): review local recordings

1. Install pixi. In PowerShell, run:
   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm -useb https://pixi.sh/install.ps1 | iex"
   ```
   Then close and reopen PowerShell.
2. Put the audio in `local_data\audio\`. Subfolders are fine.
3. In PowerShell, in the repo folder, run:
   ```powershell
   pixi run jupyter
   ```
   The first run installs everything, which takes a few minutes. JupyterLab then opens in your browser, already logged in.
4. Open `notebooks/02_review_local_recordings.ipynb`. In the first code cell:
   - If the audio is somewhere else, set `AUDIO_DIR` to that folder, using `/` (e.g. `"D:/recordings"`).
   - To split recordings into chunks, set `CHUNK_SECONDS` (e.g. `10`). Leave it `None` to review whole recordings.
5. Click **Run → Run All Cells**. The first cell writes `local_data\metadata.csv`; the second loads it into the annotator.
6. When done, press Ctrl+C in PowerShell.

No `.env` or API token is needed for local recordings.

## Troubleshooting

- **"API token rejected"**: copy a fresh token into `.env` and rerun all cells.
- **Link shows port 8889**: another server is running. Use the printed link, or stop the other server.
