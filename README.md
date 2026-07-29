# AI Forex Autopilot

Autonomous forex/gold trading bot with:

- AI market analysis (OpenAI-compatible APIs) + heuristic fallback
- Hard risk manager (position size, daily loss, drawdown, cooldowns)
- **Exness via MetaTrader 5** (recommended)
- Paper broker (default safe mode)
- Optional OANDA client
- SQLite trade/event log
- Telegram alerts
- Simple backtester

> **Risk warning:** Trading forex can lose money fast. Default mode is **paper**. This is software, not financial advice.

## Quick start

```bash
cd forex-autopilot
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env

python main.py run --once
python main.py run
python main.py backtest --bars 500 --instrument EUR_USD
python main.py status
```

## Brokers

| BROKER | Use case |
|--------|----------|
| `paper` | Local simulation (default) |
| `mt5` | **Exness** (or any MT5 broker) |
| `oanda` | OANDA (blocked in some countries incl. BR) |

| TRADING_MODE | Meaning |
|--------------|---------|
| `paper` | Simulated fills |
| `practice` | Demo account |
| `live` | Real money |

## Exness + MT5 setup (your path)

### 1. Install & login
1. Install **MetaTrader 5** from Exness
2. Log into your **Exness demo** account inside MT5
3. Note from MT5 / Exness cabinet:
   - **Login** (number)
   - **Password** (trading password)
   - **Server** (e.g. `Exness-MT5Trial8`, `Exness-MT5Real`, etc.)

### 2. Enable algo trading
In MT5:
- Click **Algo Trading** button in the toolbar (must be green/on)
- `Tools` → `Options` → `Expert Advisors`
  - ✅ Allow algorithmic trading
  - ✅ Allow WebRequest for listed URL (optional)

### 3. Check symbol names
Exness often uses suffixes like `m`:
- `EURUSDm`, `GBPUSDm`, `XAUUSDm`

In Market Watch, right-click → show all, find exact names.

### 4. Configure `.env`

```env
TRADING_MODE=practice
BROKER=mt5

MT5_LOGIN=123456789
MT5_PASSWORD=your_mt5_password
MT5_SERVER=Exness-MT5Trial8
# Optional if needed:
# MT5_PATH=C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe
```

### 5. Test connection

```bash
python main.py mt5-test
```

You should see balance/equity and resolved symbols/prices.

### 6. Run on demo

```bash
python main.py run --once
python main.py run
```

Only switch to live when demo results look acceptable:

```env
TRADING_MODE=live
BROKER=mt5
MT5_SERVER=Exness-MT5Real   # your real server name
```

## AI setup (optional)

Without an API key, the bot uses a technical heuristic.

```env
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4o-mini
```

xAI:

```env
OPENAI_API_KEY=...
OPENAI_BASE_URL=https://api.x.ai/v1
OPENAI_MODEL=grok-2-latest
```

## Risk rails (defaults)

- 0.75% equity risk per trade
- 3% daily loss kill switch
- 8% max drawdown halt
- Max 2 open trades / 8 per day
- Min 1.5 R:R
- Cooldown after 3 losses
- London + New York sessions

## Project layout

```
forex-autopilot/
  main.py
  app/
    analysis/      # indicators + AI
    broker/        # paper + mt5 + oanda
    risk/
    execution/
    backtest/
    alerts/
    data/
```

## Recommended path

1. Paper mode locally
2. `mt5-test` against **Exness demo**
3. Run bot on demo for days/weeks
4. Tiny live size only after that

## Notes / gotchas

- MT5 Python package works on **Windows** with a local terminal running/installed
- Keep MT5 open (or at least installed; `initialize` can launch it)
- Symbol names differ by account type — use `MT5_SYMBOL_MAP`
- If orders reject: check Algo Trading ON, market open, correct filling mode, sufficient margin
- Never commit real `.env` credentials
