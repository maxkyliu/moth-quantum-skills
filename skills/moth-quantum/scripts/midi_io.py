"""Minimal Standard MIDI File writing and reading. Standard library only.

Enough for block-chord tracks sent to blur-midi-v1 and for reading what it returns:
format 0/1 files, running status, meta and sysex events skipped, note-on with velocity 0
treated as note-off. Times are in quarter notes.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

TICKS_PER_QUARTER = 480


@dataclass(frozen=True)
class Note:
    onset: float        # quarter notes from the start
    pitch: int
    duration: float     # quarter notes
    velocity: int


def _varlen(value: int) -> bytes:
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append(0x80 | (value & 0x7F))
        value >>= 7
    return bytes(reversed(out))


def write_midi(path: Path, notes: list[Note], bpm: float) -> None:
    """Write a format-0 file with one tempo event and the given notes on channel 0."""
    events = []   # (tick, order, bytes): note-offs sort before note-ons at the same tick
    for n in notes:
        on = round(n.onset * TICKS_PER_QUARTER)
        off = round((n.onset + n.duration) * TICKS_PER_QUARTER)
        events.append((on, 1, bytes([0x90, n.pitch, n.velocity])))
        events.append((off, 0, bytes([0x80, n.pitch, 0])))
    events.sort(key=lambda e: (e[0], e[1]))
    tempo = round(60_000_000 / bpm)
    track = bytearray(b"\x00\xff\x51\x03" + tempo.to_bytes(3, "big"))
    last = 0
    for tick, _, data in events:
        track += _varlen(tick - last) + data
        last = tick
    track += b"\x00\xff\x2f\x00"
    header = b"MThd" + struct.pack(">IHHH", 6, 0, 1, TICKS_PER_QUARTER)
    path.write_bytes(header + b"MTrk" + struct.pack(">I", len(track)) + bytes(track))


def _read_varlen(data: bytes, pos: int) -> tuple[int, int]:
    value = 0
    while True:
        byte = data[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, pos


def _read_track(data: bytes, ticks: int) -> list[Note]:
    notes, sounding = [], {}          # (channel, pitch) -> (start tick, velocity)
    pos, tick, status = 0, 0, None
    while pos < len(data):
        delta, pos = _read_varlen(data, pos)
        tick += delta
        if data[pos] & 0x80:
            status = data[pos]
            pos += 1
        if status in (0xFF,):                       # meta: type, length, data
            pos += 1
            length, pos = _read_varlen(data, pos)
            pos += length
            continue
        if status in (0xF0, 0xF7):                  # sysex
            length, pos = _read_varlen(data, pos)
            pos += length
            continue
        kind, channel = status & 0xF0, status & 0x0F
        size = 1 if kind in (0xC0, 0xD0) else 2
        args = data[pos:pos + size]
        pos += size
        if kind == 0x90 and args[1] > 0:
            sounding[(channel, args[0])] = (tick, args[1])
        elif kind in (0x80, 0x90):
            start = sounding.pop((channel, args[0]), None)
            if start:
                notes.append(Note(start[0] / ticks, args[0], (tick - start[0]) / ticks, start[1]))
    return notes


def read_midi(path: Path) -> list[Note]:
    """All notes in all tracks, sorted by onset then pitch."""
    data = path.read_bytes()
    if data[:4] != b"MThd":
        raise ValueError(f"{path} is not a Standard MIDI File")
    length, _, track_count, ticks = struct.unpack(">IHHH", data[4:14])
    if ticks & 0x8000:
        raise ValueError("SMPTE time division is not supported")
    pos, notes = 8 + length, []
    for _ in range(track_count):
        if data[pos:pos + 4] != b"MTrk":
            raise ValueError("malformed track chunk")
        size = struct.unpack(">I", data[pos + 4:pos + 8])[0]
        notes += _read_track(data[pos + 8:pos + 8 + size], ticks)
        pos += 8 + size
    return sorted(notes, key=lambda n: (n.onset, n.pitch))
