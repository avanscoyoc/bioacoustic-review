# non-avian-validation

Review BirdNET detections in [jupyter_bioacoustic](https://github.com/SchmidtDSE/jupyter_bioacoustic).

## Setup (once)

1. Open the repo in VS Code and click **Reopen in Container**.
2. Create a `.env` file in the repo root:
   ```
   API_TOKEN=<value of your API session token cookie>
   ```

## Run

1. In the VS Code terminal, run:
   ```bash
   pixi run jupyter
   ```
2. Ctrl/Cmd+click the `http://127.0.0.1:8888/lab?token=...` link it prints.
3. In the browser, open `notebooks/01_review_detections.ipynb`.
4. Edit the filters (`SPECIES`, `SITES`, `RECORDING_IDS`, `MIN_CONFIDENCE`) if needed.
5. **Run → Run All Cells**. The annotator appears at the bottom.
6. When done, press Ctrl+C in the terminal.

The annotator only works in JupyterLab in the browser, not in VS Code's notebook viewer.

## Troubleshooting

- **"API token rejected"**: copy a fresh token into `.env` and rerun all cells.
- **Link shows port 8889**: another server is running. Use the printed link, or stop the other server.
