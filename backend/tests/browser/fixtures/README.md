# Readiness video fixture

`readiness.webm` is a one-second 32x32 VP8 test pattern generated locally, with
no audio or user content. It exercises actual video decoding in Chromium without
requiring FFmpeg in browser-test CI. Generate with FFmpeg 8.1.1:

```text
ffmpeg -f lavfi -i testsrc2=size=32x32:rate=10:duration=1 -an -c:v libvpx -b:v 20k readiness.webm
```

This is a probe regression fixture, not the speech corpus for a cloud experiment.
