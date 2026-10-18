"""Tests for sightline. Expected numbers were worked out by hand in the comments."""

from fractions import Fraction
from pathlib import Path
import json
import xml.etree.ElementTree as ET

import pytest

import sightline as sl

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"

BASE = """\
rows = 3
target_height = 1500
target_distance = 6000
row_pitch = 900
eye_height = 1100
"""


def load(name):
    return sl.load_room(EXAMPLES / name)


def write(tmp_path, text, name="room.txt"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_parse_example_reads_every_setting():
    room = load("hall-a.txt")
    assert room.name == "Lecture Hall A"
    assert room.rows == 10
    assert room.seats_per_row == 24
    assert room.target_height == 1500
    assert room.eye_to_head == 120
    assert room.clearance == 120
    assert room.max_riser == 600


def test_comments_blank_lines_and_defaults_are_handled():
    room = sl.parse_room("# note\n\n" + BASE + "clearance = 150 # inline note\n")
    assert room.clearance == 150
    # defaults: eye_to_head 120, max_riser 600, name "Untitled room"
    assert room.eye_to_head == 120
    assert room.max_riser == 600
    assert room.name == "Untitled room"


def test_parse_reports_every_error_with_line_numbers():
    text = (
        "rows = ten\n"            # line 1: not a whole number
        "clearnace = 120\n"       # line 2: unknown setting
        "row 1 riser = 200\n"     # line 3: row 1 cannot take a riser
        "row 4 riser = fast\n"    # line 4: riser not a number
        "target_height = 1500\n"
        "target_distance = 6000\n"
        "row_pitch = 900\n"
        "eye_height = 1100\n"
    )
    with pytest.raises(sl.RoomFileError) as info:
        sl.parse_room(text)
    lines = [(p.line, p.message) for p in info.value.problems]
    assert (1, "'rows' must be a whole number, got 'ten'") in lines
    assert (2, "unknown setting 'clearnace'") in lines
    assert (3, "row 1 cannot take a riser; set eye_height instead") in lines
    assert any(line == 4 and "row 4" in msg for line, msg in lines)


def test_missing_required_setting_is_reported_without_a_line():
    with pytest.raises(sl.RoomFileError) as info:
        sl.parse_room("rows = 3\n")
    messages = [str(p) for p in info.value.problems]
    assert "missing required setting 'target_height'" in messages


def test_duplicate_setting_points_back_to_first_line():
    with pytest.raises(sl.RoomFileError) as info:
        sl.parse_room(BASE + "rows = 4\n")
    assert info.value.problems[0].line == 6
    assert "first set on line 1" in info.value.problems[0].message


def test_minimum_riser_for_row_two_is_exact():
    # Row 1 eye 1100, row 2 is 6900 mm from the target, row 1 is 6000 mm away.
    # E2 >= 1500 + (1100 + 120 + 120 - 1500) * 6900 / 6000
    #    = 1500 + (-160) * 1.15 = 1316, so riser = 1316 - 1100 = 216.
    rows = sl.plan(load("hall-a.txt"))
    assert rows[1].minimum_riser == 216
    assert rows[1].eye == 1316
    assert rows[1].clearance == 120


def test_solved_rows_clear_the_head_in_front_by_exactly_the_clearance():
    room = load("hall-a.txt")
    for row in sl.plan(room)[1:]:
        assert row.clearance == room.clearance, row


def test_riser_never_goes_below_zero_when_target_is_high():
    # Target at 10 m height: row 2 needs E2 >= 10000 + (1340 - 10000) * 1.15 = 41,
    # which is below the row-1 eye of 1100, so the riser clamps to 0.
    room = sl.parse_room(BASE.replace("target_height = 1500", "target_height = 10000"))
    row2 = sl.plan(room)[1]
    assert row2.minimum_riser == 0
    assert row2.riser == 0
    assert row2.eye == 1100
    assert row2.clearance >= 120


def test_fixed_riser_below_minimum_fails_clearance():
    # Riser 100 gives eye 1200. Sight line over row 1's head is
    # 1500 + (1200 - 1500) * 6000 / 6900 = 1239.13, head top 1220, gap 19.13 < 120.
    room = sl.parse_room(BASE + "row 2 riser = 100\n")
    row2 = sl.plan(room)[1]
    assert row2.fixed
    assert not row2.clearance_ok
    assert float(row2.clearance) == pytest.approx(19.1304, abs=1e-3)


def test_fixed_riser_above_max_riser_fails_riser_check():
    room = sl.parse_room(BASE + "row 2 riser = 700\n")
    assert not sl.plan(room)[1].riser_ok


def test_summary_counts_seats_and_checks_the_rise():
    room = load("hall-a.txt")
    rows = sl.plan(room)
    summary = sl.summarise(room, rows)
    assert summary["seats"] == 240
    assert summary["passed"] is True
    assert summary["back_eye_mm"] - summary["total_rise_mm"] == pytest.approx(1100)


def test_explain_shows_the_row_two_arithmetic():
    lines = sl.explain(load("hall-a.txt"), 2)
    assert any("Minimum riser: 1316 - 1100 = 216 mm" in line for line in lines)
    with pytest.raises(ValueError):
        sl.explain(load("hall-a.txt"), 11)


def test_lint_flags_a_tight_pitch_with_its_line_number():
    room = sl.parse_room(BASE.replace("row_pitch = 900", "row_pitch = 650"))
    warnings = sl.lint_room(room)
    assert len(warnings) == 1
    assert warnings[0].line == 4
    assert "tight" in warnings[0].message


def test_svg_section_is_well_formed_and_draws_every_row():
    room = load("hall-a.txt")
    svg = sl.svg_section(room, sl.plan(room), highlight=10)
    root = ET.fromstring(svg)
    rects = root.findall("{http://www.w3.org/2000/svg}rect")
    # background, target block, and one body per row
    assert len(rects) == 2 + room.rows


def test_profile_has_a_line_per_row_and_one_for_the_target():
    room = load("hall-a.txt")
    lines = sl.profile(room, sl.plan(room)).splitlines()
    assert len(lines) == 1 + room.rows + 1


def test_json_plan_round_trips_and_row_one_has_no_clearance():
    room = load("hall-a.txt")
    data = json.loads(json.dumps(sl.plan_json(room, sl.plan(room))))
    assert len(data["rows"]) == 10
    assert data["rows"][0]["clearance_mm"] is None
    assert data["rows"][1]["riser_mm"] == 216


def test_check_passes_for_the_solved_example(capsys):
    assert sl.main(["check", str(EXAMPLES / "hall-a.txt")]) == 0
    assert "PASS" in capsys.readouterr().out


def test_check_fails_with_exit_1_for_a_too_small_fixed_riser(tmp_path, capsys):
    path = write(tmp_path, BASE + "row 2 riser = 100\n")
    assert sl.main(["check", str(path)]) == 1
    out = capsys.readouterr().out
    assert "clearance failures: row 2" in out
    assert out.strip().endswith("FAIL")


def test_cli_max_riser_override_can_fail_a_solved_room(capsys):
    # Row 2 needs a 216 mm riser, so a 200 mm limit must fail.
    assert sl.main(["check", str(EXAMPLES / "hall-a.txt"), "--max-riser", "200"]) == 1
    assert "riser failures" in capsys.readouterr().out


def test_bad_file_returns_exit_2_with_line_numbers(capsys):
    assert sl.main(["plan", str(EXAMPLES / "bad-hall.txt")]) == 2
    err = capsys.readouterr().err
    assert "line 3" in err
    assert "unknown setting 'clearnace'" in err


def test_fixed_hall_example_passes_check():
    assert sl.main(["check", str(EXAMPLES / "hall-b-fixed.txt")]) == 0
