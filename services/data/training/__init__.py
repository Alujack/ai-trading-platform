"""Model training pipeline for the ML strategies.

Kept separate from `strategies/` so the live worker never imports training-only
dependencies (lightgbm, onnxmltools). The one module that IS shared with live
inference is `features.py` — training and the strategy must build features from
the same code or they will silently drift apart.
"""
