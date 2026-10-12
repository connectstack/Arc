# Reel Studio marketing kit

The product video and the pictures behind the sales deck. Everything here is made offline from the real product; no API keys are used.

| What | Where |
| --- | --- |
| Product video, 1080p, 71 s (voice-over, music, effects) | `reel-studio-product-video.mp4` |
| Same video for email and slides (7.5 MB) | `reel-studio-product-video-720p.mp4` |
| Poster frame and subtitles | `reel-studio-product-video-poster.jpg`, `reel-studio-product-video.srt` |
| Real 2x screenshots of every screen in the tour | `shots/` (with `flow_*.json`: where the pointer was) |
| Pictures used in the sales deck | `deck/assets/` |
| Full-size frames of real renders | `samples/` |
| Narration, audio, the frame player and intermediate files | `product-video/` |
| The tools below | `tools/` |

The sales deck itself is a Claude Slides artifact and is not stored here.

## Rebuild the video

Run everything from this folder. You need Node 24+ (it has a built-in WebSocket), ffmpeg, the project's Python environment, and Playwright's cached `chrome-headless-shell` (its path is set at the top of `tools/cdp.mjs`).

1. Start a demo workspace and fill it with real reels:
   `reel serve --no-token --workspace <empty folder> --port 8768`, then `python tools/seed_demo_workspace.py`.
2. `node tools/record.mjs ABCDE` drives the real Studio in headless Chrome and saves a 2x screenshot per step to `shots/`.
3. `python tools/build_narration.py` speaks `product-video/narration.json` with the offline macOS voice (writes `product-video/audio/vo_*.wav` and `narration_timing.json`).
4. `node tools/storyboard.mjs` lays the shots, captions and cues on the narration clock (`player/timeline.js`, `audio_schedule.json`).
5. `python tools/mix_audio.py` mixes the voice, Reel's own generated music (ducked under the voice) and the effects into `product-video/mix.wav` at about -16 LUFS.
6. `node tools/capture_frames.mjs render product-video/silent.mp4 4` draws every frame at 30 fps with the frame player, in four headless browsers at once.
7. Join the picture and the sound, then make the delivery files:

```bash
ffmpeg -y -i product-video/silent.mp4 -i product-video/mix.wav -map 0:v -map 1:a -c:v copy -c:a aac -b:a 192k -movflags +faststart -shortest product-video/master.mp4
ffmpeg -y -i product-video/master.mp4 -c:v libx264 -preset slow -crf 20 -pix_fmt yuv420p -c:a aac -b:a 192k -movflags +faststart reel-studio-product-video.mp4
ffmpeg -y -i product-video/master.mp4 -vf scale=1280:720:flags=lanczos -c:v libx264 -preset slow -crf 23 -maxrate 2600k -bufsize 5200k -pix_fmt yuv420p -c:a aac -b:a 128k -movflags +faststart reel-studio-product-video-720p.mp4
```

To change the words, edit `product-video/narration.json` and run steps 3 to 7. To change what is on screen, edit `tools/storyboard.mjs` (steps 4, 6 and 7). `node tools/capture_frames.mjs test 12.5 40` saves single frames to `product-video/test/` so you can check a moment without rendering the whole film.

The music beds and sound effects in `product-video/audio/` were generated with Reel's own engine (`reel.audio.music.generate_music`, `reel.audio.sfx.synth`) and are kept, so the steps above do not need to make them again.

## Numbers used in the deck and the video

Measured on an Apple M4 Pro with 24 GB, rendering `examples/story_50s.json` (49.7 s of video): Full HD cold render 80 s (75.5 MB file), draft 360x640 cold render 27 s, re-render of an unchanged reel 2.4 s.
