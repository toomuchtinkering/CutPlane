# SPDX-License-Identifier: LGPL-2.1-or-later
"""Settings that belong to the user rather than to one section: a CutPlane page
under Edit > Preferences, stored in FreeCAD's user parameters. An open section
view follows a change at once (Session watches the parameter group)."""

import FreeCAD as App

PARAMS = "User parameter:BaseApp/Preferences/Mod/CutPlane"

HANDLE_SIZE = "HandleSize"         # handle length on screen: plane to ball centre, px
HANDLE_DEFAULT, HANDLE_MIN, HANDLE_MAX = 150, 60, 400
BALL_SIZE = "BallSize"             # diameter of the handle's ball on screen, px; the cones follow
BALL_DEFAULT, BALL_MIN, BALL_MAX = 26, 12, 64
HATCH_PX = "HatchPx"               # automatic hatch: distance between lines on screen, px
HATCH_PX_DEFAULT, HATCH_PX_MIN, HATCH_PX_MAX = 18, 6, 80
HATCH_SPACING = "hatch_spacing"    # fixed hatch spacing in mm, 0 = automatic
OUTLINE_WIDTH = "OutlineWidth"     # cut-edge outline, px
OUTLINE_DEFAULT, OUTLINE_MIN, OUTLINE_MAX = 4, 1, 10


def params():
    return App.ParamGet(PARAMS)


def _int(name, default, lo, hi):
    return min(max(params().GetInt(name, default), lo), hi)


def handle_px():
    return _int(HANDLE_SIZE, HANDLE_DEFAULT, HANDLE_MIN, HANDLE_MAX)


def ball_px():
    return _int(BALL_SIZE, BALL_DEFAULT, BALL_MIN, BALL_MAX)


def hatch_px():
    return _int(HATCH_PX, HATCH_PX_DEFAULT, HATCH_PX_MIN, HATCH_PX_MAX)


def outline_px():
    return _int(OUTLINE_WIDTH, OUTLINE_DEFAULT, OUTLINE_MIN, OUTLINE_MAX)


class PreferencePage:
    """Edit > Preferences > CutPlane. FreeCAD calls loadSettings when the page
    opens and saveSettings on OK or Apply."""

    def __init__(self, parent=None):
        from PySide import QtWidgets

        self.form = QtWidgets.QWidget(parent)
        self.form.setWindowTitle("Section view")
        outer = QtWidgets.QVBoxLayout(self.form)
        self._ints = []                 # (spin box, parameter, default)

        def spin(grid, row, label, name, default, lo, hi, step, tip):
            grid.addWidget(QtWidgets.QLabel(label), row, 0)
            sb = QtWidgets.QSpinBox()
            sb.setRange(lo, hi)
            sb.setSingleStep(step)
            sb.setSuffix(" px")
            sb.setToolTip(tip + " Default %d px." % default)
            grid.addWidget(sb, row, 1)
            reset = QtWidgets.QPushButton("Default")
            reset.clicked.connect(lambda: sb.setValue(default))
            grid.addWidget(reset, row, 2)
            self._ints.append((sb, name, default))
            return sb

        box = QtWidgets.QGroupBox("Handles")
        grid = QtWidgets.QGridLayout(box)
        self.size = spin(grid, 0, "Handle length", HANDLE_SIZE, HANDLE_DEFAULT, HANDLE_MIN, HANDLE_MAX, 10,
                         "Length of the handle's stem on screen, from the plane to the ball.")
        self.ball = spin(grid, 1, "Ball size", BALL_SIZE, BALL_DEFAULT, BALL_MIN, BALL_MAX, 2,
                         "Diameter of the handle's ball on screen; the tilt arrows beside it "
                         "grow with it.")
        grid.setColumnStretch(3, 1)
        outer.addWidget(box)

        box = QtWidgets.QGroupBox("Cut faces")
        grid = QtWidgets.QGridLayout(box)
        grid.addWidget(QtWidgets.QLabel("Hatch spacing"), 0, 0)
        self.spacing = QtWidgets.QDoubleSpinBox()
        self.spacing.setRange(0.0, 1e4)
        self.spacing.setDecimals(2)
        self.spacing.setSuffix(" mm")
        self.spacing.setSpecialValueText("Automatic")
        self.spacing.setToolTip("Distance between hatch lines in the model. Automatic (0) keeps "
                                "the spacing below on screen at any zoom.")
        grid.addWidget(self.spacing, 0, 1)
        auto = QtWidgets.QPushButton("Automatic")
        auto.clicked.connect(lambda: self.spacing.setValue(0.0))
        grid.addWidget(auto, 0, 2)
        self.hatch_px = spin(grid, 1, "Automatic spacing on screen", HATCH_PX, HATCH_PX_DEFAULT,
                             HATCH_PX_MIN, HATCH_PX_MAX, 2,
                             "Distance between hatch lines on screen when the spacing is automatic, "
                             "measured across the lines, also on faces seen at an angle. Rounded to "
                             "1, 1.5, 2, 2.5, 3, 4, 5, 6 or 8 times a power of ten millimetres.")
        self.outline = spin(grid, 2, "Outline width", OUTLINE_WIDTH, OUTLINE_DEFAULT,
                            OUTLINE_MIN, OUTLINE_MAX, 1,
                            "Width of the outline on screen, drawn just inside the cut faces.")
        grid.setColumnStretch(3, 1)
        outer.addWidget(box)

        note = QtWidgets.QLabel("An open section view follows these settings at once.")
        note.setWordWrap(True)
        outer.addWidget(note)
        outer.addStretch(1)

    def loadSettings(self):
        p = params()
        for sb, name, default in self._ints:
            sb.setValue(p.GetInt(name, default))
        self.spacing.setValue(max(0.0, p.GetFloat(HATCH_SPACING, 0.0)))

    def saveSettings(self):
        p = params()
        for sb, name, _default in self._ints:
            p.SetInt(name, int(sb.value()))
        p.SetFloat(HATCH_SPACING, float(self.spacing.value()))


_registered = False


def register():
    """Once per FreeCAD run: a second call would list the page twice."""
    global _registered
    if _registered:
        return
    import FreeCADGui as Gui
    from . import ICONS

    Gui.addIconPath(ICONS)                 # group icon: preferences-cutplane.svg
    Gui.addPreferencePage(PreferencePage, "CutPlane")
    _registered = True
