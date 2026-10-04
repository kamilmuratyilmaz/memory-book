"""Where the people are in a photo, so cover-fit crops keep faces in the frame.

YuNet face detector (OpenCV model zoo, MIT licence: models/LICENSE-yunet), run once per photo at upload.
"""

from __future__ import annotations

import threading
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

MODEL = Path(__file__).parent / "models" / "face_detection_yunet_2023mar.onnx"
DETECT_SIZE = 640  # px, long side: enough for faces down to ~2% of the photo, ~15 ms per photo
MIN_SCORE = 0.8
cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)  # errors only (the DNN engine warns about unused targets on every create)
_local = threading.local()  # a detector is not thread-safe: one per thread


def _detector():
    if not hasattr(_local, "det"):
        _local.det = cv2.FaceDetectorYN.create(str(MODEL), "", (DETECT_SIZE, DETECT_SIZE), MIN_SCORE, 0.3, 50)
    return _local.det


def focus(im: Image.Image) -> dict:
    """{"x", "y", "faces"}: the centre (0..1 of the photo) of the main faces and how many there are,
    or {"faces": 0} when there are none. Background people (under a quarter of the largest face) are ignored."""
    small = im.convert("RGB")
    small.thumbnail((DETECT_SIZE, DETECT_SIZE))
    pixels = cv2.cvtColor(np.asarray(small), cv2.COLOR_RGB2BGR)
    h, w = pixels.shape[:2]
    det = _detector()
    det.setInputSize((w, h))
    _, found = det.detect(pixels)
    if found is None or not len(found):
        return {"faces": 0}
    largest = max(f[2] * f[3] for f in found)
    main = [f for f in found if f[2] * f[3] >= largest / 4]
    x0, y0 = min(f[0] for f in main), min(f[1] for f in main)
    x1, y1 = max(f[0] + f[2] for f in main), max(f[1] + f[3] for f in main)
    # plain floats: the result is stored as JSON (numpy float32 is not serialisable)
    return {"x": round(min(max(float(x0 + x1) / 2 / w, 0.0), 1.0), 4),
            "y": round(min(max(float(y0 + y1) / 2 / h, 0.0), 1.0), 4), "faces": len(main)}
