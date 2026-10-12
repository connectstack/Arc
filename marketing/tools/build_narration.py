"""Speak the narration lines with an offline macOS voice and measure them: writes audio/vo_<id>.wav (48 kHz mono) and narration_timing.json."""
import json
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent / "product-video"
cfg = json.loads((root / "narration.json").read_text(encoding="utf-8"))
(root / "audio").mkdir(exist_ok=True)
timing = {}
for line in cfg["lines"]:
    aiff = root / "audio" / f"vo_{line['id']}.aiff"
    wav = root / "audio" / f"vo_{line['id']}.wav"
    # a short pause after each sentence reads more clearly than the voice's own
    text = line["text"].replace(". ", ". [[slnc 220]] ")
    subprocess.run(["say", "-v", cfg["voice"], "-r", str(cfg["rate"]), "-o", str(aiff), text], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(aiff), "-ar", "48000", "-ac", "1", "-af", "silenceremove=start_periods=1:start_threshold=-50dB,areverse,silenceremove=start_periods=1:start_threshold=-50dB,areverse", str(wav)], check=True)
    dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(wav)]).decode().strip())
    timing[line["id"]] = {"file": f"audio/vo_{line['id']}.wav", "dur": round(dur, 3), "text": line["text"]}
    aiff.unlink()
    print(f"{line['id']}: {dur:5.2f} s  {line['text'][:60]}")
(root / "narration_timing.json").write_text(json.dumps(timing, indent=1, ensure_ascii=False), encoding="utf-8")
print("total speech:", round(sum(v["dur"] for v in timing.values()), 2), "s")
