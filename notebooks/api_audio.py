"""API access and audio reading for the review notebook.

Call `connect()` once, then use `query_api`, `read_clip` and `install_widget_audio`.
"""

import os
import time

import ondio
import requests
from dotenv import load_dotenv
from ondio.backends.http import HttpBackend

API_URL = None
COOKIES = None


def connect(api_url, token_env, cookie_name, check_path="/users/me"):
    """Read the session token from .env (sent as cookie `cookie_name`) and check it."""
    global API_URL, COOKIES
    load_dotenv(override=True)
    API_URL = api_url.rstrip("/")
    COOKIES = {cookie_name: os.environ[token_env]}
    r = requests.get(f"{API_URL}{check_path}", cookies=COOKIES, timeout=30)
    if r.status_code in (401, 403):
        raise RuntimeError(f"API token rejected; refresh {token_env} in .env")
    r.raise_for_status()
    print("API token OK")


def query_api(path):
    """GET `{API_URL}{path}` with the session cookie and return the JSON body."""
    r = requests.get(f"{API_URL}{path}", cookies=COOKIES, timeout=30)
    r.raise_for_status()
    return r.json()


# The presigned URLs are signed for GET only, so the HEAD request ondio uses to get the file size
# returns 403. Get the size from a 1-byte ranged GET instead.
def _size_via_ranged_get(self, uri):
    r = self._session.get(uri, headers={"Range": "bytes=0-0"})
    self._check_status(uri, r)
    return int(r.headers["Content-Range"].rsplit("/", 1)[1])


HttpBackend.size = _size_via_ranged_get

PRESIGNED_TTL_SEC = 30 * 60
_s3_keys = {}  # recording_id -> S3 key
_presigned = {}  # recording_id -> (url, fetched_at)


def presigned_url(recording_id):
    """Presigned S3 URL for a recording, taken from the API's /media redirect (cached)."""
    cached = _presigned.get(recording_id)
    if cached and time.time() - cached[1] < PRESIGNED_TTL_SEC:
        return cached[0]
    if recording_id not in _s3_keys:
        # path is "<bucket>/<key>"; /media wants just the key
        _s3_keys[recording_id] = query_api(f"/recordings/{recording_id}")["path"].split("/", 1)[1]
    # Don't follow the redirect: we only want the presigned URL, and the cookie must not go to S3.
    r = requests.get(
        f"{API_URL}/media/{_s3_keys[recording_id]}", cookies=COOKIES, allow_redirects=False, timeout=30
    )
    r.raise_for_status()
    if "location" not in r.headers:
        raise RuntimeError(f"/media did not redirect for recording {recording_id} (HTTP {r.status_code})")
    _presigned[recording_id] = (r.headers["location"], time.time())
    return r.headers["location"]


# ondio's ranged read fails when the window ends exactly at the end of the file,
# so clamp reads to slightly before the duration in the FLAC header.
END_MARGIN_SEC = 0.5
_durations = {}  # recording_id -> seconds


def recording_duration(recording_id):
    if recording_id not in _durations:
        _durations[recording_id] = ondio.extract_flac_header(presigned_url(recording_id)).duration
    return _durations[recording_id]


# ondio guesses the byte range from the average bitrate, which can miss where the compression
# varies, so fetch more padding than its default (0.25) and retry once with a lot more.
PADDING_RATIOS = (1.0, 8.0)


def read_clip(recording_id, start_sec, end_sec):
    """(samples, sample_rate) for [start_sec, end_sec] of a recording, via a ranged S3 read."""
    start = max(0.0, float(start_sec))
    end = min(float(end_sec), recording_duration(recording_id) - END_MARGIN_SEC)
    for padding_ratio in PADDING_RATIOS[:-1]:
        try:
            return ondio.read_flac(presigned_url(recording_id), start, end, padding_ratio=padding_ratio)
        except ondio.OndioError:
            pass
    return ondio.read_flac(presigned_url(recording_id), start, end, padding_ratio=PADDING_RATIOS[-1])


def install_widget_audio():
    """Route `{API_URL}/recordings/<id>` paths in jupyter_bioacoustic through `read_clip`.

    The widget reads audio by calling `jupyter_bioacoustic.audio.read_segment(path, ...)` with no
    auth, so wrap it. Other paths go to the original reader. Safe to call more than once.
    """
    import jupyter_bioacoustic.audio as jba_audio

    original = getattr(jba_audio.read_segment, "__wrapped__", jba_audio.read_segment)
    prefix = f"{API_URL}/recordings/"

    def read_segment(path, start_sec, dur_sec, partial=True, **kwargs):
        if not path.startswith(prefix):
            return original(path, start_sec, dur_sec, partial=partial, **kwargs)
        recording_id = int(path.removeprefix(prefix).strip("/"))
        return read_clip(recording_id, start_sec, start_sec + dur_sec)

    read_segment.__wrapped__ = original
    jba_audio.read_segment = read_segment
