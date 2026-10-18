# Sightline

**Plan auditorium riser steps so every seat keeps a clear view, offline, from one plain-text room file.**

Sightline solves the minimum riser for each row so the eye line of every seat clears the head in front by a set clearance, checks any hand-built steps you specify, and gates CI on the result. It is a single Python file using only the standard library. No servers, no accounts, no network.

## Features

- **Plain-text room files** with `key = value` settings, `#` comments, and an optional `row N riser = value` override per row.
- **Exact minimum risers** solved with `Fraction` arithmetic, so the clearance comes out exactly equal to the target rather than approximately.
- **Clearance check for every row**: the gap between the sight line to the target and the top of the head in the row in front.
- **Hand-built steps**: fix any row's riser and Sightline checks whether the result still clears the head in front.
- **Riser limit check** against `max_riser`, with a CLI override via `--max-riser`.
- **Line-numbered errors** for every bad setting, duplicate, unknown key, out-of-range row or missing requirement. All problems are reported in one pass.
- **Lint warnings** for unusual values, such as a row pitch under 700 mm, a riser steeper than 900 mm, or an unusually large eye-to-head allowance.
- **Row-by-row table** with distance, floor, eye height, riser, minimum riser, clearance and status for each row.
- **Explain mode** that prints the arithmetic for one row, from the formula to the riser used.
- **ASCII side profile** of eye levels against the target height.
- **SVG section drawing** of the room, with the chosen row's sight line and its clearance marked. Writes to a file or stdout.
- **JSON output** of the full plan and summary for scripting.
- **Seat count and total rise** for the room, when `seats_per_row` is given.
- **CI exit codes**: `check` returns 0 on pass, 1 on any clearance or riser failure, and 2 on file errors. `lint --strict` returns 1 on warnings.

## Quick start

```console
$ python3 sightline.py explain examples/hall-a.txt 2
Row 2: 6900 mm from the target; row 1 is 6000 mm away.
Head top in front: eye 1100 + eye_to_head 120 = 1220 mm.
Lowest eye that clears it by the clearance:
  E >= H + (E_prev + eye_to_head + clearance - H) * D / D_prev
     = 1500 + (1100 + 120 + 120 - 1500) * 6900 / 6000
     = 1316 mm
Minimum riser: 1316 - 1100 = 216 mm
Riser used: 216 mm, eye 1316 mm.
Clearance over row 1's head: 120 mm (need 120) -> ok.
Riser against max_riser 600 mm -> ok.
```

The example file `examples/hall-a.txt` describes a 10-row hall with a 1.5 m stage target, 6 m from the front row, 900 mm row pitch, and 24 seats per row:

```text
name = Lecture Hall A
rows = 10
seats_per_row = 24
target_height = 1500
target_distance = 6000
row_pitch = 900
eye_height = 1100
eye_to_head = 120
clearance = 120
max_riser = 600
```

Then run the gate:

```console
$ python3 sightline.py check examples/hall-a.txt
$ echo $?
```

## Room file reference

| Setting           | Meaning                                                         | Default       |
|-------------------|-----------------------------------------------------------------|---------------|
| `name`            | Label used in reports                                           | Untitled room |
| `rows`            | Number of rows, 1 to 200                                        | required      |
| `seats_per_row`   | Seats in each row, used for the seat count                      | none          |
| `target_height`   | Height of the target above the front row's floor (mm)           | required      |
| `target_distance` | Horizontal distance from the target to row 1's eyes (mm)        | required      |
| `row_pitch`       | Horizontal distance between rows (mm)                           | required      |
| `eye_height`      | Seated eye height above a row's own floor (mm)                  | required      |
| `eye_to_head`     | Eye to top of the head (mm)                                     | 120           |
| `clearance`       | Required gap between the sight line and the head in front (mm)  | 120           |
| `max_riser`       | Largest allowed step between rows (mm)                          | 600           |
| `row N riser`     | Fixed step for row N (mm), replacing the solved minimum         | solved        |

Heights are measured above the front row's floor. Row 1 has no row in front, so it cannot take a riser. Set `eye_height` for it instead.

## How the rule works

For row *n*, let the target be at height *H* and *D* away, the row in front at *D_prev* with eye height *E_prev*. The sight line from the target through row *n*'s eyes must pass the head in front by `clearance`:

```
E_n >= H + (E_prev + eye_to_head + clearance - H) * D_n / D_prev
riser_n = max(0, E_n - E_prev)
```

Solved risers meet the clearance exactly. A fixed riser that is smaller than the minimum fails the check, and a riser bigger than `max_riser` fails the riser check.

## Commands

| Command                              | What it does                                                   | Exit codes                |
|--------------------------------------|----------------------------------------------------------------|---------------------------|
| `plan FILE [--json]`                 | Print the row table, or the full plan as JSON                  | 0 ok, 2 file error        |
| `check FILE`                         | Verify clearance and riser limits                              | 0 pass, 1 fail, 2 error   |
| `explain FILE ROW`                   | Show the arithmetic for one row                                | 0 ok, 2 error             |
| `profile FILE`                       | ASCII side profile of eye levels                               | 0 ok, 2 error             |
| `svg FILE [--row N] [-o OUT.svg]`    | Write a section drawing with one row's sight line              | 0 ok, 2 error             |
| `lint FILE [--strict]`               | Parse, print warnings; `--strict` fails on any warning         | 0 ok, 1 warnings, 2 error |

`plan`, `check`, `explain`, `profile` and `svg` also accept `--clearance MM` and `--max-riser MM` to override the file for one run.

## How to run

Needs Python 3.9 or newer. There is nothing to install for the program itself.

```console
$ python3 sightline.py plan examples/hall-a.txt
$ python3 sightline.py svg examples/hall-a.txt --row 10 -o hall-a.svg
$ python3 sightline.py lint examples/bad-hall.txt
```

`examples/bad-hall.txt` is deliberately broken, to show the line-numbered errors.

## How to test

```console
$ pip install pytest
$ python3 -m pytest -q
```

The tests live in `tests/` and cover parsing and errors with line numbers, the exact minimum riser, the clearance guarantee for solved rows, clamping at zero, fixed risers that fail, CLI exit codes, lint warnings, the SVG, profile and JSON output, and the example files.

## Project layout

```text
sightline.py            the whole program (argparse CLI and pure functions)
examples/hall-a.txt     a passing 10-row hall
examples/hall-b-fixed.txt  a hall with one hand-built 400 mm step
examples/bad-hall.txt   a broken file showing error reporting
tests/                  pytest suite
```

## Limits

Sightline checks one centre-line of sight from a single target point. It does not model seat width, lateral offsets, or obstructions such as balconies and lighting. Treat its output as a planning aid, and confirm final tiers against the local code and an on-site sight-line survey.
