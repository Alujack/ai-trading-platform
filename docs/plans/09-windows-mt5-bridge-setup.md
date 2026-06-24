# Plan 09 — Windows setup: MT5 bridge for Exness (DEMO first)

**Read me on your Windows machine.** This is the step-by-step to clone the project, run the **MT5 bridge** (`services/mt5bridge/`) against an **Exness demo** account, and connect it back to the main system. It implements Phase 1 of [08-mt5-exness-integration.md](08-mt5-exness-integration.md).

> ⚠️ **DEMO ONLY** for now. Going to a real account is gated — see [08 §9](08-mt5-exness-integration.md). Do not put real money behind this until that gate is met.

---

## Picture of what you're building

```
┌─────────────── your main app (Mac/Linux, Docker) ───────────────┐
│  api container  ──HTTP──▶  MT5_BRIDGE_URL                         │
└──────────────────────────────────│──────────────────────────────┘
                                    │  (LAN IP or VPS public IP : 8800,  X-Bridge-Token)
┌─────────────── Windows machine / VPS ───────────────────────────┐
│  services/mt5bridge  (FastAPI, port 8800)                        │
│        │ MetaTrader5 python lib                                  │
│        ▼                                                         │
│  MT5 terminal (Exness)  ──▶  Exness demo server                  │
└──────────────────────────────────────────────────────────────────┘
```

The bridge and the MT5 terminal **must be on the same Windows machine**. The main app can be on that same machine or anywhere that can reach it over the network.

---

## Step 1 — Install the Exness MT5 terminal + create a DEMO account

1. Download **MetaTrader 5** from your Exness Personal Area (or exness.com). Install it (64-bit).
2. Open a **demo** account: in the terminal, `File ▸ Open an Account ▸ Exness ▸` choose a **Demo** account, pick USD, note the **login number**, **password**, and **server** (e.g. `Exness-MT5Trial`, sometimes with a number like `Exness-MT5Trial7`). You can also see these later in `File ▸ Login to Trade Account`.
3. **Enable automated trading:**
   - Toolbar: click **Algo Trading** so it's green/enabled.
   - `Tools ▸ Options ▸ Expert Advisors`: tick **Allow algorithmic trading**.
4. **Add the symbols** you trade to Market Watch: right-click Market Watch ▸ **Symbols** (or Show All), add **EURUSD, XAUUSD, BTCUSD**. **Write down the EXACT names** the terminal shows — some Exness account types suffix them (e.g. `EURUSDm`). If they're suffixed, you'll set `BROKER_SYMBOL_MAP` later.
5. Leave the terminal **running and logged in**. The bridge attaches to it.

---

## Step 2 — Install Python (64-bit) + clone the project

1. Install **Python 3.12, 64-bit** from python.org. On the installer, tick **“Add python.exe to PATH”**. (64-bit matters — it must match the 64-bit MT5 terminal.)
   - Verify in a new PowerShell: `python --version` and `python -c "import struct;print(struct.calcsize('P')*8)"` → should print `64`.
2. Install **Git** (git-scm.com) if you don't have it.
3. Clone the repo and go to the bridge folder:
   ```powershell
   git clone <your-repo-url> ai-trading-platform
   cd ai-trading-platform\services\mt5bridge
   ```

---

## Step 3 — Create the venv + install bridge deps

```powershell
python -m venv venv
venv\Scripts\pip install -r requirements.txt
```
`MetaTrader5` only installs on Windows — that's expected and fine here.

---

## Step 4 — Configure the bridge `.env`

```powershell
copy .env.example .env
notepad .env
```
Fill in:
- `MT5_LOGIN` / `MT5_PASSWORD` / `MT5_SERVER` — your **demo** credentials from Step 1 (server must be the **exact** string, e.g. `Exness-MT5Trial`).
- `MT5_BRIDGE_TOKEN` — a long random secret. Generate one:
  ```powershell
  powershell -Command "[guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')"
  ```
- `MT5_TERMINAL_PATH` — usually leave blank (auto-detect). If startup can't find the terminal, set it to the full path of `terminal64.exe` (e.g. `C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe`).

---

## Step 5 — Run the bridge

With the MT5 terminal open and logged in:
```powershell
venv\Scripts\python -m uvicorn app:app --host 0.0.0.0 --port 8800
```
(or just double-click / run `run.bat`). You should see `connected login=… server=… balance=…`.

---

## Step 6 — Smoke test (still on Windows)

