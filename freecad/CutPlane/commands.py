# SPDX-License-Identifier: LGPL-2.1-or-later
"""FreeCAD commands and the workbench manipulator that shows them everywhere."""

import FreeCAD as App
import FreeCADGui as Gui

from . import icon


def _active_3d_view():
    gdoc = Gui.ActiveDocument
    if gdoc is None:
        return None
    view = gdoc.ActiveView
    return view if hasattr(view, "getSceneGraph") else None


def _session():
    from .session import Session
    return Session


class SectionViewCommand:
    def GetResources(self):
        return {
            "Pixmap": icon("CutPlane_Section.svg"),
            "MenuText": "Section view",
            "ToolTip": ("Live section view: click a face, edge or datum to place the cutting plane, "
                        "drag its handles, see the cut faces filled and hatched. Toggles on and off."),
            "Accel": "Shift+X",
        }

    def IsActive(self):
        return _active_3d_view() is not None

    def Activated(self):
        _session().toggle()


class EditSectionCommand:
    def GetResources(self):
        return {
            "Pixmap": icon("CutPlane_Edit.svg"),
            "MenuText": "Edit section view",
            "ToolTip": "Show the section view panel and handles again",
        }

    def IsActive(self):
        try:
            return _session().current is not None
        except Exception:
            return False

    def Activated(self):
        _session().edit()


class Manipulator:
    """Puts the commands into every workbench: View menu, View toolbar, context menus."""

    def modifyMenuBar(self):
        return [
            {"insert": "CutPlane_EditSection", "menuItem": "Std_ToggleClipPlane", "after": ""},
            {"insert": "CutPlane_SectionView", "menuItem": "Std_ToggleClipPlane", "after": ""},
        ]

    def modifyToolBars(self):
        return [{"append": "CutPlane_SectionView", "toolBar": "View"}]

    def modifyContextMenu(self, recipient):
        # After "Align to selection", which the 3D view's menu always has; the
        # entries from "Toggle visibility" on appear only with a selection.
        if recipient == "View":
            return [{"insert": "CutPlane_SectionView", "menuItem": "Std_AlignToSelection", "after": ""}]
        return []


def register():
    Gui.addCommand("CutPlane_SectionView", SectionViewCommand())
    Gui.addCommand("CutPlane_EditSection", EditSectionCommand())
    Gui.addWorkbenchManipulator(Manipulator())
    try:
        from . import prefs
        prefs.register()
    except Exception as e:  # the commands work without the preference page
        App.Console.PrintWarning("CutPlane: no preference page: %s\n" % e)
