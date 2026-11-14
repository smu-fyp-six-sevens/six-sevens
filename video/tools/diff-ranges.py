#!/usr/bin/env python3
"""diff-ranges.py OLD.mp4 NEW.mp4 [--handles SECONDS] - which frames changed between two renders of the film.

Decodes both at 480x270 grey, counts the pixels that changed by more than encoding noise, and prints the changed ranges (merged
when closer than 1 s), padded by the handles, as JSON: [[first_frame, last_frame], ...] at 30 fps.
"""
import json, subprocess, sys
import numpy as np
old, new = sys.argv[1], sys.argv[2]
handles = float(sys.argv[sys.argv.index("--handles") + 1]) if "--handles" in sys.argv else 1.0
W, H, FPS = 480, 270, 30
def frames(path):
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", path, "-vf", f"scale={W}:{H},format=gray", "-f", "rawvideo", "-"], stdout=subprocess.PIPE)
    while True:
        b = p.stdout.read(W * H)
        if len(b) < W * H: break
        yield np.frombuffer(b, np.uint8).astype(np.int16)
scores = [int((np.abs(a - b) > 18).sum()) for a, b in zip(frames(old), frames(new))]
# a frame changed when at least 25 pixels moved by more than encoding noise does
changed = [i for i, s in enumerate(scores) if s >= 6]
ranges = []
for i in changed:
    if ranges and i - ranges[-1][1] <= FPS: ranges[-1][1] = i
    else: ranges.append([i, i])
n = len(scores)
pad = round(handles * FPS)
print(json.dumps({"frames": n, "changed": [[a, b] for a, b in ranges], "with_handles": [[max(0, a - pad), min(n - 1, b + pad)] for a, b in ranges]}))