Set your token once for the session, then hit the endpoints (PowerShell):
```powershell
$T = "PASTE_YOUR_MT5_BRIDGE_TOKEN"
$H = @{ "X-Bridge-Token" = $T }

# health + account
Invoke-RestMethod http://localhost:8800/health  -Headers $H
Invoke-RestMethod http://localhost:8800/account -Headers $H

# symbol spec (confirm the name + contract size / lot step)
Invoke-RestMethod http://localhost:8800/symbol/EURUSD -Headers $H

# OPEN a tiny 0.01-lot demo trade (LONG). Use realistic sl/tp around current price.
$order = @{ symbol="EURUSD"; side="LONG"; lots=0.01; sl=1.0500; tp=1.2000; clientTag="manual-test-1" } | ConvertTo-Json
Invoke-RestMethod http://localhost:8800/order -Method Post -Headers $H -ContentType "application/json" -Body $order

# see it
Invoke-RestMethod http://localhost:8800/positions -Headers $H

# CLOSE it (use the ticket from the open/positions response)
$close = @{ ticket = 123456789 } | ConvertTo-Json
Invoke-RestMethod http://localhost:8800/close -Method Post -Headers $H -ContentType "application/json" -Body $close
```
You should see the trade appear and disappear in the MT5 **Trade** tab too. ✅ If that works, the bridge is good.

---

## Step 7 — Connect it to the main system

On the machine running the **main app** (the Docker stack), edit the root `.env`:
```
BROKER=exness
EXNESS_ENV=demo
MT5_BRIDGE_URL=http://<WINDOWS_HOST_IP>:8800
MT5_BRIDGE_TOKEN=<the same token from Step 4>
# Only if your Exness symbols are suffixed (Step 1.4):
# BROKER_SYMBOL_MAP={"EURUSD":"EURUSDm","XAUUSD":"XAUUSDm","BTCUSD":"BTCUSDm"}
```
- If the app and Windows are the **same machine**, use `http://host.docker.internal:8800`.
- If different machines, use the Windows host's **LAN IP** (or the VPS **public IP**).
- Then restart the services that talk to the broker:
  ```bash
  docker compose up -d api worker
  ```

> Note: wiring the broker into the live order path is **Phase 4** of plan 08 (not done yet) — until then, `BROKER=exness` is read by the factory and you can verify connectivity, but the decider still uses the existing paper engine. The bridge being ready now means Phase 4 can be built and tested immediately.

### Networking / firewall
- Allow inbound TCP **8800** on the Windows firewall (`New-NetFirewallRule -DisplayName "MT5 Bridge" -Direction Inbound -LocalPort 8800 -Protocol TCP -Action Allow`).
- The token is the only auth — **do not expose port 8800 to the open internet.** Keep the Windows host and the app on the same private network/VPN, or restrict the firewall rule to the app's IP. (TLS/reverse-proxy can be added later.)

---

## Step 8 — Keep it running across reboots (recommended)

Use **NSSM** (the Non-Sucking Service Manager) so the bridge auto-starts:
```powershell
# after installing nssm
nssm install MT5Bridge "C:\path\to\ai-trading-platform\services\mt5bridge\venv\Scripts\python.exe" "-m uvicorn app:app --host 0.0.0.0 --port 8800"
nssm set MT5Bridge AppDirectory "C:\path\to\ai-trading-platform\services\mt5bridge"
nssm start MT5Bridge
```
Also set the **MT5 terminal to start on login** and keep the machine from sleeping (a VPS is ideal). The bridge auto-reconnects to the terminal, but the terminal itself must be running and logged in.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `mt5 initialize failed` | Terminal not running/logged in, or 32-vs-64-bit mismatch. Use 64-bit Python; open the terminal first; set `MT5_TERMINAL_PATH`. |
| `mt5 login failed` | Wrong `MT5_SERVER` string (must match the terminal exactly) or wrong login/password. |
| Order rejected `AutoTrading disabled` | Enable **Algo Trading** (toolbar) and `Tools ▸ Options ▸ Expert Advisors ▸ Allow algorithmic trading`. |
| `unknown symbol` (404) | Name mismatch. Check Market Watch; set `BROKER_SYMBOL_MAP`. |
| Order rejected `Invalid stops` | SL/TP too close to price or wrong side; the gate's RR≥2 levels are usually fine, but verify direction. |
| Rejected `Unsupported filling mode` | The bridge already retries IOC→FOK→RETURN; if it still fails, your symbol uses a different policy — note the retcode and we'll pin it. |
| `401 bad or missing X-Bridge-Token` | Token in the request header doesn't match `.env`. |
| App can't reach the bridge | Firewall port 8800; correct IP (`host.docker.internal` vs LAN/VPS IP). |

---

## What NOT to do
- ❌ Don't point `MT5_SERVER` at a **real** Exness server yet.
- ❌ Don't expose port 8800 publicly without the token + IP restriction.
- ❌ Don't run uvicorn with `--workers >1` — the MT5 library isn't thread-safe (the bridge serializes calls; multiple workers would each open their own terminal connection).

When demo is solid and the [08 §9](08-mt5-exness-integration.md) promotion gate is met, going live is just: `EXNESS_ENV=real`, a real `MT5_SERVER`, real creds — no code change.
