# Frog call labeling protocol

We're building a training dataset for a frog call classifier. You'll label short audio segments from your own recordings, and the labels you save become the positive (frog) and negative (no frog) examples.

## Overview

1. **Set up.** Follow steps 1–4 in the README.
2. **Input.** Open `notebooks/03_frog_sampling.ipynb` and fill in step 1: a short name for your dataset, your name and initials, where your audio is, how your files are named, and your time zone. Optionally limit sampling to certain `SITES`, `HOURS` or `MONTHS`, for example sites near ponds and night hours.
3. **Sample.** Run the notebook cell. For each site, it picks 5 nights spread across your recordings and 5 random 10-second segments spread through each night, at least 60 seconds apart (up to 25 per site). Then it cuts them into clips.
4. **Annotate.** Label every segment in the notebook's annotator (step 2).
5. **Need more segments?** Once every segment is labeled, use step 3 of the notebook to add a round of up to 25 more per site, from nights not sampled yet. A site stops at 75 segments.
6. **Send.** Run the notebook's step 4 (Export) and send us the zip file it creates in `exports/`.


## Labeling a segment

Listen to the whole 10 seconds and look at the spectrogram, then answer **Any frog call in the window?**

| If… | Then… |
|---|---|
| A frog calls anywhere in the segment | Click to center the yellow 3-second window on the **clearest** frog call. Answer **Yes**. Select additional species and clarity of each call.|
| No frog calls anywhere in the segment | Don't place a window. Answer **No**. |
| You can't tell whether a sound is a frog | Center the window on that sound. Answer **Unsure**. A wrong Yes or No harms the model more than an Unsure does. |
| Clipping, a dead or silent microphone, or noise that hides everything | Answer **Recording unusable**. |

Whatever your answer, tick every non-frog sound you hear anywhere in the 10 seconds. Click **Submit** to save. Add notes for anything unusual. Your answers save as you go, so you can stop and come back later.

## Ready to annotate a new dataset? 
- Change `SOURCE`, and update the metadata in step 1. This will create a new export subfolder for your new annotation dataset and parameters. Continue with the protocol above

## Please don't

- Skip segments. Answer **Unsure** instead.
- Edit `segments.csv` or the clips, rename audio files, or edit the output file by hand.
- Edit `frogs.yaml`. It holds the sampling design, which is the same for everyone.
- Change `SOURCE`, `PATH_PATTERN`, `SITES`, `HOURS`, `MONTHS` or the time settings in step 1 after your first round. Doing so will create a second dataset.

## Methods

**Data selection.** Recording segments for annotation were selected by stratified random sampling, separately at each site. Sampling was restricted to sites, months, and hours when frogs were likely to be calling (e.g., sites adjacent to ponds, March–May, 18:00–06:00 local time). Recording times were read from file names and converted to local time. Each night ran from 12:00 to 12:00 the next day. Recordings without a timestamp were not limited by month or hour, and each counted as its own night. At each site, nights with eligible audio were sorted by date and split into five consecutive groups of equal size. One night was drawn at random from each group. Each selected night's eligible audio was pooled across recordings and split into five equal strata. One 10-s segment was drawn at random from each stratum, with at least 60 s between segments. This gave up to five segments per night and 25 per site. Nights with too little audio, and sites with fewer than five eligible nights, gave fewer segments. When more labeled data were needed, extra rounds of up to 25 segments per site used the same design. These rounds drew from unsampled nights first and excluded audio within 60 s of earlier segments. Each site was capped at 75 segments to keep the dataset balanced and not dominated by a single site or recording. A fixed random seed made the sample reproducible.

**Annotation.**

Segments were annotated in Jupyter notebooks using jupyter-bioacoustic (v0.9.3). For each 10-s segment, annotators listened to the full segment while viewing its spectrogram and recorded whether a frog was calling. If a frog called anywhere in the segment, the annotator centered a fixed 3-s window on the clearest call and identified every frog species calling within the window (up to three). A call counted as within the window if at least half of the call fell inside. Each species call in the window was rated as clear (standing out from the background) or faint (barely distinguishable), and any other frog species calling elsewhere in the segment was recorded separately. Frog calls that could not be identified to species were labeled as unsure. Annotators were instructed to label a segment as unsure, placing the window on the sound in question and giving a reason, whenever they were in doubt rather than guess. Segments with no frog call anywhere within the 10 s were labeled negative. For every segment, annotators recorded whether any of the following non-frog sounds were present in the 10 s: insects, birds or mammals, rain, wind, flowing water, human noise (voices, traffic or machinery), and handling or microphone noise. They labeled a segment as unusable when microphone clipping or failure obscured it. Positive clips were the annotated 3-s windows, and negative clips were the central 3 s of negative segments. Unsure and unusable segments were excluded.
