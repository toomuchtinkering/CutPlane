# SPDX-License-Identifier: LGPL-2.1-or-later
# CutPlane: live section view for FreeCAD. FreeCAD imports this module when the
# GUI starts (freecad.<Mod>.init_gui). It only registers the commands, the menu
# entries and the preference page; the rest is imported when a command first runs.

try:
    from . import commands

    commands.register()
except Exception as e:  # never stop FreeCAD from starting
    import FreeCAD

    FreeCAD.Console.PrintError("CutPlane: could not register its commands: %s\n" % e)
