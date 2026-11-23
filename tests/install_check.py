"""Checks the installed addon (Mod/CutPlane) registers itself: run with tests/run_gui.sh."""
import os, time, traceback
fails = []
def out(*a): os.write(1, (" ".join(str(x) for x in a) + "\n").encode())
def check(c, what):
    out(("  ok   " if c else "  FAIL ") + what)
    if not c: fails.append(what)
def pump(n=10):
    from PySide import QtGui
    for _ in range(n): QtGui.QApplication.processEvents(); time.sleep(0.02)
try:
    import FreeCAD as App, FreeCADGui as Gui
    from PySide import QtGui, QtCore
    import sys
    import freecad.CutPlane as cp
    mod = os.path.join(App.getUserAppDataDir(), "Mod", "CutPlane")
    check(cp.__file__.replace(os.sep, "/").startswith(mod.replace(os.sep, "/") + "/freecad/CutPlane/"),
          "freecad.CutPlane imported from the Mod folder (%s)" % cp.__file__)
    check("freecad.CutPlane.init_gui" in sys.modules, "FreeCAD ran freecad.CutPlane.init_gui at start-up")
    meta = App.Metadata(os.path.join(mod, "package.xml"))
    check(meta.Name == "CutPlane" and "other" in meta.Content, "package.xml: name CutPlane, content <other>")
    check(meta.supportsCurrentFreeCAD(), "package.xml: supports this FreeCAD (min %s)" % meta.FreeCADMin)
    check(os.path.exists(os.path.join(mod, meta.Icon)), "package.xml: icon exists (%s)" % meta.Icon)
    check([l["name"] for l in meta.License] == ["LGPL-2.1-or-later", "MIT"]
          and all(os.path.exists(os.path.join(mod, l["file"])) for l in meta.License),
          "package.xml: both licences declared, their files exist")
    check(sorted(d["package"] for d in meta.Depend) == ["numpy", "shapely"], "package.xml: depends on numpy, shapely")
    cmd = Gui.Command.get("CutPlane_SectionView")
    check(cmd is not None, "command CutPlane_SectionView registered")
    check(cmd is not None and cmd.getShortcut() == "Shift+X", "shortcut Shift+X (%s)" % (cmd.getShortcut() if cmd else None))
    from freecad.CutPlane import prefs
    check(prefs._registered, "Edit > Preferences > CutPlane page registered at start-up")
    mw = Gui.getMainWindow()
    for wb in ("PartWorkbench", "PartDesignWorkbench", "AssemblyWorkbench"):
        try:
            Gui.activateWorkbench(wb)
        except Exception as e:
            out("  (could not activate %s: %s)" % (wb, e)); continue
        pump()
        texts = []
        for m in mw.menuBar().actions():
            if m.menu() is not None:
                texts += [(m.text().replace("&", ""), a.text().replace("&", "")) for a in m.menu().actions()]
        check(("View", "Section view") in texts, "%s: 'Section view' in the View menu" % wb)
        check(("View", "Edit section view") in texts, "%s: 'Edit section view' in the View menu" % wb)
        tbs = [tb for tb in mw.findChildren(QtGui.QToolBar) if tb.objectName() == "View"]
        check(any("Section view" in [a.text().replace("&", "") for a in tb.actions()] for tb in tbs),
              "%s: button on the View toolbar" % wb)
    # right-click menu of the 3D view, with nothing selected and with a selection
    doc = App.newDocument("Ctx"); doc.addObject("Part::Box", "Box"); doc.recompute(); pump()
    view = Gui.ActiveDocument.ActiveView
    w = view.graphicsView()
    while w is not None and not isinstance(w, QtGui.QMdiSubWindow): w = w.parentWidget()
    mw.findChild(QtGui.QMdiArea).setActiveSubWindow(w); pump()
    view.fitAll(); pump()
    vp = view.graphicsView().viewport()

    def context_menu():
        seen = []
        def grab():
            m = QtGui.QApplication.activePopupWidget()
            if m is not None:
                seen.extend(a.text().replace("&", "") for a in m.actions())
                m.close()
        pos = QtCore.QPointF(vp.width() / 2, vp.height() / 2)
        QtCore.QTimer.singleShot(700, grab)
        for kind in (QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonRelease):
            ev = QtGui.QMouseEvent(kind, pos, vp.mapToGlobal(pos.toPoint()), QtCore.Qt.RightButton,
                                   QtCore.Qt.RightButton if kind == QtCore.QEvent.MouseButtonPress else QtCore.Qt.NoButton,
                                   QtCore.Qt.NoModifier)
            QtGui.QApplication.sendEvent(vp, ev)
        pump(60)
        return seen
    Gui.Selection.clearSelection()
    seen = context_menu()
    out("  context menu, nothing selected:", seen)
    check("Section view" in seen, "'Section view' in the 3D view's context menu with nothing selected")
    Gui.Selection.addSelection(doc.Name, "Box", "Face6", 5, 5, 10)
    seen = context_menu()
    out("  context menu, a face selected:", seen)
    check(seen.count("Section view") == 1, "'Section view' once in the context menu with a selection")
except Exception:
    out(traceback.format_exc()); fails.append("exception")
finally:
    out("INSTALL CHECK " + ("OK" if not fails else "FAILED: " + "; ".join(fails)))
    os._exit(0)
