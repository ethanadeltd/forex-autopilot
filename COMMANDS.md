╔══════════════════════════════════════════════════════════════╗
║           AI FOREX AUTOPILOT — COMMAND CHEAT SHEET          ║
║              Project: C:\...\forex-autopilot                ║
╚══════════════════════════════════════════════════════════════╝

============================================================
 FIRST TIME SETUP
============================================================

  1. Open PowerShell as Administrator
  2. cd C:\Users\Alex\.openclaw\workspace\agents\forge\forex-autopilot
  3. .venv\Scripts\activate            (activate Python env)
  4. pip install -r requirements.txt   (install deps)
  5. pip install python-multipart      (needed for dashboard forms)

============================================================
 START THE BOT (LIVE TRADING LOOP)
============================================================

  cd C:\Users\Alex\.openclaw\workspace\agents\forge\forex-autopilot
  .venv\Scripts\activate

  python main.py run                   # start trading loop (keeps running)
  python main.py run --once            # single tick (test)

  The bot runs until you stop it.

============================================================
 STOP THE BOT
============================================================

  Dashboard:   http://127.0.0.1:8787     → click STOP BOT
  CLI:         Create file data\bot.stop  (or delete data\bot.pid)

============================================================
 DASHBOARD (WEB UI)
============================================================

  FIRST, start the dashboard server:
    python main.py dashboard

  THEN open your browser → http://127.0.0.1:8787

  From here you can:
    - Select strategy (dropdown — see description on Strategy tab)
    - Change instruments (checkboxes: EUR, GBP, Gold — uncheck to disable)
    - Change risk settings
    - View open trades with timestamps
    - Stop the bot
    - See event log

  TIP: Keep this PowerShell window open. Open a SECOND one for the bot.

============================================================
 BACKTEST THE STRATEGY
============================================================

  6 months on EUR/USD (MTF: M15 + H1 + H4 + Daily):
    python main.py strategy-bt --instrument EUR_USD --months 6

  6 months on GBP/USD:
    python main.py strategy-bt --instrument GBP_USD --months 6

  6 months on XAU/USD (Gold) — WARNING: high risk:
    python main.py strategy-bt --instrument XAU_USD --months 6

  Shorter test (quick):
    python main.py strategy-bt --instrument EUR_USD --months 1

============================================================
 CHECK STATUS / ACCOUNT
============================================================

  python main.py status                # show equity, open trades
  python main.py mt5-test              # test Exness MT5 connection

============================================================
 THE STRATEGY — HOW IT WORKS
============================================================

  Name: Human S/R (Daily bias + 4H structure + H1 levels + M15 entry)

  ┌─────────┬──────────────────────────────────────┐
  │ Layer   │ Role                                 │
  ├─────────┼──────────────────────────────────────┤
  │ Daily   │ MACRO BIAS FILTER — never trade      │
  │         │ against the daily trend              │
  ├─────────┼──────────────────────────────────────┤
  │ 4H      │ MEDIUM STRUCTURE — confirms trend,   │
  │         │ major swing support/resistance       │
  ├─────────┼──────────────────────────────────────┤
  │ H1      │ KEY LEVELS — entry S/R zones from    │
  │         │ swing highs and lows                 │
  ├─────────┼──────────────────────────────────────┤
  │ M15     │ EXECUTION — wait for candle reaction │
  │         │ at H1 level, then enter              │
  └─────────┴──────────────────────────────────────┘

  BEST FOR:  EUR/USD ✅  |  GBP/USD ✅
  HIGH RISK: XAU/USD ⚠️  (PF=1.02, DD=115% in backtest)

============================================================
 CONFIGURATION (.env file)
============================================================

  Key settings in .env (edit with Notepad):
    TRADING_MODE     = paper | practice | live
    BROKER            = paper | mt5
    INSTRUMENTS       = EUR_USD,GBP_USD,XAU_USD
    STRATEGY_ID       = human_sr_h1_m15
    RISK_PER_TRADE_PCT = 0.75

============================================================
 QUICK START (FROM SCRATCH)
============================================================

  1. Open PowerShell (Window 1)
  2. cd C:\Users\Alex\.openclaw\workspace\agents\forge\forex-autopilot
  3. .venv\Scripts\activate
  4. python main.py dashboard           (LAUNCH dashboard web UI)
  5. Open http://127.0.0.1:8787 in browser
  6. Open PowerShell (Window 2)
  7. cd C:\Users\Alex\.openclaw\workspace\agents\forge\forex-autopilot
  8. .venv\Scripts\activate
  9. python main.py run --once          (test one tick first)
  10. python main.py run                (start trading loop)

  NOTE: Keep both PowerShell windows open!
  - Window 1 = dashboard (see trades, settings)
  - Window 2 = trading bot (keep running)

============================================================
