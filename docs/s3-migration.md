# Switching audio access from presigned URLs to `s3://` URIs

Status (2026-10-01): **not started**. This needs AWS credentials with read access to the `casoundhub` bucket. Without them, the presigned route in `notebooks/api_audio.py` is what works.

## Why switch
jupyter_bioacoustic reads `s3://` itself (boto3, byte-range reads). With `s3://` URIs in the table, all of the presigned-URL workarounds can go.

## Steps
1. **Credentials in the container**
   - Add the AWS CLI. Either put `awscli` in `pixi.toml`, or add the AWS CLI devcontainer feature to `.devcontainer/devcontainer.json`.
   - Run `aws configure sso`, then `aws sso login`, and set `AWS_PROFILE` if you don't use the default profile.
   - Check access:
     ```bash
     aws s3 ls s3://casoundhub/audio/cemaf-acoustics/AGCA1_2023/
     ```
2. **Build the URI.** `GET /recordings/<id>` returns `path` = `"casoundhub/audio/.../20230522_000000.flac"`, which is bucket + key. The URI is `"s3://" + path`:
   ```python
   recordings["audio_uri"] = "s3://" + recordings.path
   clips = clips.merge(recordings[["recording_id", "audio_uri"]], on="recording_id")
   ```
3. **Notebook:** use `audio_column="audio_uri"` in `BioacousticAnnotator`, and delete the `api_audio.install_widget_audio()` cell. The preview cell can call `ondio.read_flac(row.audio_uri, start, end)` directly.
4. **`api_audio.py`:** keep `connect` and `query_api` (still needed for recording and deployment metadata). Delete:
   - `presigned_url`, plus `_s3_keys` and `_presigned`
   - the `HttpBackend.size` patch (only GET-only presigned URLs reject HEAD; normal S3 access doesn't)
   - `install_widget_audio`
   - most likely `read_clip`, `recording_duration`, `END_MARGIN_SEC` and `PADDING_RATIOS`. These are ondio workarounds, and jba uses its own S3 reader.
5. **Verify** with the same checks as before:
   - Run the notebook headless (`jupyter nbconvert --execute`).
   - Call `jupyter_bioacoustic.audio.read_segment(uri, start - 2, dur + 4)` for every clip with rank 1 and confidence ≥ 0.5 (609 clips in the current data).
   - Include the edge cases: a negative start, windows ending at the end of recordings 3 and 4, and the mid-file windows in recording 4 at ~1012–1022 s that broke ondio's default padding.
   - Watch `/tmp/jba_audio_cache`. If it fills with whole files, jba's partial read failed and fell back to a full download, about 1.8 GB per recording.

## Things to recheck after switching
- **jba's S3 partial read** only handles FLAC; it parses the header and uses pydub. Confirm it works for these files and doesn't fall back to full downloads.
- **If jba's reader has the same end-of-file or mid-file problems as ondio**, keep `install_widget_audio` but have it call `ondio.read_flac("s3://...", padding_ratio=1.0)` with the end clamp. That is still simpler, because there's no presigning and no size patch.
