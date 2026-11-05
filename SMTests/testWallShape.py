# -*- coding: utf-8 -*-
# #######################################################################
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 2 of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU Lesser General Public
#  License along with this program; if not, write to the Free Software
#  Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
#  MA 02110-1301, USA.
#
# #######################################################################

"""Tests for wall shapes (Edit Shape): a Make Wall wall with a custom outline.

A shape sketch is an open chain drawn in its own 2D frame: x along the
attachment edge from its start, y up the wall from the reference line. Closing
lines join its loose ends to the bend ends, straight in the flat pattern, and
the bend follows the outline.

Expected values come from formulas: with K-factor 0.5 a sheet keeps its volume
when folded, so a wall's volume is thickness x (plate area + outline area in
the flat pattern) minus the reliefs.

Run with:  FreeCADCmd -t TestSheetMetal
"""

import math
import unittest

import FreeCAD
import Part
from FreeCAD import Vector

import SheetMetalCmd
import SheetMetalTools
import SheetMetalUnfoldCmd
from SMTests.testFeatures import (
    PLATE_L,
    PLATE_VOLUME,
    PLATE_W,
    THK,
    FeatureTestCase,
    add_wall,
    face_name,
    is_flat_face,
    make_plate,
)

RADIUS, K = 1.0, 0.5
RELIEF_VOLUME = 0.8 * 1.0 * THK  # default relief width x depth x thickness


def bend_allowance(angle_deg=90.0, radius=RADIUS, k=K):
    return math.radians(angle_deg) * (radius + k * THK)


def shoelace(points):
    return abs(sum(x1 * y2 - x2 * y1
                   for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]))) / 2


def outline_area(chain, bend_start, bend_end, rise):
    """Area of the outline in the flat pattern, for a chain drawn left to right."""
    lifted = [(x, y + rise) for x, y in chain]
    return shoelace([(bend_start, 0.0)] + lifted + [(bend_end, 0.0)])


def shape_sketch(doc, points):
    """An open polyline in the shape sketch's own 2D frame."""
    sketch = doc.addObject("Sketcher::SketchObject", "Shape")
    for start, end in zip(points, points[1:]):
        sketch.addGeometry(Part.LineSegment(Vector(*start, 0), Vector(*end, 0)))
    return sketch


def unfold(doc, wall, old_unfolder):
    """Unfold with the old or the new unfolder, from the plate's top face."""
    params = SheetMetalTools.params
    previous = params.GetBool("UseOldUnfolder", False)
    params.SetBool("UseOldUnfolder", old_unfolder)
    try:
        flat = doc.addObject("Part::FeaturePython", "Unfold")
        SheetMetalUnfoldCmd.SMUnfold(flat, wall, [face_name(wall.Shape, is_flat_face(1))])
        flat.MaterialSheet = "_manual"
        flat.KFactor = K
        doc.recompute()
        return flat
    finally:
        params.SetBool("UseOldUnfolder", previous)


def upper_part(wall, above=10.0):
    """The part of the wall well above the plate."""
    return wall.Shape.common(Part.makeBox(200, 400, 200, Vector(-50, -150, above)))


