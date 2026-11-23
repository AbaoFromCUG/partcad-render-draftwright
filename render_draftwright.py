# PartCAD render implementation backed by draftwright.
# Licensed under Apache License, Version 2.0.
"""Render a PartCAD BREP with Draftwright 0.6's Rust-backed Python API.

PartCAD supplies ``path`` and a request containing the OCCT shape in
``wrapped``. Draftwright's public Part/ Drawing API transfers the geometry
to its native engine, writes the requested formats and exposes findings in
``drawing.problems``. No Draftwright internals or PartCAD imports are needed.

The native exporters are deterministic, including when the caller does not
require reproducibility. PDF no longer goes through reportlab or svglib.
"""

import os
import traceback

import build123d as b3d
from draftwright import Part

FORMATS = ("pdf", "svg", "dxf")
TITLE_FIELDS = ("drawn_by", "date", "revision", "company", "approved_by", "document_type", "sheet")


def _shape(wrapped):
    """Wrap OCCT geometry without the abstract ``Shape.cast`` downcast."""
    shape = b3d.Solid.make_box(1, 1, 1)
    shape.wrapped = wrapped
    return shape


def _format_of(path):
    extension = os.path.splitext(path)[1].lstrip(".").lower()
    if extension not in FORMATS:
        raise ValueError("draftwright writes %s, not '%s'" % (", ".join(FORMATS).upper(), extension))
    return extension


def _boolean(value):
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes", "on", "1"):
            return True
        if lowered in ("false", "no", "off", "0"):
            return False
        raise ValueError("Expected a boolean, got %r" % value)
    return bool(value)


def _drawing(request):
    """Map the existing package options to the public Part and Drawing API."""
    if request.get("detail_view") is not None and not _boolean(request["detail_view"]):
        raise ValueError("detail_view: false is not supported by Draftwright 0.6")
    if request.get("assembly") is not None and _boolean(request["assembly"]):
        raise ValueError("assembly: true requires Draftwright 0.6's separate Pack API")

    name = request.get("shape_name") or "part"
    part = Part(_shape(request["wrapped"]), name, title=request.get("title") or name, number=request.get("number"))
    if request.get("material") is not None:
        part.material(request["material"])
    if request.get("tolerance") is not None:
        part.general_tolerance(request["tolerance"])

    page = request.get("page")
    if isinstance(page, str):
        page = page.strip().upper()
    drawing = part.drawing(sheet=page, scale=request.get("scale"))
    title = {key: request[key] for key in TITLE_FIELDS if request.get(key) is not None}
    if title:
        drawing.title(**title)

    spec = {"sheet": {}}
    if request.get("projection") is not None:
        spec["sheet"]["projection"] = request["projection"]
    if request.get("zones") is not None:
        spec["sheet"]["zones"] = _boolean(request["zones"])
    if request.get("auto_dims") is not None and not _boolean(request["auto_dims"]):
        spec["dimension_source"] = "authored"
        spec["sheet"].update(centre_lines=False, centre_marks=False)
    drawing.update(spec)
    return drawing


def _critique(drawing):
    """Keep the existing failure policy: engine errors fail the render."""
    errors, advisories = [], []
    for issue in drawing.problems:
        severity = issue.get("severity", "warning")
        line = "[%s] %s: %s" % (severity, issue.get("code", "drawing"), issue.get("message", ""))
        if issue.get("suggestion"):
            line += " -- %s" % issue["suggestion"]
        (errors if severity == "error" else advisories).append(line)
    return errors, advisories


def process(path, request):
    try:
        file_format = _format_of(path)
        if request.get("shape_kind") == "sketch":
            raise ValueError("draftwright draws solids; '%s' is a sketch" % request.get("shape_name"))
        pmi = request.get("pmi")
        if pmi not in (None, False, "off", "report", "annotate"):
            raise ValueError("pmi must be off, report or annotate")

        drawing = _drawing(request)
        stem = os.path.splitext(path)[0]
        produced = drawing.render(stem, formats=(file_format,))
        if not produced:
            raise RuntimeError("draftwright returned no output files")
        written = os.path.abspath(produced[0])
        if written != os.path.abspath(path):
            os.replace(written, path)
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            raise RuntimeError("draftwright produced no %s file: %s" % (file_format.upper(), path))

        errors, advisories = _critique(drawing)
        if pmi in ("report", "annotate"):
            advisories.append("The PartCAD BREP input carries no AP242 PMI; this drawing uses geometry only.")
        if request.get("frame") is not None and not _boolean(request["frame"]):
            advisories.append("Draftwright 0.6 always draws the sheet frame; frame: false cannot hide it.")
        result = {
            "success": not errors,
            "exception": None,
            "warnings": advisories,
            # Additional SVG/DXF sheets must travel with a cached artifact.
            "files": [os.path.abspath(item) for item in produced[1:]],
        }
        if errors:
            result["exception"] = (
                "draftwright reports %d error(s) in the drawing (written to %s so it can be inspected):\n  %s"
                % (len(errors), os.path.basename(path), "\n  ".join(errors))
            )
        return result
    except Exception as exc:
        return {"success": False, "exception": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))}
