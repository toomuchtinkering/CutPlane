#!/bin/sh
# Unit tests of the pure core, with the FreeCAD flatpak's python (numpy + shapely).
here=$(cd "$(dirname "$0")" && pwd)
exec flatpak run --command=python3 org.freecad.FreeCAD -I "$here/test_geom.py" -v