class TestWallShape(FeatureTestCase):

    def setUp(self):
        super().setUp()
        self.plate = make_plate(self.doc)

    def shaped_wall(self, chain, **props):
        wall = add_wall(self.doc, self.plate, **props)
        wall.ShapeSketch = shape_sketch(self.doc, chain)
        self.doc.recompute()
        return wall

    def test_starting_sketch_keeps_the_wall(self):
        """An unchanged starting sketch gives exactly the plain wall back."""
        cases = [
            ("Leg", {}),
            ("Outer Sharp", {"LengthSpec": "Outer Sharp"}),
            ("Inner Sharp", {"LengthSpec": "Inner Sharp"}),
            ("Tangential", {"LengthSpec": "Tangential"}),
            ("Material Inside", {"BendType": "Material Inside"}),
            ("Thickness Outside", {"BendType": "Thickness Outside"}),
            ("Offset", {"BendType": "Offset", "offset": 2.0}),
            ("Invert", {"invert": True}),
            ("negative angle", {"angle": -90.0}),
            ("45 degrees", {"angle": 45.0}),
            ("135 degrees, Outer Sharp", {"angle": 135.0, "LengthSpec": "Outer Sharp"}),
            ("gaps", {"gap1": 5.0, "gap2": 10.0}),
            ("extends", {"gap1": 8.0, "gap2": 8.0, "extend1": 3.0, "extend2": -2.0}),
            ("manual miter", {"AutoMiter": False, "miterangle1": 20.0}),
            ("miter and negative Extend", {"AutoMiter": False, "miterangle2": 15.0,
                                           "gap2": 6.0, "extend2": -3.0}),
            ("negative (widening) miter", {"AutoMiter": False, "miterangle1": -15.0}),
            ("negative miter and negative Extend", {"AutoMiter": False, "miterangle1": -15.0,
                                                    "gap1": 5.0, "extend1": -2.0}),
        ]
        for label, props in cases:
            with self.subTest(label):
                wall = add_wall(self.doc, self.plate, **props)
                plain = wall.Shape.copy()
                sketch = SheetMetalCmd.smCreateWallShapeSketch(wall)
                self.doc.recompute()
                self.assertValidSheet(wall)
                self.assertEqual(sketch.solve(), 0, "starting sketch doesn't solve")
                self.assertLess(plain.cut(wall.Shape).Volume, 0.01)
                self.assertLess(wall.Shape.cut(plain).Volume, 0.01)
                # Same faces too: no extra seams or slivers on the bend.
                self.assertEqual(len(wall.Shape.Faces), len(plain.Faces), "face count")
                self.assertEqual(len(wall.Shape.Edges), len(plain.Edges), "edge count")
                # The bend is wrapped from B-splines, so allow a tiny volume difference.
                self.assertAlmostEqual(wall.Shape.Volume, plain.Volume, delta=plain.Volume * 1e-4)

    def test_making_the_starting_sketch_reports_no_errors(self):
        """Edit Shape never builds the wall from a half-made sketch."""
        errors = []
        original = SheetMetalCmd.SMWallShape.__init__

        def recording(shape, sketch):
            try:
                original(shape, sketch)
            except Exception as error:
                errors.append(str(error))
                raise

        SheetMetalCmd.SMWallShape.__init__ = recording
        try:
            wall = add_wall(self.doc, self.plate, gap1=5.0, extend1=2.0)
            SheetMetalCmd.smCreateWallShapeSketch(wall)
            SheetMetalCmd.smRefreshWallShapeTilt(wall)
            self.doc.recompute()
        finally:
            SheetMetalCmd.SMWallShape.__init__ = original
        self.assertEqual(errors, [])
        self.assertValidSheet(wall)

    def test_panel_values_give_the_plain_wall(self):
        """Setting Gap A/B and Extend A/B on a shaped wall, as the task panel
        does, gives exactly the wall Make Wall makes with the same values.

        Miters and negative Extends are set on the wall only; the wall applies
        them where the flat part starts.
        """
        fields = {"gap1": "GapA", "gap2": "GapB", "extend1": "ExtendA", "extend2": "ExtendB"}
        # The panel's miter and negative Extend values go to the wall only.
        cases = [
            ({"gap1": 5.0, "gap2": 10.0}, "Leg"),
            ({"extend1": 3.0, "extend2": 2.0}, "Leg"),
            ({"gap1": 5.0, "extend1": 2.0}, "Leg"),
            ({"gap1": 5.0, "gap2": 10.0}, "Outer Sharp"),
            ({"extend1": 3.0, "extend2": 2.0}, "Outer Sharp"),
            ({"gap1": 5.0, "extend1": 2.0}, "Outer Sharp"),
            ({"AutoMiter": False, "miterangle1": 20.0, "miterangle2": 10.0}, "Outer Sharp"),
            ({"gap1": 5.0, "extend1": -2.0}, "Outer Sharp"),
        ]
        for values, spec in cases:
            with self.subTest(values=values, spec=spec):
                plain = add_wall(self.doc, self.plate, LengthSpec=spec, **values).Shape
                wall = add_wall(self.doc, self.plate, LengthSpec=spec)
                sketch = SheetMetalCmd.smCreateWallShapeSketch(wall)
                for prop, value in values.items():
                    setattr(wall, prop, value)
                    if prop in fields:
                        if prop.startswith("extend"):
                            value = max(value, 0.0)
                        sketch.setDatum(SheetMetalCmd.smFindNamedConstraint(sketch, fields[prop]),
                                        FreeCAD.Units.Quantity(value, FreeCAD.Units.Length))
                self.doc.recompute()
                self.assertValidSheet(wall)
                self.assertLess(plain.cut(wall.Shape).Volume + wall.Shape.cut(plain).Volume, 0.01)
                self.assertEqual(len(wall.Shape.Faces), len(plain.Faces), "face count")

    def test_miters_and_negative_extend_follow_the_wall(self):
        """Miters and negative Extends stay on the wall and start where the flat
        part starts, so they stay exact when that line moves (radius, angle,
        Length Spec) or Length changes.
        """
        starts = [
            ("miter A 20", {"AutoMiter": False, "miterangle1": 20.0}),
            ("miters A 20 / B 10", {"AutoMiter": False, "miterangle1": 20.0, "miterangle2": 10.0}),
            ("negative Extend A", {"gap1": 5.0, "extend1": -2.0}),
            ("miter and negative Extend", {"AutoMiter": False, "miterangle1": 20.0,
                                           "gap1": 5.0, "extend1": -2.0}),
            ("miter and positive Extend", {"AutoMiter": False, "miterangle2": 20.0, "extend2": 3.0}),
        ]
        changes = [
            ("Length 20->30", {"length": 30.0}),
            ("radius 1->3", {"radius": 3.0}),
            ("angle 90->60", {"angle": 60.0}),
            ("Length Spec Leg", {"LengthSpec": "Leg"}),
        ]
        for slabel, start in starts:
            for clabel, change in changes:
                with self.subTest(start=slabel, change=clabel):
                    base = dict(start, LengthSpec="Outer Sharp")
                    plain = add_wall(self.doc, self.plate, **dict(base, **change)).Shape
                    wall = add_wall(self.doc, self.plate, **base)
                    sketch = SheetMetalCmd.smCreateWallShapeSketch(wall)
                    for prop, value in change.items():
                        setattr(wall, prop, value)
                        if prop == "length":  # the task panel also edits the sketch's Length
                            sketch.setDatum(SheetMetalCmd.smFindNamedConstraint(sketch, "Length"),
                                            FreeCAD.Units.Quantity(value, FreeCAD.Units.Length))
                    self.doc.recompute()
                    self.assertValidSheet(wall)
                    self.assertLess(plain.cut(wall.Shape).Volume + wall.Shape.cut(plain).Volume, 0.01)
                    self.assertEqual(len(wall.Shape.Faces), len(plain.Faces), "face count")

    def test_miter_rules_on_a_custom_outline(self):
        """A miter keeps nothing of the flat part outside its line."""
        steep = [(0, 0), (12, 20), (88, 20), (100, 0)]  # sides already slanting in by 31 degrees
        tee = [(0, 0), (0, 10), (-10, 10), (-10, 20), (110, 20), (110, 10), (100, 10), (100, 0)]
        with self.subTest("a steeper slant in the sketch is left alone"):
            unmitered = self.shaped_wall(steep).Shape.Volume
            wall = self.shaped_wall(steep, AutoMiter=False, miterangle1=10.0, miterangle2=10.0)
            self.assertValidSheet(wall)
            self.assertAlmostEqual(wall.Shape.Volume, unmitered, delta=0.01)
        with self.subTest("an overhang outside the miter line is cut off"):
            wall = self.shaped_wall(tee, AutoMiter=False, miterangle1=10.0, miterangle2=10.0)
            self.assertValidSheet(wall)
            upper = upper_part(wall).BoundBox
            self.assertGreater(upper.YMin, 0.0)
            self.assertLess(upper.YMax, PLATE_L)
        with self.subTest("a negative miter is refused"):
            wall = self.shaped_wall(tee, AutoMiter=False, miterangle1=-10.0)
            self.assertIn("negative miter", wall.getStatusString())
        with self.subTest("trimming away the whole flat part is refused"):
            wall = self.shaped_wall([(0, 0), (0, 20), (100, 20), (100, 0)], extend1=-150.0)
            self.assertIn("whole flat part", wall.getStatusString())

    def test_negative_miter_is_drawn_into_the_sketch(self):
        """A shaped wall can't widen with a miter, so a negative miter goes into
        the starting sketch and the wall's own value is reset."""
        wall = add_wall(self.doc, self.plate, AutoMiter=False, miterangle1=-15.0)
        plain = wall.Shape.copy()
        SheetMetalCmd.smCreateWallShapeSketch(wall)
        self.doc.recompute()
        self.assertValidSheet(wall)
        self.assertEqual(wall.miterangle1.Value, 0.0)
        self.assertLess(plain.cut(wall.Shape).Volume + wall.Shape.cut(plain).Volume, 0.01)

    def test_starting_sketch_stands_on_the_wall(self):
        """Edit Shape shows the sketch on the wall's flat part (ADR 0001)."""
        for label, props in [("90", {}), ("45", {"angle": 45.0}), ("135", {"angle": 135.0}),
                             ("Invert", {"invert": True}),
                             ("Outer Sharp", {"LengthSpec": "Outer Sharp"})]:
            with self.subTest(label):
                wall = add_wall(self.doc, self.plate, **props)
                sketch = SheetMetalCmd.smCreateWallShapeSketch(wall)
                top = max(sketch.Shape.Edges,
                          key=lambda e: e.CenterOfMass.distanceToPoint(self.plate.Shape.CenterOfMass))
                self.assertLess(wall.Shape.distToShape(Part.Vertex(top.CenterOfMass))[0], 1e-6)

    def test_starting_sketch_is_seen_from_outside(self):
        """The sketch is seen from the outside of the bend, looking back at the
        edge. Invert keeps the same side (so the sketch never mirrors), which
        then shows the inside.
        """
        s45 = math.sqrt(0.5)
        top_right = ((PLATE_W, 0, THK), (PLATE_W, PLATE_L, THK))
        cases = [
            # label, edge, props, direction the viewer looks from
            ("top edge, up", top_right, {}, (1, 0, 0)),
            ("top edge, inverted", top_right, {"invert": True}, (-1, 0, 0)),
            ("bottom edge, down", ((PLATE_W, 0, 0), (PLATE_W, PLATE_L, 0)), {}, (1, 0, 0)),
            ("bottom rear edge, down", ((0, PLATE_L, 0), (PLATE_W, PLATE_L, 0)), {}, (0, 1, 0)),
            ("other side of the plate", ((0, 0, THK), (0, PLATE_L, THK)), {}, (-1, 0, 0)),
            ("45 degrees", top_right, {"angle": 45.0}, (s45, 0, -s45)),
            ("135 degrees", top_right, {"angle": 135.0}, (s45, 0, s45)),
        ]
        for label, edge, props, expected in cases:
            with self.subTest(label):
                wall = add_wall(self.doc, self.plate, edge=edge, **props)
                sketch = SheetMetalCmd.smCreateWallShapeSketch(wall)
                normal = sketch.getGlobalPlacement().Rotation.multVec(Vector(0, 0, 1))
                self.assertGreater(normal.dot(Vector(*expected)), 0.999, f"normal {normal}")
                self.assertEqual(sketch.solve(), 0)
                self.assertValidSheet(wall)

    def test_stale_frame_from_an_older_file(self):
        """Edit Shape uses the wall as it is now, not the frame a file stored.

        A wall loaded from a file isn't recomputed until something changes,
        so its stored frame can come from older code.
        """
        wall = add_wall(self.doc, self.plate)
        stale = FreeCAD.Placement(Vector(0, 0, 0), FreeCAD.Rotation(Vector(0, 0, 1), 180))
        wall.ShapeFrame = stale
        sketch = SheetMetalCmd.smCreateWallShapeSketch(wall)
        normal = sketch.getGlobalPlacement().Rotation.multVec(Vector(0, 0, 1))
        self.assertGreater(normal.dot(Vector(1, 0, 0)), 0.999, f"normal {normal}")
        wall.ShapeFrame = stale
        SheetMetalCmd.smRefreshWallShapeTilt(wall)
        self.doc.recompute()
        normal = sketch.getGlobalPlacement().Rotation.multVec(Vector(0, 0, 1))
        self.assertGreater(normal.dot(Vector(1, 0, 0)), 0.999, f"after refresh {normal}")

    def test_sketch_stays_put_when_the_viewing_side_changes(self):
        """Invert changes which side the sketch is seen from, but never mirrors it.

        The sketch's direction along the edge doesn't depend on Invert.
        """
        wall = add_wall(self.doc, self.plate, gap1=5.0, gap2=20.0)
        sketch = SheetMetalCmd.smCreateWallShapeSketch(wall)
        before = upper_part(wall).BoundBox
        wall.invert = True
        self.doc.recompute()
        SheetMetalCmd.smRefreshWallShapeTilt(wall)
        self.doc.recompute()
        self.assertValidSheet(wall)
        self.assertEqual(sketch.solve(), 0)
        lower = wall.Shape.common(Part.makeBox(200, 400, 200, Vector(-50, -150, -210))).BoundBox
        self.assertAlmostEqual(lower.YMin, before.YMin, places=3)
        self.assertAlmostEqual(lower.YMax, before.YMax, places=3)

    def test_custom_outlines(self):
        """Each outline gives the volume its flat pattern says, and a valid solid.

        Chains are listed left to right. Plate edge 0..100, Leg unless noted.
        """
        ba = bend_allowance()
        outer = (RADIUS + THK)  # Outer Sharp setback at 90 degrees
        cases = [
            # label, chain, props, bend span, reliefs
            ("one end dragged past the corner (Creo)",
             [(-5, 12), (-5, 20), (100, 20), (100, 0)], {}, (0, 100), 0),
            ("centred tab: loose ends on the reference line narrow the bend",
             [(30, 0), (30, 15), (70, 15), (70, 0)], {}, (30, 70), 2),
            ("one corner notched",
             [(0, 0), (0, 20), (80, 20), (80, 12), (100, 12), (100, 0)], {}, (0, 100), 0),
            ("tapered: closing lines angle through the bend",
             [(20, 18), (80, 18)], {}, (0, 100), 0),
            ("taper with Outer Sharp",
             [(20, 18), (80, 18)], {"LengthSpec": "Outer Sharp"}, (0, 100), 0),
            ("arc on top",
             None, {}, (0, 100), 0),
        ]
        for label, chain, props, span, reliefs in cases:
            with self.subTest(label):
                setback = outer if props.get("LengthSpec") == "Outer Sharp" else 0.0
                if chain is None:
                    wall = add_wall(self.doc, self.plate, **props)
                    sketch = self.doc.addObject("Sketcher::SketchObject", "Shape")
                    sketch.addGeometry(Part.LineSegment(Vector(0, 0, 0), Vector(0, 10, 0)))
                    sketch.addGeometry(Part.ArcOfCircle(
                        Part.Circle(Vector(50, 10, 0), Vector(0, 0, 1), 50), 0, math.pi))
                    sketch.addGeometry(Part.LineSegment(Vector(100, 10, 0), Vector(100, 0, 0)))
                    wall.ShapeSketch = sketch
                    self.doc.recompute()
                    area = 100 * (10 + ba - setback) + math.pi * 50 ** 2 / 2
                else:
                    wall = self.shaped_wall(chain, **props)
                    area = outline_area(chain, *span, ba - setback)
                self.assertValidSheet(wall)
                expected = PLATE_VOLUME + area * THK - reliefs * RELIEF_VOLUME
                self.assertAlmostEqual(wall.Shape.Volume, expected, delta=expected * 2e-4)

    def assertFlatOutline(self, chain, span, reliefs, old_unfolder):
        ba = bend_allowance()
        wall = self.shaped_wall(chain)
        flat = unfold(self.doc, wall, old_unfolder)
        self.assertValidSheet(flat)
        expected = PLATE_VOLUME + outline_area(chain, *span, ba) * THK - reliefs * RELIEF_VOLUME
        self.assertAlmostEqual(flat.Shape.Volume, expected, delta=expected * 2e-4)
        self.assertAlmostEqual(flat.Shape.BoundBox.ZLength, THK, places=3)
        top = max(y for _x, y in chain)
        self.assertAlmostEqual(flat.Shape.BoundBox.XLength, PLATE_W + ba + top, places=2)

    def test_unfolders_give_the_outline(self):
        """Unfolding a shaped wall gives the plate plus the outline, flat.

        The old unfolder is only checked on square bend ends: see
        test_old_unfolder_angled_bend_ends.
        """
        cases = [
            # label, chain, bend span, reliefs, also check the old unfolder
            ("Creo drag", [(-5, 12), (-5, 20), (100, 20), (100, 0)], (0, 100), 0, False),
            ("centred tab", [(30, 0), (30, 15), (70, 15), (70, 0)], (30, 70), 2, True),
            ("taper", [(20, 18), (80, 18)], (0, 100), 0, False),
        ]
        for label, chain, span, reliefs, check_old in cases:
            for old in (False, True) if check_old else (False,):
                with self.subTest(label, unfolder="old" if old else "new"):
                    self.assertFlatOutline(chain, span, reliefs, old)

    @unittest.expectedFailure
    def test_old_unfolder_angled_bend_ends(self):
        """Known gap: the old unfolder leaves a bend with angled ends 0.2 mm out of flat."""
        self.assertFlatOutline([(20, 18), (80, 18)], (0, 100), 0, True)

    def test_same_flat_part_at_any_angle(self):
        """With Leg, the flat part above the bend is the same at 45, 90 and 135 degrees."""
        chain = [(0, 0), (0, 20), (80, 20), (80, 12), (100, 12), (100, 0)]
        for angle in (45.0, 90.0, 135.0):
            with self.subTest(angle=angle):
                wall = self.shaped_wall(chain, angle=angle)
                flat = unfold(self.doc, wall, False)
                ba = bend_allowance(angle)
                expected = PLATE_VOLUME + outline_area(chain, 0, 100, ba) * THK
                self.assertAlmostEqual(flat.Shape.Volume, expected, delta=expected * 2e-4)
                self.assertAlmostEqual(flat.Shape.BoundBox.XLength, PLATE_W + ba + 20, places=2)

    def test_outer_sharp_height_ignores_the_radius(self):
        """With Outer Sharp the shape's height is measured from the edge line."""
        tops = []
        for radius in (1.0, 3.0):
            wall = self.shaped_wall([(0, 0), (0, 20), (100, 20), (100, 0)],
                                    LengthSpec="Outer Sharp", radius=radius)
            self.assertValidSheet(wall)
            tops.append(wall.Shape.BoundBox.ZMax)
        self.assertAlmostEqual(tops[0], tops[1], places=3)

    def test_wall_follows_a_shorter_edge(self):
        """The starting sketch's right side follows the far end of the edge (Q16)."""
        wall = add_wall(self.doc, self.plate, gap2=5.0, extend2=2.0)
        SheetMetalCmd.smCreateWallShapeSketch(wall)
        sketch = self.plate.BendSketch
        short = PLATE_L - 20
        sketch.moveGeometry(1, 2, Vector(PLATE_W, short, 0))
        sketch.moveGeometry(2, 1, Vector(PLATE_W, short, 0))
        sketch.moveGeometry(2, 2, Vector(0, short, 0))
        sketch.moveGeometry(3, 1, Vector(0, short, 0))
        self.doc.recompute()
        self.assertValidSheet(wall)
        self.assertAlmostEqual(upper_part(wall).BoundBox.YMax, short - 5.0 + 2.0, places=3)

    def test_gap_moves_bend_end_and_side_together(self):
        """Changing Gap 1 and the sketch's Gap1 keeps Extend 1 (Q17)."""
        wall = add_wall(self.doc, self.plate, gap1=4.0, extend1=1.0)
        sketch = SheetMetalCmd.smCreateWallShapeSketch(wall)
        wall.gap1 = 10.0
        sketch.setDatum(SheetMetalCmd.smFindNamedConstraint(sketch, "GapA"),
                        FreeCAD.Units.Quantity(10.0, FreeCAD.Units.Length))
        self.doc.recompute()
        self.assertValidSheet(wall)
        self.assertAlmostEqual(upper_part(wall).BoundBox.YMin, 10.0 - 1.0, places=3)

    def test_length_survives_editing_a_top_corner(self):
        """Trimming or rounding either top corner keeps the Length dimension.

        Sketcher's Trim drops a point-to-line distance when it trims away the
        part of the line nearest the point, so Length is measured from the
        middle of the bend span.
        """
        height, ba = 20.0, bend_allowance()
        chamfer = 6 * 6 / 2       # corner cut off by a line through (0, 14) and (6, 20)
        rounding = 9 - math.pi * 9 / 4  # corner removed by a 3 mm fillet
        cases = [
            ("trim top-left", 1, "trim", chamfer),
            ("trim top-right", -1, "trim", chamfer),
            ("round top-left", 1, "fillet", rounding),
            ("round top-right", -1, "fillet", rounding),
        ]
        for label, side, edit, removed in cases:
            with self.subTest(label):
                wall = add_wall(self.doc, self.plate)
                sketch = SheetMetalCmd.smCreateWallShapeSketch(wall)

                def at(x, y):
                    """A point measured inwards from the corner on this side."""
                    return Vector(x if side == 1 else 100 - x, y, 0)

                def geometry_at(point):
                    return min((i for i, g in enumerate(sketch.Geometry)
                                if not isinstance(g, Part.Point)),
                               key=lambda i: sketch.Geometry[i].toShape().distToShape(
                                   Part.Vertex(point))[0])

                if edit == "trim":
                    sketch.addGeometry(Part.LineSegment(at(-2, height - 8), at(8, height + 2)))
                    for pick in (at(0, height - 2), at(2, height),
                                 at(-1, height - 7), at(7, height + 1)):
                        sketch.trim(geometry_at(pick), pick)
                else:
                    top, corner_side = geometry_at(at(50, height)), geometry_at(at(0, 5))
                    sketch.fillet(top, corner_side, at(5, height), at(0, height - 5), 3.0)
                self.doc.recompute()
                index = SheetMetalCmd.smFindNamedConstraint(sketch, "Length")
                self.assertIsNotNone(index, "the Length dimension was deleted")
                self.assertValidSheet(wall)
                area = 100 * (height + ba) - removed
                self.assertAlmostEqual(wall.Shape.Volume, PLATE_VOLUME + area * THK, delta=2.0)
                before = wall.Shape.BoundBox.ZMax
                sketch.setDatum(index, FreeCAD.Units.Quantity(25.0, FreeCAD.Units.Length))
                self.doc.recompute()
                self.assertValidSheet(wall)
                self.assertAlmostEqual(wall.Shape.BoundBox.ZMax - before, 5.0, places=4)

    def test_removing_the_sketch_gives_the_plain_wall_back(self):
        wall = add_wall(self.doc, self.plate)
        plain_volume = wall.Shape.Volume
        sketch = shape_sketch(self.doc, [(30, 0), (30, 15), (70, 15), (70, 0)])
        wall.ShapeSketch = sketch
        self.doc.recompute()
        self.assertNotAlmostEqual(wall.Shape.Volume, plain_volume, places=0)
        self.doc.removeObject(sketch.Name)
        self.doc.recompute()
        self.assertIsNone(wall.ShapeSketch)
        self.assertAlmostEqual(wall.Shape.Volume, plain_volume, places=3)
        self.assertEqual(wall.getEditorMode("length"), [])

    def test_perforated_shape(self):
        """Perforation still cuts the bend of a shaped wall (Q20)."""
        chain = [(30, 0), (30, 15), (70, 15), (70, 0)]
        solid = self.shaped_wall(chain).Shape.Volume
        wall = self.shaped_wall(chain, Perforate=True)
        self.assertValidSheet(wall)
        self.assertLess(wall.Shape.Volume, solid - 1.0)

    def test_errors(self):
        """Bad shape sketches fail with a clear message, not a traceback."""
        cases = [
            ("closed", [(0, 0), (0, 10), (100, 10), (100, 0), (0, 0)], {}, "must be open"),
            ("dips onto the sheet", [(0, 0), (0, 10), (50, -2), (100, 10), (100, 0)], {},
             "dips below the reference line"),
            ("closing line crosses the sketch",
             [(60, 10), (60, 3), (20, 3), (20, 20), (100, 20), (100, 0)], {}, "crosses"),
            ("slot splits the bend",
             [(0, 0), (0, 20), (100, 20), (100, 0), (60, 0), (60, 10), (40, 10), (40, 0)],
             {"LengthSpec": "Outer Sharp"}, "splits the bend"),
        ]
        for label, chain, props, message in cases:
            with self.subTest(label):
                wall = self.shaped_wall(chain, **props)
                self.assertIn("Invalid", wall.State)
                self.assertIn(message, wall.getStatusString())
        with self.subTest("two pieces"):
            wall = add_wall(self.doc, self.plate)
            sketch = shape_sketch(self.doc, [(0, 0), (0, 10)])
            sketch.addGeometry(Part.LineSegment(Vector(100, 0, 0), Vector(100, 10, 0)))
            wall.ShapeSketch = sketch
            self.doc.recompute()
            self.assertIn("one connected chain", wall.getStatusString())
        with self.subTest("wall on two edges"):
            from SMTests.testFeatures import edge_name
            wall = self.doc.addObject("Part::FeaturePython", "Wall2")
            SheetMetalCmd.SMBendWall(wall, self.plate, [
                edge_name(self.plate.Shape, (PLATE_W, 0, THK), (PLATE_W, PLATE_L, THK)),
                edge_name(self.plate.Shape, (0, 0, THK), (0, PLATE_L, THK))])
            self.doc.recompute()
            self.assertIn("one edge", SheetMetalCmd.smWallShapeUnavailableReason(wall))
            wall.ShapeSketch = shape_sketch(self.doc, [(0, 0), (0, 10), (100, 10), (100, 0)])
            self.doc.recompute()
            self.assertIn("exactly one edge", wall.getStatusString())
