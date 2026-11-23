# SPDX-License-Identifier: LGPL-2.1-or-later
"""CutPlane: an interactive section view for FreeCAD.

Place a section plane by clicking a face, edge or datum, drag and tilt it
with handles in the 3D view, and see the cut faces filled and hatched per
part. View only: the document is never changed.
"""

import os

__version__ = "0.1.0"

ICONS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "icons")


def icon(name):
    return os.path.join(ICONS, name)
