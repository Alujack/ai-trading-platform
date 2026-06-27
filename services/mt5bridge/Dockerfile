# MT5 bridge — containerized so the whole platform ships as one Docker stack.
#
# WHY THIS IS SPECIAL: the `MetaTrader5` Python package is Windows-only and
# drives a real MT5 terminal (a Windows GUI .exe). On Linux/Docker the terminal
# must run under Wine. We build on the purpose-built community image that bundles
# Wine + a headless MT5 terminal + noVNC, and add this FastAPI bridge on top.
#
# Auto-login: the base image logs the terminal into MT5_LOGIN / MT5_PASSWORD /
# MT5_SERVER (Phase 1: from .env; Phase 2: injected from the DB/UI at runtime).
#
# ⚠️ The Wine/MT5 layer is environment-sensitive. This builds the right shape;
# the `wine python` invocation and terminal startup may need tuning the first
# time you run it on a host. Validate on a DEMO account first
# (CLAUDE.md: paper before real money).
FROM gmag11/metatrader5_vnc:latest

WORKDIR /bridge

# Install the bridge's deps into the image's Wine Python — the interpreter that
# can `import MetaTrader5`. (Base image exposes it as `wine python`.)
COPY requirements.txt ./
RUN wine python -m pip install --no-cache-dir -r requirements.txt

COPY app.py ./

# 8800 = bridge HTTP API (the main API's ExnessBroker calls this).
# 3000 = noVNC, to watch / hand-log-in to the terminal if auto-login needs it.
EXPOSE 8800 3000

# Serve the bridge through Wine Python so MetaTrader5 imports against the
# terminal the base image started.
CMD ["wine", "python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8800"]
