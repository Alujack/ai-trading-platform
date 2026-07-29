# xaubot — vendored research snapshot

Third-party code imported for reference. **Nothing here runs as part of the
platform.** It is the upstream research project that
`services/data/strategies/ml_xau.py` was ported from, kept in-tree so the port
can be audited against its source.

## Provenance

| | |
|---|---|
| Source | https://github.com/andywarui/xaubot |
| Commit | `d87137286d7ea472871c21779dc66aed2d9901a1` |
| Upstream date | 2025-12-28 — *"Merge pull request #8 from andywarui/claude/mt5-model-research-yfrWh"* |
| Imported | 2026-07-29 |
| Import style | Files-only snapshot. Upstream's 67 commits are **not** included. |

### Why files-only

Upstream committed its entire virtualenv: 12,576 of its 12,817 tracked files
live under `.venv/`, which is what makes its `.git` 131 MB. A subtree merge
would have moved those blobs into this repo's object store permanently,
taking every future clone from ~47 MB to ~180 MB. The 241 real project files
are 4.8 MB.

## What was excluded

| Excluded | Count | Why |
|---|---|---|
| `.venv/` | 12,576 | Committed virtualenv (see above) |
| `__pycache__/`, `*.pyc` | 13 | Build artifacts; this repo's `.gitignore` drops them anyway |
| `.ipynb_checkpoints/` | 4 | Editor artifacts |
| `xaubot` (gitlink) | 1 | Dangling submodule — mode `160000` at `9e894786`, but upstream has no `.gitmodules` and the directory is empty. Unresolvable. |
| upstream `.gitattributes` | 1 | Would enable LFS filters this repo can't satisfy — see the local `.gitattributes` |
| upstream `.gitignore` | 1 | Would have swallowed 3 tracked `python_training/models/*_config.json` files on re-add |

Also imported: three files that were uncommitted in the working copy at import
time (`RUN_LOCAL.md`, `python_backtesting/run_local.py`,
`requirements-local.txt`) — local work that does not exist upstream at the
recorded SHA.

**224 files total.**

## The data and most models are not here

44 imported files are **Git-LFS pointer stubs, not real content** — ~130-byte
text files. Upstream's LFS objects were never fetched. This covers:

- everything in `data/processed/` (all 35 `.parquet` / `.npy` / `.csv`)
- `python_training/models/` — `hybrid_lightgbm.onnx`, `lightgbm_xauusd.onnx`,
  `lightgbm_xauusd.pkl`, `transformer.onnx`, `multi_tf_scaler.pkl`,
  `multi_tf_transformer_price.pth`
- `mt5_expert_advisor/Files/NeuralBot/` — `hybrid_lightgbm.onnx`, `transformer.onnx`
- the `docs/` PDF

Resolving them needs `git lfs pull` against upstream, which requires access to
andywarui's LFS storage. **Until then the training and ensemble pipelines here
cannot be run.**

### The one real model

`MT5_XAUBOT/Files/lightgbm_real_26features.onnx` (309,401 bytes,
sha256 `7ddc58dd738ac4e49ea8bcd0fdbed8afdb440e21e367c62416bcb2a1abb14bd4`) is
real bytes, because upstream's `.gitattributes` exempted that one path from LFS.

It is **byte-identical** to `services/data/models/lightgbm_real_26features.onnx`,
already in this repo. That is the only artifact that actually crossed over.

## Relationship to the platform

`services/data/strategies/ml_xau.py` is the honest re-port of the model above,
registered in `services/data/strategies/registry.py` as `ml_xau` plus an
`ml_xau_random` control. Read that module's docstring before trusting anything
in the upstream documentation here.

Upstream's headline claims — 66.2% win rate, 1.96 profit factor, 3,780% return —
do **not** reproduce, and the markdown files in this directory repeat them
throughout. Verified on this platform's data:

- 10 of the model's 26 inputs (`mtf_0..mtf_9`) are hard-coded `0.0` — placeholders
  for a Transformer that was never shipped. It is a 16-feature model in a
  26-feature interface.
- On 2026 XAUUSD 1min it is degenerate: ~88% HOLD, ~12% SHORT, ~0% LONG.
- Its advertised 0.55 confidence threshold produces **zero trades** — SHORT
  probability peaks at 0.476.
- `ema_10/20/50`, `tr` and `atr_14` are raw price-scale features, binding the
  model to the price regime it trained in.

`services/data/training/` is this platform's own replacement pipeline, written
from that post-mortem — see the module docstrings in `features.py`, `labels.py`
and `train.py`, each of which names the specific upstream defect it exists to
avoid.

## Licensing

Upstream's `README.md` states MIT and links an MIT badge, but **the repository
contains no `LICENSE` file** at the imported commit. The grant is therefore
asserted in prose only. Worth resolving with the author before any of this is
redistributed or used commercially.
