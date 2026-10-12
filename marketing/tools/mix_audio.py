"""Mix the product video's soundtrack: the voice-over lines at their times, Reel's own generated music bed ducked under the voice, and the
sound effects (clicks, whooshes ...) at their times, then master it to about -16 LUFS. Reads product-video/audio_schedule.json.
Usage: python mix_audio.py [out.wav]"""
import json
import subprocess
import sys
from pathlib import Path

pv = Path(__file__).resolve().parent.parent / "product-video"
audio = pv / "audio"
sched = json.loads((pv / "audio_schedule.json").read_text())
dur = sched["duration"]
out = Path(sys.argv[1]) if len(sys.argv) > 1 else pv / "mix.wav"


def ff(args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


# ---------------------------------------------------------------------------------------------------------------- voice bus
inputs, chains, labels = [], [], []
for i, v in enumerate(sched["vo"]):
    inputs += ["-i", str(pv / v["file"])]
    ms = round(v["t"] * 1000)
    chains.append(f"[{i}:a]highpass=f=90,acompressor=threshold=-21dB:ratio=3:attack=5:release=90:makeup=3,adelay={ms}:all=1[v{i}]")
    labels.append(f"[v{i}]")
chains.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0,apad=whole_dur={dur},atrim=0:{dur},pan=stereo|c0=c0|c1=c0[vo]")
ff([*inputs, "-filter_complex", ";".join(chains), "-map", "[vo]", "-ar", "48000", str(pv / "bus_vo.wav")])

# ---------------------------------------------------------------------------------------------------------------- effects bus
inputs, chains, labels = [], [], []
for i, s in enumerate(sched["sfx"]):
    inputs += ["-i", str(audio / f"sfx_{s['name']}.wav")]
    ms = max(0, round(s["t"] * 1000))
    chains.append(f"[{i}:a]volume={s['gain']}dB,adelay={ms}:all=1,pan=stereo|c0=c0|c1=c0[s{i}]")
    labels.append(f"[s{i}]")
chains.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0,apad=whole_dur={dur},atrim=0:{dur}[sfx]")
ff([*inputs, "-filter_complex", ";".join(chains), "-map", "[sfx]", "-ar", "48000", str(pv / "bus_sfx.wav")])

# ---------------------------------------------------------------------------------------------------------------- music under the voice, then the master
music = f"[0:a]atrim=0:{dur},asetpts=N/SR/TB,afade=t=in:d=1.2,afade=t=out:st={dur - 3.0}:d=3.0,volume=-11dB[m]"
duck = "[m][sc]sidechaincompress=threshold=0.012:ratio=9:attack=12:release=420:makeup=1[md]"
graph = ";".join(
    [
        music,
        "[1:a]asplit=2[vo][sc]",
        duck,
        "[md][vo][2:a]amix=inputs=3:normalize=0:weights='1 1 0.9'[mix]",
        "[mix]alimiter=limit=0.95,loudnorm=I=-16:TP=-1.5:LRA=9[out]",
    ]
)
ff(["-i", str(audio / "music_corporate.wav"), "-i", str(pv / "bus_vo.wav"), "-i", str(pv / "bus_sfx.wav"), "-filter_complex", graph, "-map", "[out]", "-ar", "48000", str(out)])
print("wrote", out)
