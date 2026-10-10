# Real dashcam drive: I-280, San Francisco Peninsula (30 July 2018)

**Video:** real recording, 179 s, 1164x874 at 20 fps.
**GPS:** 1,708 real u-blox fixes recorded in the same car at the same time. Nothing is synthesised.
**Source:** [comma2k19](https://github.com/commaai/comma2k19) by comma.ai, MIT licence. Drive `b0c9d2329ad1606b|2018-07-30--13-44-30`, segments 6–8.

## How video and GPS are aligned

comma's logs timestamp every video frame and every GPS fix on the same device clock, and each fix also carries its satellite UTC time.

- **MP4:** `creation_time` is the UTC time of the first frame. The leading frames are trimmed so the video starts on a whole second, because MP4 only stores whole seconds. The remaining error is about 9 ms.
- **GPX:** every fix keeps its real UTC time, plus speed (m/s), course and elevation.

The pipeline aligns the two from this metadata. No `--offset` is needed.

## Regenerate, or fetch another stretch

    .venv/bin/python tools/fetch_comma2k19.py --drive "2018-07-30--13-44-30" --segments 6 7 8 \
        --out data/real/i280_2018-07-30
    .venv/bin/python -m hazardmap.pipeline --video data/real/i280_2018-07-30.mp4 \
        --gps data/real/i280_2018-07-30.gpx --out runs/i280-real-2018-07-30 --camera dashcam

The MP4 is git-ignored. Re-run the first command after cloning to get it.
