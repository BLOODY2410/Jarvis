"""Bundle the native extension from ``webrtcvad-wheels``.

The upstream hook asks for distribution metadata named ``webrtcvad`` while the
Windows wheel is published as ``webrtcvad-wheels``.  Importing the extension is
all the runtime needs.
"""

hiddenimports = ["_webrtcvad"]
