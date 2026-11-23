# SPDX-License-Identifier: LGPL-2.1-or-later
"""The section view panel: a dock window, so the Tasks panel stays free (Measure etc.)."""

from PySide import QtCore, QtGui

import FreeCADGui as Gui

from . import prefs

HINT = ("Click a face, edge or datum to place the plane; Ctrl+click adds one. "
        "Drag the grey ball to move it, the red or green arrows to tilt it, the small white "
        "ball to snap it through a point; click the grey ball to flip.")


def _button(text, tip, slot, checkable=False):
    b = QtGui.QToolButton()
    b.setText(text)
    b.setToolTip(tip)
    b.setCheckable(checkable)
    b.setAutoRaise(False)
    b.clicked.connect(slot)
    return b


class SectionPanel(QtGui.QDockWidget):
    def __init__(self, session):
        super().__init__("Section view", Gui.getMainWindow())
        self.s = session
        self.setObjectName("CutPlaneSectionPanel")
        self.setFeatures(QtGui.QDockWidget.DockWidgetMovable | QtGui.QDockWidget.DockWidgetFloatable
                         | QtGui.QDockWidget.DockWidgetClosable)
        body = QtGui.QWidget()
        lay = QtGui.QVBoxLayout(body)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)

        hint = QtGui.QLabel(HINT)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        lay.addWidget(hint)

        # planes -------------------------------------------------------------
        box = QtGui.QGroupBox("Plane")
        g = QtGui.QGridLayout(box)
        row = QtGui.QHBoxLayout()
        self.plane_buttons = []
        self.plane_group = QtGui.QButtonGroup(self)
        self.plane_group.setExclusive(True)
        for i in range(3):
            b = QtGui.QToolButton()
            b.setText(str(i + 1))
            b.setCheckable(True)
            b.setToolTip("Edit plane %d" % (i + 1))
            self.plane_group.addButton(b, i)
            row.addWidget(b)
            self.plane_buttons.append(b)
        if hasattr(self.plane_group, "idClicked"):            # Qt 6
            self.plane_group.idClicked.connect(self.s.set_active)
        else:
            self.plane_group.buttonClicked[int].connect(self.s.set_active)
        self.add_btn = _button("+", "Add a plane (or Ctrl+click a face)", lambda: self.s.add_plane())
        self.del_btn = _button("−", "Remove the active plane", self.s.remove_plane)
        row.addWidget(self.add_btn)
        row.addWidget(self.del_btn)
        row.addStretch(1)
        g.addLayout(row, 0, 0, 1, 4)

        self.ref_label = QtGui.QLabel()
        self.ref_label.setWordWrap(True)
        g.addWidget(self.ref_label, 1, 0, 1, 4)

        g.addWidget(QtGui.QLabel("Offset"), 2, 0)
        self.offset = QtGui.QDoubleSpinBox()
        self.offset.setDecimals(2)
        self.offset.setRange(-1e6, 1e6)
        self.offset.setSuffix(" mm")
        self.offset.setKeyboardTracking(False)
        self.offset.setToolTip("Distance from where the plane was placed, in the direction of the handle's stem")
        self.offset.valueChanged.connect(self._offset_changed)
        g.addWidget(self.offset, 2, 1)
        self.tilt_label = QtGui.QLabel()
        g.addWidget(self.tilt_label, 2, 2, 1, 2)

        row2 = QtGui.QHBoxLayout()
        row2.addWidget(_button("Flip", "Keep the other side (or click the grey ball)", self.s.flip_active))
        row2.addWidget(_button("Reset", "Back to where the plane was placed", self.s.reset_active))
        row2.addWidget(_button("View normal", "Look straight at the cut", self.s.view_normal))
        row2.addStretch(1)
        g.addLayout(row2, 3, 0, 1, 4)

        row3 = QtGui.QHBoxLayout()
        row3.addWidget(QtGui.QLabel("Default:"))
        for which, text in (("top", "Top"), ("front", "Front"), ("right", "Right")):
            row3.addWidget(_button(text, "%s plane through the middle of the model" % text,
                                   lambda checked=False, w=which: self.s.default_plane(w)))
        row3.addStretch(1)
        g.addLayout(row3, 4, 0, 1, 4)
        lay.addWidget(box)

        # parts ---------------------------------------------------------------
        box2 = QtGui.QGroupBox("Parts")
        v2 = QtGui.QVBoxLayout(box2)
        mode_row = QtGui.QHBoxLayout()
        mode_row.addWidget(QtGui.QLabel("A click in the 3D view"))
        self.mode = QtGui.QComboBox()
        for mode, text, tip in (("align", "places the plane", "Clicked faces, edges and datums place the plane"),
                                ("include", "picks parts to cut", "Only the clicked parts are cut"),
                                ("exclude", "picks parts to keep whole", "Clicked parts are not cut")):
            self.mode.addItem(text, mode)
            self.mode.setItemData(self.mode.count() - 1, tip, QtCore.Qt.ToolTipRole)
        self.mode.currentIndexChanged.connect(lambda i: self._set_mode(self.mode.itemData(i)))
        mode_row.addWidget(self.mode, 1)
        v2.addLayout(mode_row)
        self.include_label = QtGui.QLabel()
        self.include_label.setWordWrap(True)
        self.exclude_label = QtGui.QLabel()
        self.exclude_label.setWordWrap(True)
        for lab, which in ((self.include_label, "include"), (self.exclude_label, "exclude")):
            r = QtGui.QHBoxLayout()
            r.addWidget(lab, 1)
            r.addWidget(_button("×", "Empty this list", lambda checked=False, w=which: self.s.clear_list(w)))
            v2.addLayout(r)
        lay.addWidget(box2)

        # display -------------------------------------------------------------
        box3 = QtGui.QGroupBox("Display")
        v3 = QtGui.QVBoxLayout(box3)
        r1 = QtGui.QHBoxLayout()
        r1.addWidget(QtGui.QLabel("Cut faces"))
        self.fill_mode = QtGui.QComboBox()
        self.fill_mode.addItem("Part colour", "part")
        self.fill_mode.addItem("One colour", "single")
        self.fill_mode.currentIndexChanged.connect(self._opts_changed)
        r1.addWidget(self.fill_mode, 1)
        self.color_btn = _button("■", "Colour of the cut faces in 'One colour' mode", self._pick_color)
        r1.addWidget(self.color_btn)
        v3.addLayout(r1)
        r2 = QtGui.QHBoxLayout()
        self.hatch = QtGui.QCheckBox("Hatch")
        self.hatch.setToolTip("Hatch the cut faces (spacing: Edit > Preferences > CutPlane)")
        self.hatch.toggled.connect(self._opts_changed)
        r2.addWidget(self.hatch)
        self.outline = QtGui.QCheckBox("Outline")
        self.outline.setToolTip("Outline the cut faces (width: Edit > Preferences > CutPlane)")
        self.outline.toggled.connect(self._opts_changed)
        r2.addWidget(self.outline)
        r2.addStretch(1)
        v3.addLayout(r2)
        r3 = QtGui.QHBoxLayout()
        self.interference = QtGui.QCheckBox("Overlaps red")
        self.interference.setToolTip("Show where parts overlap in the cut in red, and name them below")
        self.interference.toggled.connect(self._opts_changed)
        r3.addWidget(self.interference)
        self.show_plane = QtGui.QCheckBox("Plane frame")
        self.show_plane.setToolTip("Dashed outline of the active plane while editing")
        self.show_plane.toggled.connect(self._opts_changed)
        r3.addWidget(self.show_plane)
        r3.addStretch(1)
        v3.addLayout(r3)
        lay.addWidget(box3)

        self.status = QtGui.QLabel()
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        bottom = QtGui.QHBoxLayout()
        done = QtGui.QPushButton("Done")
        done.setToolTip("Hide this panel and the handles; the section stays (Edit section view brings them back)")
        done.clicked.connect(lambda: self.s.show_panel(False))
        off = QtGui.QPushButton("Turn off")
        off.setToolTip("End the section view")
        off.clicked.connect(self.s.close)
        bottom.addStretch(1)
        bottom.addWidget(done)
        bottom.addWidget(off)
        lay.addLayout(bottom)
        lay.addStretch(1)

        scroll = QtGui.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)   # wrap to the dock width
        scroll.setFrameShape(QtGui.QFrame.NoFrame)
        scroll.setWidget(body)
        self.setWidget(scroll)
        bar = scroll.verticalScrollBar().sizeHint().width()
        self.setMinimumWidth(max(300, body.minimumSizeHint().width() + bar + 4))

        self._load_opts()
        mw = Gui.getMainWindow()
        mw.addDockWidget(QtCore.Qt.RightDockWidgetArea, self)
        QtCore.QTimer.singleShot(0, self._claim_height)
        self.refresh_planes()
        self.refresh_lists()

    def _claim_height(self):
        """Once docked: take about 60 % of the column it shares with the Tasks panel."""
        try:
            mw = Gui.getMainWindow()
            others = [d for d in mw.findChildren(QtGui.QDockWidget)
                      if d is not self and d.isVisible() and not d.isFloating()
                      and mw.dockWidgetArea(d) == mw.dockWidgetArea(self)
                      and abs(d.geometry().x() - self.geometry().x()) < 20]
            total = self.height() + sum(d.height() for d in others)
            if others and total > 0:
                mine = int(total * 0.6)
                rest = max(int((total - mine) / len(others)), 80)
                mw.resizeDocks([self] + others, [mine] + [rest] * len(others), QtCore.Qt.Vertical)
        except Exception:
            pass

    # -- session -> panel ------------------------------------------------------
    def closeEvent(self, ev):
        """The dock's own close button: like Done, the section stays."""
        ev.ignore()
        self.s.show_panel(False)

    def dispose(self):
        try:
            Gui.getMainWindow().removeDockWidget(self)
        except Exception:
            pass
        self.deleteLater()

    def refresh_planes(self):
        n = len(self.s.planes)
        for i, b in enumerate(self.plane_buttons):
            b.setVisible(i < n and n > 1)
            b.setChecked(i == self.s.active)
        self.add_btn.setEnabled(n < 3)
        self.del_btn.setEnabled(n > 1)
        self.refresh_values()

    def refresh_values(self):
        if not self.s.planes:
            return
        p = self.s.planes[self.s.active]
        kind = p.ref.kind
        self.ref_label.setText("<b>%s</b>%s" % (p.label or "Plane", (" — " + kind) if kind else ""))
        self.offset.blockSignals(True)
        self.offset.setValue(p.offset())
        step = max(self.s.hatch_spacing(), 0.01)
        self.offset.setSingleStep(step)
        self.offset.blockSignals(False)
        t = p.tilt()
        self.tilt_label.setText("tilted %.1f°" % t if t > 0.05 else "")

    def refresh_lists(self):
        inc = ", ".join(self.s.part_label(k) for k in self.s.include)
        exc = ", ".join(self.s.part_label(k) for k in self.s.exclude)
        self.include_label.setText("Only cut: %s" % (inc or "<i>all visible parts</i>"))
        self.exclude_label.setText("Not cut: %s" % (exc or "<i>none</i>"))

    def refresh_status(self):
        txt = self.s.status
        if prefs.params().GetBool("ShowTiming", False):
            txt += " · %.0f ms" % self.s.last_update_ms
        self.status.setText(txt)

    # -- panel -> session ------------------------------------------------------
    def _offset_changed(self, val):
        p = self.s.planes[self.s.active]
        p.set_offset(val)
        self.s.plane_changed()

    def _set_mode(self, mode):
        self.s.pick_mode = mode or "align"

    def refresh_options(self):
        """The options changed elsewhere (Edit > Preferences)."""
        self._load_opts()

    def _load_opts(self):
        o = self.s.opts
        widgets = (self.fill_mode, self.hatch, self.outline, self.interference, self.show_plane)
        for w in widgets:
            w.blockSignals(True)
        self.fill_mode.setCurrentIndex(0 if o.fill_mode == "part" else 1)
        self.hatch.setChecked(o.hatch)
        self.outline.setChecked(o.outline)
        self.interference.setChecked(o.interference)
        self.show_plane.setChecked(o.show_plane)
        for w in widgets:
            w.blockSignals(False)
        self._color_icon()

    def _opts_changed(self, *_args):
        o = self.s.opts
        o.fill_mode = self.fill_mode.currentData() or "part"
        o.hatch = self.hatch.isChecked()
        o.outline = self.outline.isChecked()
        o.interference = self.interference.isChecked()
        o.show_plane = self.show_plane.isChecked()
        o.save()
        self.s.plane_changed()

    def _color_icon(self):
        r, g, b = self.s.opts.single_rgb()
        self.color_btn.setStyleSheet("color: rgb(%d,%d,%d);" % (r * 255, g * 255, b * 255))

    def _pick_color(self):
        r, g, b = self.s.opts.single_rgb()
        c = QtGui.QColorDialog.getColor(QtGui.QColor.fromRgbF(r, g, b), self, "Cut face colour")
        if c.isValid():
            self.s.opts.single_color = "%.4f,%.4f,%.4f" % (c.redF(), c.greenF(), c.blueF())
            self.s.opts.save()
            self._color_icon()
            self.s.plane_changed()
