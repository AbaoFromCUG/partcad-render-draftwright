"""Exercise the real Rust engine across the sandbox adapter boundary."""

import importlib.util
from pathlib import Path
from xml.etree import ElementTree

import build123d as b3d
import pytest

_spec = importlib.util.spec_from_file_location(
    "render_draftwright", Path(__file__).parents[1] / "render_draftwright.py"
)
renderer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(renderer)


@pytest.fixture(scope="module")
def board():
    # Geometry of //pcb:banana-power-board: two inclined contact bores and
    # two vertical holes on a 3.81 mm pitch, rather than a box-only smoke test.
    solid = b3d.Box(46, 7.6, 1.6, align=(b3d.Align.CENTER, b3d.Align.CENTER, b3d.Align.MIN))
    for x in (-16.9, 16.9):
        tool = (
            b3d.Pos(x, 0.429538594, 0)
            * b3d.Rot(0.9999994040204676, 0, 0)
            * b3d.Pos(0, 0, -1)
            * b3d.Cylinder(1.1, 3.6, align=(b3d.Align.CENTER, b3d.Align.CENTER, b3d.Align.MIN))
        )
        solid -= tool
    for x in (-5.905, -2.095):
        solid -= b3d.Pos(x, 0, -1) * b3d.Cylinder(0.55, 3.6, align=(b3d.Align.CENTER, b3d.Align.CENTER, b3d.Align.MIN))
    return solid


@pytest.mark.parametrize("extension", renderer.FORMATS)
def test_board_formats_are_valid_and_reproducible(tmp_path, board, extension):
    path = tmp_path / ("board." + extension)
    request = {"wrapped": board.wrapped, "shape_kind": "part", "shape_name": "banana-power-board"}
    result = renderer.process(str(path), request)
    assert result["success"], result
    original = path.read_bytes()
    assert len(original) > 1000
    if extension == "svg":
        root = ElementTree.fromstring(original)
        assert root.tag.endswith("svg")
        assert "banana-power-board" in " ".join(root.itertext())
    elif extension == "pdf":
        assert original.startswith(b"%PDF-")
        assert original.rstrip().endswith(b"%%EOF")
    else:
        import ezdxf

        document = ezdxf.readfile(path)
        assert len(document.modelspace()) > 0
        assert not document.audit().has_errors
    result = renderer.process(str(path), {**request, "reproducible": True})
    assert result["success"], result
    assert path.read_bytes() == original


def test_explicit_drawing_options(tmp_path, board):
    path = tmp_path / "configured.svg"
    result = renderer.process(
        str(path),
        {
            "wrapped": board.wrapped,
            "shape_name": "board",
            "title": "Configured board",
            "number": "TEST-061",
            "material": "FR4",
            "revision": "C",
            "drawn_by": "Adapter test",
            "date": "2026-11-23",
            "page": " a3 ",
            "scale": 2,
            "projection": "first",
            "zones": "false",
            "auto_dims": "false",
            "frame": False,
        },
    )
    assert result["success"], result
    root = ElementTree.parse(path).getroot()
    text = " ".join(root.itertext())
    for value in ("Configured board", "TEST-061", "FR4", "Adapter test", "2026-11-23"):
        assert value in text
    assert any("always draws the sheet frame" in item for item in result["warnings"])


@pytest.mark.parametrize(
    "options, message",
    [
        ({"shape_kind": "sketch"}, "draws solids"),
        ({"detail_view": False}, "detail_view: false"),
        ({"assembly": True}, "separate Pack API"),
        ({"scale": -1}, "scale"),
    ],
)
def test_unsupported_requests_fail_clearly(tmp_path, board, options, message):
    path = tmp_path / "invalid.svg"
    result = renderer.process(str(path), {"wrapped": board.wrapped, "shape_name": "board", **options})
    assert not result["success"]
    assert message in result["exception"]
    assert not path.exists()
