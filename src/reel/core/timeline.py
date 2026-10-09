"""Scene timing: where each scene starts on the global timeline.

Transitions *overlap* the outgoing and incoming scenes (like a crossfade in an
editor): scene i+1 starts ``overlap_i`` seconds before scene i ends, and the
overlapped frames are rendered by blending both scenes.  Total reel duration is
therefore ``sum(durations) - sum(overlaps)``; that is the number the 45-60 s
budget is checked against.

Both an exact float view (for the linter) and a whole-frame view (for the
renderer) are provided; the frame view is what actually decides every frame.
"""

from __future__ import annotations

from dataclasses import dataclass

from reel.core.spec import ReelSpec, SceneSpec


def overlap_seconds(scene: SceneSpec, nxt: SceneSpec | None) -> float:
    """Seconds that ``scene``'s transition_out overlaps with ``nxt`` (0 for cuts / last scene)."""
    t = scene.transition_out
    if nxt is None or t.type == "cut" or t.duration <= 0:
        return 0.0
    return float(min(t.duration, scene.duration_sec, nxt.duration_sec))


@dataclass(frozen=True)
class SceneSlot:
    index: int
    scene_id: str
    start_frame: int
    n_frames: int
    overlap_frames: int  # frames shared with the NEXT scene (its start_frame is end - overlap)
    start_sec: float
    duration_sec: float
    overlap_sec: float

    @property
    def end_frame(self) -> int:
        return self.start_frame + self.n_frames


@dataclass(frozen=True)
class Active:
    """One scene visible on a global frame."""

    slot: int
    local_frame: int


@dataclass(frozen=True)
class Timeline:
    fps: int
    slots: tuple[SceneSlot, ...]
    total_frames: int
    total_sec: float  # exact: sum(durations) - sum(overlaps)

    @property
    def total_sec_frames(self) -> float:
        return self.total_frames / self.fps

    def active(self, frame: int) -> list[Active]:
        """Scenes visible on global ``frame`` (1, or 2 during a transition), outgoing first."""
        out = [
            Active(s.index, frame - s.start_frame)
            for s in self.slots
            if s.start_frame <= frame < s.end_frame
        ]
        return out

    def transition_progress(self, frame: int) -> float | None:
        """Progress in (0,1) if ``frame`` is inside a transition overlap, else None."""
        for s in self.slots:
            if s.overlap_frames > 0:
                lo = s.end_frame - s.overlap_frames
                if lo <= frame < s.end_frame:
                    return float((frame - lo + 1) / (s.overlap_frames + 1))
        return None

    def slot_at_time(self, t: float) -> SceneSlot:
        f = round(t * self.fps)
        for s in self.slots:
            if f < s.end_frame - s.overlap_frames or s is self.slots[-1]:
                return s
        return self.slots[-1]

    def global_time(self, slot: int, local_t: float) -> float:
        return self.slots[slot].start_frame / self.fps + local_t


def compute_timeline(spec: ReelSpec) -> Timeline:
    fps = spec.meta.fps
    scenes = spec.scenes
    n = [max(1, round(s.duration_sec * fps)) for s in scenes]
    ov_sec = [
        overlap_seconds(s, scenes[i + 1] if i + 1 < len(scenes) else None)
        for i, s in enumerate(scenes)
    ]
    ov = [0] * len(scenes)
    for i in range(len(scenes) - 1):
        want = round(ov_sec[i] * fps)
        # an overlap can never swallow a whole scene or collide with the previous overlap
        room_here = n[i] - (ov[i - 1] if i > 0 else 0) - 1
        room_next = n[i + 1] - 1
        ov[i] = max(0, min(want, room_here, room_next))

    slots: list[SceneSlot] = []
    start = 0
    for i, s in enumerate(scenes):
        start_sec = start / fps
        slots.append(
            SceneSlot(
                index=i,
                scene_id=s.id,
                start_frame=start,
                n_frames=n[i],
                overlap_frames=ov[i],
                start_sec=start_sec,
                duration_sec=s.duration_sec,
                overlap_sec=ov_sec[i],
            )
        )
        start += n[i] - ov[i]
    total_frames = slots[-1].end_frame
    total_sec = sum(s.duration_sec for s in scenes) - sum(ov_sec)
    return Timeline(fps=fps, slots=tuple(slots), total_frames=total_frames, total_sec=total_sec)


def total_duration_sec(spec: ReelSpec) -> float:
    """Exact reel length in seconds (durations minus transition overlaps)."""
    return compute_timeline(spec).total_sec
