"""Music theory helpers: keys, Camelot wheel, and speed <-> semitone math.

When a track is sped up or slowed down by *resampling* (the classic
"nightcore" / "slowed" effect, like changing a record's RPM), pitch and tempo
change together:

    semitones = 12 * log2(speed)        speed = 2 ** (semitones / 12)

Only speeds of the form 2**(n/12) move the song by a *whole* number of
semitones. Any other speed (e.g. 0.90x = -1.82 semitones) leaves the song
"between keys" - out of tune with standard A=440 Hz instruments and
other songs, which matters when you mix, sing along or layer instruments.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT_NAMES = {"C#": "Db", "D#": "Eb", "F#": "Gb", "G#": "Ab", "A#": "Bb"}

INTERVAL_NAMES = {
    0: "unison", 1: "minor 2nd", 2: "major 2nd", 3: "minor 3rd", 4: "major 3rd",
    5: "perfect 4th", 6: "tritone", 7: "perfect 5th", 8: "minor 6th",
    9: "major 6th", 10: "minor 7th", 11: "major 7th", 12: "octave",
}


@dataclass(frozen=True)
class Key:
    tonic: int  # pitch class 0..11, C = 0
    minor: bool

    @property
    def name(self) -> str:
        n = NOTE_NAMES[self.tonic]
        alt = FLAT_NAMES.get(n)
        label = f"{n}/{alt}" if alt else n
        return f"{label} {'minor' if self.minor else 'major'}"

    @property
    def short(self) -> str:
        return NOTE_NAMES[self.tonic] + ("m" if self.minor else "")

    @property
    def camelot(self) -> str:
        """Camelot wheel code used by DJs (8B = C major, 8A = A minor)."""
        major_tonic = (self.tonic + 3) % 12 if self.minor else self.tonic
        number = ((major_tonic * 7) % 12 + 7) % 12 + 1
        return f"{number}{'A' if self.minor else 'B'}"

    def transpose(self, semitones: int) -> "Key":
        return Key((self.tonic + semitones) % 12, self.minor)

    @property
    def relative(self) -> "Key":
        return Key((self.tonic + 3) % 12, False) if self.minor else Key((self.tonic - 3) % 12, True)


def semitones_for_speed(speed: float) -> float:
    return 12.0 * math.log2(speed)


def speed_for_semitones(semitones: float) -> float:
    return 2.0 ** (semitones / 12.0)


def tuning_offset_cents(semitones: float) -> float:
    """How far (in cents, -50..50) a shift lands from the nearest whole semitone."""
    return (semitones - round(semitones)) * 100.0


def camelot_distance(a: Key, b: Key) -> int:
    """Steps around the Camelot wheel (0 = same, 1 = harmonic neighbour)."""
    na, nb = int(a.camelot[:-1]), int(b.camelot[:-1])
    d = abs(na - nb) % 12
    d = min(d, 12 - d)
    return d + (0 if a.minor == b.minor else 1)


@dataclass
class SpeedSuggestion:
    semitones: int
    speed: float
    new_key: Key | None
    new_bpm: float | None
    stars: int
    label: str
    description: str

    @property
    def percent(self) -> str:
        return f"{self.speed * 100:.1f}%"


# (stars, short label, explanation) per semitone shift
_SHIFT_INFO: dict[int, tuple[int, str, str]] = {
    -1: (4, "Subtle slow", "Barely noticeable, a bit warmer and heavier. Keeps the original vibe."),
    -2: (5, "Slowed (light)", "Popular gentle 'slowed' sound, a whole tone lower. Vocals still natural."),
    -3: (5, "Slowed + reverb", "The classic 'slowed + reverb' sweet spot (~0.84x). Dreamy and deep."),
    -4: (4, "Slowed (heavy)", "Deep, moody and lazy. Vocals start to sound noticeably lower."),
    -5: (4, "Perfect 4th down", "Lands on a circle-of-fifths neighbour key: very harmonic, very slow."),
    -6: (1, "Tritone down", "Most distant key from the original - sounds strange; rarely used."),
    -7: (3, "Perfect 5th down", "Neighbour key on the circle of fifths. Extremely slow and dark."),
    1: (4, "Subtle boost", "Slightly brighter and more energetic, still sounds natural."),
    2: (4, "Sped up (light)", "A whole tone higher. Common 'sped up' TikTok version."),
    3: (5, "Nightcore (light)", "Nightcore-style energy (~1.19x), bright and bouncy vocals."),
    4: (5, "Nightcore", "Classic nightcore pace (~1.26x). High, fast, euphoric."),
    5: (4, "Perfect 4th up", "Circle-of-fifths neighbour key: harmonic, very fast and chipmunk-like."),
    6: (1, "Tritone up", "Most distant key from the original - sounds strange; rarely used."),
    7: (3, "Perfect 5th up", "Neighbour key on the circle of fifths. Extreme chipmunk effect (1.5x)."),
}


def speed_suggestions(key: Key | None = None, bpm: float | None = None,
                      max_shift: int = 7) -> list[SpeedSuggestion]:
    """Best speed values: every one keeps the track in tune on a real key."""
    out = []
    for n in range(-max_shift, max_shift + 1):
        if n == 0 or n not in _SHIFT_INFO:
            continue
        stars, label, desc = _SHIFT_INFO[n]
        speed = speed_for_semitones(n)
        new_key = key.transpose(n) if key else None
        if key is not None:
            interval = INTERVAL_NAMES[abs(n)]
            desc = f"{desc} {interval.capitalize()} {'down' if n < 0 else 'up'}: {key.short} -> {new_key.short} ({new_key.camelot})."
            if camelot_distance(key, new_key) <= 1:
                desc += " Mixes perfectly with the original (Camelot neighbour)."
        out.append(SpeedSuggestion(
            semitones=n,
            speed=speed,
            new_key=new_key,
            new_bpm=bpm * speed if bpm else None,
            stars=stars,
            label=label,
            description=desc,
        ))
    return out


def describe_speed(speed: float, key: Key | None = None, bpm: float | None = None,
                   keep_pitch: bool = False, extra_semitones: float = 0.0) -> str:
    """Human readable summary of what a speed/pitch setting does to the song."""
    total = extra_semitones if keep_pitch else semitones_for_speed(speed) + extra_semitones
    parts = [f"Tempo x{speed:.3f}"]
    if bpm:
        parts.append(f"BPM {bpm:.0f} -> {bpm * speed:.0f}")
    parts.append(f"Pitch {total:+.2f} semitones")
    cents = tuning_offset_cents(total)
    if abs(cents) < 3:
        if key is not None:
            parts.append(f"Key {key.short} -> {key.transpose(round(total)).short}")
        parts.append("in tune")
    else:
        nearest = round(total)
        parts.append(f"OUT OF TUNE by {cents:+.0f} cents (between keys)")
        if not keep_pitch:
            parts.append(f"tip: use x{speed_for_semitones(nearest - extra_semitones):.3f}")
    return "  |  ".join(parts)
