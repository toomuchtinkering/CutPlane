#!/bin/sh
# SPDX-License-Identifier: LGPL-2.1-or-later
# Remakes the README pictures in docs/images: raw shots in FreeCAD on Xvfb
# (dev/make_docs.py), then crops, labels and animations on the host
# (dev/compose_docs.py: Pillow and ffmpeg). DOCS_ONLY=hero,tilt redoes a few shots.
here=$(cd "$(dirname "$0")" && pwd)
"$here/../tests/run_gui.sh" "$here/make_docs.py" | tail -n 20
python3 "$here/compose_docs.py"
