# Frog call labeling protocol

We're building a training dataset for a frog call classifier. You'll label short audio segments from your own recordings, and the labels you save become the positive (frog) and negative (no frog) examples.

## Overview

1. **Set up.** Follow steps 1–4 in the README.
2. **Input.** Open `notebooks/03_frog_sampling.ipynb` and fill in step 1: a short name for your dataset, your name and initials, where your audio is, how your files are named, and your time zone. Optionally limit sampling to certain `SITES`, `HOURS` or `MONTHS`, for example sites near ponds and night hours.
3. **Sample.** Run the notebook with **Run All**. At each site it picks 25 random 10-second segments, spread over as many recordings and dates as possible, then cuts them into clips.
   - Never choose the segments yourself. The notebook does that.
4. **Annotate.** Label every segment in the notebook's step 2 (see below).
5. **Need more segments?** Once every segment is labeled, use step 3 of the notebook to add a round. Never delete or edit earlier rows.
6. **Send.** Run the notebook's step 4 (Export) and send us the zip file it creates in `exports/`.


## Labeling a segment

Listen to the whole 10 seconds and look at the spectrogram, then answer **Any frog call in the window?**

| If… | Then… |
|---|---|
| A frog calls anywhere in the segment | Click to center the yellow 3-second window on the **clearest** frog call. Answer **Yes**. |
| No frog calls anywhere in the segment | Don't place a window. Answer **No**. |
| You can't tell whether a sound is a frog | Center the window on that sound. Answer **Unsure**. |
| Clipping, a dead or silent microphone, or noise that hides everything | Answer **Recording unusable**. |

Click **Submit** to save. Add notes for anything unusual. Your answers save as you go, so you can stop and come back later.

## Rules for each answer

**In the window.** A call counts as in the window if at least half of it is inside the yellow window.

**Yes:**
- **Species 1** is the species of the call you centered the window on.
- Add **Species 2** and **3** only if they also call inside the window.
- Choose **Unknown frog** if you're sure it's a frog but not which species.
- **Calling index** for the window:
  - **1** = individual calls are countable and don't overlap
  - **2** = calls overlap, but you can still count callers
  - **3** = full chorus, too many to count
- **Clearest call is:**
  - **clear** = it stands out from the background
  - **faint** = you can only just hear or see it
- **Call type:** answer only if you're sure. Otherwise leave it blank.
- **Another species calling outside the window:** a frog species you heard elsewhere in the 10 seconds but not inside the window.

**No** means no frog anywhere in the 10 seconds. If any frog calls anywhere in the segment, the answer is **Yes** with the window on it. Tick every other sound you hear. Leave them all blank if it's quiet.

**Unsure.** Pick the reason. Use Unsure whenever you're in doubt. A wrong Yes or No harms the model more than an Unsure does.

## Please don't

- Skip segments. Answer **Unsure** instead.
- Edit `segments.csv` or the clips, rename audio files, or edit the output file by hand.
- Edit `frogs.yaml`. It holds the sampling design, which is the same for everyone.
- Change `SOURCE`, `PATH_PATTERN`, `SITES`, `HOURS`, `MONTHS` or the time settings in step 1 after your first round. The notebook will stop you if you do.

## Data selection approach

Audio segments for annotation were selected using stratified random sampling, applied independently at each site. Sampling was restricted to sites, months and hours likely to contain calling frogs [e.g., sites adjacent to ponds, March–May, 18:00–06:00 local time]. Month and hour restrictions were applied only to recordings with a timestamp in the file name, converted to local time. Recordings shorter than the 10-s segment length were excluded. Within each site, recordings were ordered chronologically, or by file name when no timestamp was available, and 25 non-overlapping 10-s segments were drawn. To maximize the number of recordings represented, no recording contributed a second segment until every eligible recording had contributed one. When more recordings were eligible than segments required, the ordered recordings were divided into equal consecutive groups and one recording was drawn at random from each, spreading the sample across the deployment. Segment start times were drawn uniformly at random from the eligible portion of each recording, with at least 60 s between segments from the same recording. Additional sampling rounds (up to 75 segments per site) followed the same rules, giving priority to recordings with the fewest segments and excluding audio within 60 s of earlier segments. A fixed random seed was used, so the sample is reproducible.