#!/usr/bin/env python3
"""Sightline: plan auditorium riser steps so every row keeps a clear view.

Model, all heights in mm above the front row's floor:

* The target (stage, screen or speaker) sits at ``target_height`` and
  ``target_distance`` from the eyes of row 1.
* Row n is ``target_distance + (n - 1) * row_pitch`` from the target.
* The eye line of row n must pass over the top of the head in row n - 1 by
  at least ``clearance``. The head top is the row-in-front eye height plus
  ``eye_to_head``.
* The minimum riser for each row solves that rule exactly with Fractions.
  A riser never goes below zero. Rows may also take a fixed riser from the
  room file, which is then checked rather than solved.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from dataclasses import dataclass, field, replace
from fractions import Fraction
from pathlib import Path

MAX_ROWS = 200
TIGHT_PITCH_MM = 700
STEEP_RISER_MM = 900
TALL_HEAD_MM = 200

SETTINGS = {
    "name": "text",
    "rows": "int",
    "seats_per_row": "int",
    "target_height": "num",
    "target_distance": "num",
    "row_pitch": "num",
    "eye_height": "num",
    "eye_to_head": "num",
    "clearance": "num",
    "max_riser": "num",
}
REQUIRED = ("rows", "target_height", "target_distance", "row_pitch", "eye_height")
DEFAULTS = {
    "name": "Untitled room",
    "eye_to_head": Fraction(120),
    "clearance": Fraction(120),
    "max_riser": Fraction(600),
}
POSITIVE = ("target_distance", "row_pitch", "eye_height")

KEY_RE = re.compile(r"([A-Za-z_]\w*)\s*=\s*(.*)")
ROW_RE = re.compile(r"row\s+(\d+)\s+riser\s*=\s*(.*)", re.IGNORECASE)


@dataclass(frozen=True)
class Problem:
    line: int
    message: str

    def __str__(self) -> str:
        return f"line {self.line}: {self.message}" if self.line else self.message


class RoomFileError(ValueError):
    def __init__(self, problems: list[Problem]):
        self.problems = sorted(problems, key=lambda p: p.line)
        super().__init__("; ".join(str(p) for p in self.problems))


@dataclass(frozen=True)
class Room:
    name: str
    rows: int
    target_height: Fraction
    target_distance: Fraction
    row_pitch: Fraction
    eye_height: Fraction
    eye_to_head: Fraction
    clearance: Fraction
    max_riser: Fraction
    seats_per_row: int | None = None
    riser_overrides: dict = field(default_factory=dict)
    lines: dict = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class SeatRow:
    number: int
    distance: Fraction  # horizontal mm from the target to this row's eyes
    floor: Fraction  # floor height above row 1, mm
    eye: Fraction  # eye height above row 1's floor, mm
    riser: Fraction  # step up from the row in front, mm
    minimum_riser: Fraction  # riser the clearance rule needs, floored at 0
    clearance: Fraction | None  # sight-line gap over the row in front; None for row 1
    fixed: bool
    riser_ok: bool
    clearance_ok: bool


def fmt(x) -> str:
    """Whole millimetres print as integers; anything else gets one decimal."""
    x = Fraction(x)
    if x.denominator == 1:
        return str(x.numerator)
    return f"{float(x):.1f}"


def _round(x) -> float:
    return round(float(x), 3)


def _number(source: str, integer: bool = False) -> Fraction | None:
    pattern = r"\d+" if integer else r"\d+(?:\.\d+)?"
    if re.fullmatch(pattern, source):
        return Fraction(source)
    return None


def parse_room(source: str) -> Room:
    """Parse a room file. Collects every problem, then raises RoomFileError."""
    problems: list[Problem] = []
    seen: dict[str, int] = {}
    values: dict = {}
    overrides: dict[int, tuple[Fraction, int]] = {}

    for lineno, raw in enumerate(source.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue

        row_match = ROW_RE.fullmatch(line)
        if row_match:
            row = int(row_match.group(1))
            text = row_match.group(2).strip()
            value = _number(text)
            if value is None:
                problems.append(Problem(lineno, f"riser for row {row} must be a non-negative number, got '{text}'"))
            elif row in overrides:
                problems.append(Problem(lineno, f"duplicate riser for row {row} (first set on line {overrides[row][1]})"))
            else:
                overrides[row] = (value, lineno)
            continue

        key_match = KEY_RE.fullmatch(line)
        if not key_match:
            problems.append(Problem(lineno, "expected 'key = value' or 'row N riser = value'"))
            continue

        key, text = key_match.group(1), key_match.group(2).strip()
        if key not in SETTINGS:
            problems.append(Problem(lineno, f"unknown setting '{key}'"))
            continue
        if key in seen:
            problems.append(Problem(lineno, f"duplicate setting '{key}' (first set on line {seen[key]})"))
            continue
        seen[key] = lineno

        kind = SETTINGS[key]
        if kind == "text":
            if text:
                values[key] = text
            else:
                problems.append(Problem(lineno, "name cannot be empty"))
            continue

        value = _number(text, integer=(kind == "int"))
        if value is None:
            want = "a whole number" if kind == "int" else "a non-negative number"
            problems.append(Problem(lineno, f"'{key}' must be {want}, got '{text}'"))
        else:
            values[key] = int(value) if kind == "int" else value

    for key in REQUIRED:
        if key not in seen:
            problems.append(Problem(0, f"missing required setting '{key}'"))

    if "rows" in values and not 1 <= values["rows"] <= MAX_ROWS:
        problems.append(Problem(seen["rows"], f"rows must be from 1 to {MAX_ROWS}, got {values['rows']}"))
    if "seats_per_row" in values and values["seats_per_row"] < 1:
        problems.append(Problem(seen["seats_per_row"], "seats_per_row must be at least 1"))
    for key in POSITIVE:
        if key in values and values[key] <= 0:
            problems.append(Problem(seen[key], f"'{key}' must be greater than zero"))

    riser_overrides: dict[int, Fraction] = {}
    for row, (riser, lineno) in sorted(overrides.items()):
        if row == 1:
            problems.append(Problem(lineno, "row 1 cannot take a riser; set eye_height instead"))
        elif row < 1 or ("rows" in values and row > values["rows"]):
            problems.append(Problem(lineno, f"row {row} is outside 1..{values.get('rows', '?')}"))
        else:
            riser_overrides[row] = riser

    if problems:
        raise RoomFileError(problems)

    lines = dict(seen)
    lines.update({f"row {row}": lineno for row, (_, lineno) in overrides.items()})
    return Room(
        name=values.get("name", DEFAULTS["name"]),
        rows=values["rows"],
        target_height=values["target_height"],
        target_distance=values["target_distance"],
        row_pitch=values["row_pitch"],
        eye_height=values["eye_height"],
        eye_to_head=values.get("eye_to_head", DEFAULTS["eye_to_head"]),
        clearance=values.get("clearance", DEFAULTS["clearance"]),
        max_riser=values.get("max_riser", DEFAULTS["max_riser"]),
        seats_per_row=values.get("seats_per_row"),
        riser_overrides=riser_overrides,
        lines=lines,
    )


def load_room(path: Path) -> Room:
    return parse_room(path.read_text(encoding="utf-8"))


def lint_room(room: Room) -> list[Problem]:
    """Warnings about settings that parse but are unusual for a real hall."""
    warnings = []
    if room.row_pitch < TIGHT_PITCH_MM:
        warnings.append(Problem(
            room.lines.get("row_pitch", 0),
            f"row_pitch {fmt(room.row_pitch)} mm is tight; rule of thumb is {TIGHT_PITCH_MM} mm or more",
        ))
    if room.max_riser > STEEP_RISER_MM:
        warnings.append(Problem(
            room.lines.get("max_riser", 0),
            f"max_riser {fmt(room.max_riser)} mm is steeper than {STEEP_RISER_MM} mm",
        ))
    if room.eye_to_head > TALL_HEAD_MM:
        warnings.append(Problem(
            room.lines.get("eye_to_head", 0),
            f"eye_to_head {fmt(room.eye_to_head)} mm is unusually large",
        ))
    return warnings


def distance_to_row(room: Room, n: int) -> Fraction:
    return room.target_distance + (n - 1) * room.row_pitch


def needed_eye(room: Room, eye_prev: Fraction, n: int) -> Fraction:
    """Lowest eye height for row n that clears the head in row n - 1 by `clearance`."""
    d_prev, d = distance_to_row(room, n - 1), distance_to_row(room, n)
    h = room.target_height
    return h + (eye_prev + room.eye_to_head + room.clearance - h) * d / d_prev


def plan(room: Room) -> list[SeatRow]:
    h, eh, clearance = room.target_height, room.eye_to_head, room.clearance
    rows = [SeatRow(
        number=1,
        distance=distance_to_row(room, 1),
        floor=Fraction(0),
        eye=room.eye_height,
        riser=Fraction(0),
        minimum_riser=Fraction(0),
        clearance=None,
        fixed=False,
        riser_ok=True,
        clearance_ok=True,
    )]
    eye = room.eye_height
    floor = Fraction(0)
    for n in range(2, room.rows + 1):
        d_prev, d = distance_to_row(room, n - 1), distance_to_row(room, n)
        head_in_front = eye + eh
        minimum = max(Fraction(0), needed_eye(room, eye, n) - eye)
        fixed = n in room.riser_overrides
        riser = room.riser_overrides[n] if fixed else minimum
        eye = eye + riser
        floor = floor + riser
        sight_over_front_row = h + (eye - h) * d_prev / d
        gap = sight_over_front_row - head_in_front
        rows.append(SeatRow(
            number=n,
            distance=d,
            floor=floor,
            eye=eye,
            riser=riser,
            minimum_riser=minimum,
            clearance=gap,
            fixed=fixed,
            riser_ok=riser <= room.max_riser,
            clearance_ok=gap >= clearance,
        ))
    return rows


def summarise(room: Room, rows: list[SeatRow]) -> dict:
    clearances = [r.clearance for r in rows if r.clearance is not None]
    return {
        "rows": len(rows),
        "seats": room.rows * room.seats_per_row if room.seats_per_row else None,
        "total_rise_mm": _round(rows[-1].floor),
        "back_eye_mm": _round(rows[-1].eye),
        "min_clearance_mm": _round(min(clearances)) if clearances else None,
        "clearance_failures": [r.number for r in rows if not r.clearance_ok],
        "riser_failures": [r.number for r in rows if not r.riser_ok],
        "passed": all(r.clearance_ok and r.riser_ok for r in rows),
    }


def explain(room: Room, n: int) -> list[str]:
    rows = plan(room)
    if not 1 <= n <= room.rows:
        raise ValueError(f"row must be from 1 to {room.rows}, got {n}")
    row = rows[n - 1]
    if n == 1:
        return [f"Row 1 is the reference row: eye height {fmt(row.eye)} mm, nothing in front to clear."]

    prev = rows[n - 2]
    h, eh, c = room.target_height, room.eye_to_head, room.clearance
    needed = needed_eye(room, prev.eye, n)
    out = [
        f"Row {n}: {fmt(row.distance)} mm from the target; row {n - 1} is {fmt(prev.distance)} mm away.",
        f"Head top in front: eye {fmt(prev.eye)} + eye_to_head {fmt(eh)} = {fmt(prev.eye + eh)} mm.",
        "Lowest eye that clears it by the clearance:",
        "  E >= H + (E_prev + eye_to_head + clearance - H) * D / D_prev",
        f"     = {fmt(h)} + ({fmt(prev.eye)} + {fmt(eh)} + {fmt(c)} - {fmt(h)}) * {fmt(row.distance)} / {fmt(prev.distance)}",
        f"     = {fmt(needed)} mm",
        f"Minimum riser: {fmt(needed)} - {fmt(prev.eye)} = {fmt(max(Fraction(0), needed - prev.eye))} mm"
        + (" (floored at 0)" if needed < prev.eye else ""),
    ]
    if row.fixed:
        out.append(f"Fixed riser in the room file: {fmt(row.riser)} mm (used instead of the minimum).")
    out.append(f"Riser used: {fmt(row.riser)} mm, eye {fmt(row.eye)} mm.")
    out.append(
        f"Clearance over row {n - 1}'s head: {fmt(row.clearance)} mm "
        f"(need {fmt(c)}) -> {'ok' if row.clearance_ok else 'FAIL'}."
    )
    out.append(f"Riser against max_riser {fmt(room.max_riser)} mm -> {'ok' if row.riser_ok else 'FAIL'}.")
    return out


def _status(row: SeatRow) -> str:
    bad = []
    if not row.clearance_ok:
        bad.append("clearance")
    if not row.riser_ok:
        bad.append("riser")
    return "ok" if not bad else "FAIL " + ", ".join(bad)


def render_table(room: Room, rows: list[SeatRow]) -> str:
    seats = f", {room.seats_per_row} seats per row" if room.seats_per_row else ""
    out = [
        f"{room.name}: {room.rows} rows{seats}, clearance {fmt(room.clearance)} mm, "
        f"max riser {fmt(room.max_riser)} mm",
        " row  distance   floor     eye  riser  minimum  clearance  status",
    ]
    for r in rows:
        clr = "-" if r.clearance is None else fmt(r.clearance)
        out.append(
            f"{r.number:>4} {fmt(r.distance):>9} {fmt(r.floor):>7} {fmt(r.eye):>7} "
            f"{fmt(r.riser):>6} {fmt(r.minimum_riser):>8} {clr:>10}  {_status(r)}"
        )
    s = summarise(room, rows)
    out.append(f"total rise {fmt(rows[-1].floor)} mm, back-row eye {fmt(rows[-1].eye)} mm"
               + (f", {s['seats']} seats" if s["seats"] else ""))
    return "\n".join(out)


def plan_json(room: Room, rows: list[SeatRow]) -> dict:
    return {
        "name": room.name,
        "summary": summarise(room, rows),
        "rows": [
            {
                "row": r.number,
                "distance_mm": _round(r.distance),
                "floor_mm": _round(r.floor),
                "eye_mm": _round(r.eye),
                "riser_mm": _round(r.riser),
                "minimum_riser_mm": _round(r.minimum_riser),
                "clearance_mm": None if r.clearance is None else _round(r.clearance),
                "fixed_riser": r.fixed,
                "riser_ok": r.riser_ok,
                "clearance_ok": r.clearance_ok,
            }
            for r in rows
        ],
    }


def check_report(room: Room, rows: list[SeatRow]) -> tuple[str, bool]:
    clr_fail = [r for r in rows if not r.clearance_ok]
    riser_fail = [r for r in rows if not r.riser_ok]
    out = [f"{room.name}: {room.rows} rows, clearance {fmt(room.clearance)} mm, "
           f"max riser {fmt(room.max_riser)} mm"]
    if clr_fail:
        out.append("clearance failures: " + ", ".join(f"row {r.number} ({fmt(r.clearance)} mm)" for r in clr_fail))
    else:
        out.append("clearance: every row clears the head in front")
    if riser_fail:
        out.append("riser failures: " + ", ".join(f"row {r.number} ({fmt(r.riser)} mm)" for r in riser_fail))
    else:
        out.append("risers: all within max_riser")
    passed = not clr_fail and not riser_fail
    out.append("PASS" if passed else "FAIL")
    return "\n".join(out), not passed


def profile(room: Room, rows: list[SeatRow], width: int = 40) -> str:
    top = max(r.eye for r in rows) + room.eye_to_head
    top = max(top, room.target_height)
    out = [f"{room.name}: eye levels, scale {fmt(top)} mm = {width} columns"]
    for r in rows:
        bar = max(1, round(float(r.eye / top) * width))
        out.append(f"row {r.number:>3} |{'#' * bar:<{width}}| eye {fmt(r.eye):>6} mm  {_status(r)}")
    target = round(float(room.target_height / top) * width)
    out.append(f"target|{'T' * target:<{width}}| {fmt(room.target_height)} mm")
    return "\n".join(out)


def svg_section(room: Room, rows: list[SeatRow], highlight: int) -> str:
    h = float(room.target_height)
    eh = float(room.eye_to_head)
    target = rows[highlight - 1]
    x_min = -300.0
    x_max = float(rows[-1].distance) + 600.0
    y_max = max([h] + [float(r.eye) + eh for r in rows]) + 300.0
    width, margin = 960, 40
    scale = (width - 2 * margin) / (x_max - x_min)
    height = y_max * scale + 2 * margin

    def px(x: float) -> float:
        return margin + (x - x_min) * scale

    def py(y: float) -> float:
        return height - margin - y * scale

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height:.0f}" '
        f'viewBox="0 0 {width} {height:.0f}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{margin}" y="{margin - 14}" font-family="sans-serif" font-size="16">'
        f'{html.escape(room.name)}: row {highlight} sightline</text>',
        f'<line x1="{px(x_min):.1f}" y1="{py(0):.1f}" x2="{px(x_max):.1f}" y2="{py(0):.1f}" stroke="#57606a"/>',
        f'<rect x="{px(-100):.1f}" y="{py(h):.1f}" width="{200 * scale:.1f}" height="{h * scale:.1f}" fill="#9aa5b1"/>',
    ]
    for r in rows:
        d = float(r.distance)
        top = float(r.eye) + eh
        floor = float(r.floor)
        colour = "#1f6feb" if r.number == highlight else "#c9d1d9"
        parts.append(
            f'<rect x="{px(d - 175):.1f}" y="{py(top):.1f}" width="{350 * scale:.1f}" '
            f'height="{(py(floor) - py(top)):.1f}" fill="{colour}"/>'
        )
        parts.append(
            f'<text x="{px(d):.1f}" y="{py(top) - 4:.1f}" text-anchor="middle" '
            f'font-family="sans-serif" font-size="11">{r.number}</text>'
        )

    d_t, e_t = float(target.distance), float(target.eye)
    slope_end = h + (e_t - h) * x_max / d_t
    parts.append(
        f'<line x1="{px(0):.1f}" y1="{py(h):.1f}" x2="{px(x_max):.1f}" y2="{py(slope_end):.1f}" '
        f'stroke="#d1242f" stroke-width="2"/>'
    )
    parts.append(f'<circle cx="{px(d_t):.1f}" cy="{py(e_t):.1f}" r="4" fill="#d1242f"/>')

    if highlight > 1:
        prev = rows[highlight - 2]
        d_prev = float(prev.distance)
        sight_prev = h + (e_t - h) * d_prev / d_t
        head_prev = float(prev.eye) + eh
        parts.append(
            f'<line x1="{px(d_prev):.1f}" y1="{py(head_prev):.1f}" x2="{px(d_prev):.1f}" '
            f'y2="{py(sight_prev):.1f}" stroke="#1a7f37" stroke-width="2" stroke-dasharray="4 3"/>'
        )
        if target.clearance is not None:
            parts.append(
                f'<text x="{px(d_prev) + 6:.1f}" y="{py((head_prev + sight_prev) / 2):.1f}" '
                f'font-family="sans-serif" font-size="12" fill="#1a7f37">'
                f'clearance {fmt(target.clearance)} mm</text>'
            )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def _with_cli_overrides(room: Room, args: argparse.Namespace) -> Room:
    if getattr(args, "clearance", None) is not None:
        value = _number(args.clearance)
        if value is None:
            raise ValueError(f"--clearance must be a non-negative number, got '{args.clearance}'")
        room = replace(room, clearance=value)
    if getattr(args, "max_riser", None) is not None:
        value = _number(args.max_riser)
        if value is None:
            raise ValueError(f"--max-riser must be a non-negative number, got '{args.max_riser}'")
        room = replace(room, max_riser=value)
    return room


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sightline",
        description="Plan auditorium riser steps and check every row's sightline clearance.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def room_options(p: argparse.ArgumentParser) -> None:
        p.add_argument("file", help="plain-text room file")
        p.add_argument("--clearance", metavar="MM", help="override the clearance in the file")
        p.add_argument("--max-riser", metavar="MM", help="override the max riser in the file")

    p = sub.add_parser("lint", help="parse the room file and print warnings")
    p.add_argument("file", help="plain-text room file")
    p.add_argument("--strict", action="store_true", help="exit 1 when there are warnings")

    p = sub.add_parser("plan", help="print the riser and sightline table")
    room_options(p)
    p.add_argument("--json", action="store_true", help="print JSON instead of a table")

    p = sub.add_parser("check", help="verify clearance and riser limits (CI gate)")
    room_options(p)

    p = sub.add_parser("explain", help="show the arithmetic for one row")
    room_options(p)
    p.add_argument("row", type=int, help="row number")

    p = sub.add_parser("profile", help="draw an ASCII side profile of eye levels")
    room_options(p)

    p = sub.add_parser("svg", help="write a side-section SVG with one row's sightline")
    room_options(p)
    p.add_argument("--row", type=int, help="row whose sightline to draw (default: back row)")
    p.add_argument("-o", "--output", metavar="FILE", help="write SVG here instead of stdout")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        room = load_room(Path(args.file))
    except RoomFileError as err:
        for problem in err.problems:
            print(f"{args.file}: {problem}", file=sys.stderr)
        return 2
    except OSError as err:
        print(f"sightline: cannot read {args.file}: {err.strerror}", file=sys.stderr)
        return 2

    if args.command == "lint":
        warnings = lint_room(room)
        for w in warnings:
            print(f"{args.file}: {w}")
        print(f"{args.file}: ok, {room.rows} rows, {len(warnings)} warning(s)")
        return 1 if args.strict and warnings else 0

    try:
        room = _with_cli_overrides(room, args)
        rows = plan(room)
        if args.command == "explain":
            print("\n".join(explain(room, args.row)))
        elif args.command == "svg":
            highlight = args.row if args.row is not None else room.rows
            if not 1 <= highlight <= room.rows:
                raise ValueError(f"--row must be from 1 to {room.rows}, got {highlight}")
            svg = svg_section(room, rows, highlight)
            if args.output:
                Path(args.output).write_text(svg, encoding="utf-8")
                print(f"wrote {args.output}")
            else:
                print(svg, end="")
    except ValueError as err:
        print(f"sightline: {err}", file=sys.stderr)
        return 2

    if args.command == "plan":
        if args.json:
            print(json.dumps(plan_json(room, rows), indent=2))
        else:
            print(render_table(room, rows))
        return 0
    if args.command == "check":
        report, failed = check_report(room, rows)
        print(report)
        return 1 if failed else 0
    if args.command == "profile":
        print(profile(room, rows))
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
