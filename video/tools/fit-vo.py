#!/usr/bin/env python3
"""fit-vo.py - fit each section's caption timing to its voice clips (public/vo/cues.json).

Each caption's voice starts VO_LEAD s after the caption rises. When a caption would rise before the previous line
has finished (plus GAP), that caption and every later time knob in the section move later by the difference, and
the section's length grows by the same; a section also grows if its last line would run past its end. Idempotent:
run it again after replacing a clip. Edits src/timing.ts in place.
"""
import json, re, sys
VO_LEAD, GAP, TAIL = 0.05, 0.15, 0.35
# seconds a section holds after its last event, where it needs more than the default
HOLD = {"OVERWHELM3": 2.15, "DETECT": 1.0, "SCORE": 1.0, "TAMPER": 0.6, "PORTAL2": 0.8, "CTA": 0.6, "DASHBOARD": 0.6}
NOT_TIMES = {"length", "ramp", "docPop", "stretchEase", "dim", "pushIn", "pulseEvery", "fidget", "pileStagger", "rowLand"}
src = open("src/timing.ts").read()
cues = json.load(open("public/vo/cues.json"))

def block(name):
    i = src.index(f"export const {name} = {{"); j = src.index("\n};", i)
    return i, j

def knobs(name):
    i, j = block(name)
    return {m.group(1): float(m.group(2)) for m in re.finditer(r"\n  (\w+): (-?[0-9.]+),", src[i:j])}

def shift(name, frm, delta, placed):
    """Move every time knob >= frm (and every entry of a time array) later by delta, except placed captions."""
    global src
    i, j = block(name)
    body = src[i:j]
    def num(m):
        key, v = m.group(1), float(m.group(2))
        if key in NOT_TIMES or key in placed or v < frm - 1e-9: return m.group(0)
        return f"\n  {key}: {round(v + delta, 2)},"
    body = re.sub(r"\n  (\w+): (-?[0-9.]+),", num, body)
    def arr(m):
        vals = [float(x) for x in m.group(2).split(",")]
        return f"\n  {m.group(1)}: [" + ", ".join(str(round(v + delta, 2)) if v >= frm - 1e-9 else str(v) for v in vals) + "],"
    body = re.sub(r"\n  (\w+): \[([-0-9., ]+)\],", arr, body)
    src = src[:i] + body + src[j:]

def setk(name, key, value):
    global src
    i, j = block(name)
    body = re.sub(rf"\n  {key}: -?[0-9.]+,", f"\n  {key}: {round(value, 2)},", src[i:j], count=1)
    src = src[:i] + body + src[j:]

for name in dict.fromkeys(c["timing"] for c in cues):
    placed, end = set(), -1.0
    for c in [c for c in cues if c["timing"] == name]:
        k = knobs(name)[c["knob"]]
        start = max(k, end + GAP - VO_LEAD) if end >= 0 else k
        if start > k + 1e-6:
            shift(name, k, start - k, placed)
        placed.add(c["knob"])
        end = knobs(name)[c["knob"]] + VO_LEAD + c["dur"]
    # The section ends shortly after its last line or its last event, whichever is later (the original end hold
    # is not carried along, so a shifted section does not drag). The hook keeps room for the sigh after its ping.
    k = knobs(name)
    last_event = max(v for key, v in k.items() if key not in NOT_TIMES)
    new_len = max(end + TAIL, last_event + HOLD.get(name, 1.0))
    setk(name, "length", new_len)
    # captions that must clear before the next one rises
    if name == "OVERWHELM3": setk(name, "title1Out", knobs(name)["wayIn"] - 0.3)
    if name == "PORTAL2": setk(name, "introOut", knobs(name)["titleIn"] - 0.2)
open("src/timing.ts", "w").write(src)
for name in dict.fromkeys(c["timing"] for c in cues):
    print(name, "length", knobs(name)["length"])
