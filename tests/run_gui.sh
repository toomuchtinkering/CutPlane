#!/bin/sh
# Run a FreeCAD GUI script on a private Xvfb display (no window on the desktop).
# Usage: tests/run_gui.sh [script.py]   (default: tests/gui_test.py)
here=$(cd "$(dirname "$0")" && pwd)
script=${1:-$here/gui_test.py}
case "$script" in /*) ;; *) script="$PWD/$script" ;; esac
if command -v xvfb-run >/dev/null 2>&1; then
    run="xvfb-run -a -s '-screen 0 1920x1200x24'"
else
    echo "xvfb-run not found: running on the desktop" >&2
    run=""
fi
eval timeout 300 $run flatpak run org.freecad.FreeCAD "$script" 2>&1 \
    | grep -v -E "^(Qt|libpng|QStandardPaths|Gtk-Message)"
