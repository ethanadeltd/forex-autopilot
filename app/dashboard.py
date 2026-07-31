from __future__ import annotations

import threading
import uuid
from typing import Any, Optional

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

from app.broker.factory import make_broker
from app.config import get_settings
from app.data.store import Store
from app.runtime import is_running, pid, request_stop, wait_for_stop
from app.settings_manager import TraderSettings, load_settings, save_settings, settings_schema
from app.strategies.registry import list_strategies
from app.strategy_state import get_selected_strategy_id, set_selected_strategy_id

app = FastAPI(title="AI Forex Autopilot Monitor")

# Background backtest result cache
_bt_results: dict[str, dict[str, Any]] = {}
_bt_lock = threading.Lock()


def _page() -> str:
    settings = get_settings()
    store = Store(settings.db_path)
    selected = get_selected_strategy_id(settings.strategy_id)
    presets = list_strategies()
    running = is_running()
    bot_pid = pid()

    account = None
    err = ""
    try:
        broker = make_broker(settings)
        if settings.trading_mode == "paper" and hasattr(broker, "_open"):
            for t in store.open_trades():
                broker._open[t.id] = t
        account = broker.get_account()
        if hasattr(broker, "shutdown"):
            broker.shutdown()
    except Exception as exc:
        err = str(exc)

    ts = load_settings()
    opens = store.open_trades()
    events = store.recent_events(100)
    closed = [t for t in store.list_trades() if t.status.value == "closed"][-20:]
    selected_meta = next((p for p in presets if p["id"] == selected), presets[0])

    # Status badge
    status_badge = (
        f'<span class="badge-running">RUNNING (PID {bot_pid})</span>'
        if running
        else '<span class="badge-stopped">STOPPED</span>'
    )
    stop_btn = (
        '<form method="post" action="/stop" style="display:inline"><button class="danger">STOP BOT</button></form>'
        if running
        else ""
    )

    def rows_trades(items):
        if not items:
            return "<tr><td colspan='9'>None</td></tr>"
        out = []
        for t in items:
            opened = t.opened_at.strftime('%m-%d %H:%M') if hasattr(t, 'opened_at') and t.opened_at else '-'
            closed = t.closed_at.strftime('%m-%d %H:%M') if hasattr(t, 'closed_at') and t.closed_at else '-'
            pnl = getattr(t, 'pnl', 0)
            pnl_style = 'color:#7dffa6' if pnl > 0 else ('color:#ff8e8e' if pnl < 0 else '')
            out.append(
                "<tr>"
                f"<td>{t.instrument}</td>"
                f"<td>{t.side.value}</td>"
                f"<td>{t.entry_price}</td>"
                f"<td>{t.stop_loss}</td>"
                f"<td>{t.take_profit}</td>"
                f"<td style='{pnl_style}'>{pnl:.2f}</td>"
                f"<td>{opened}</td>"
                f"<td>{closed}</td>"
                "</tr>"
            )
        return "".join(out)

    # Extract AI insights from recent events
    ai_insights = []
    has_ai_key = bool(settings.openai_api_key)
    for e in events:
        msg = e.message or ""
        if "AI agrees" in msg or "AI caution" in msg:
            ai_insights.append(e)
            if len(ai_insights) >= 6:
                break

    def ai_insights_html(items):
        if not items:
            if has_ai_key:
                return '<p class="muted">🤖 AI is configured — waiting for the next trading decision to see its opinion.</p>'
            else:
                return '<p class="muted">🤖 No AI API key configured. <a href="#tab-settings" onclick="switchTab(\'settings\')">Add one in Settings</a> to get AI second opinions.</p>'
        out = []
        for e in items:
            msg = e.message or ""
            ts = e.ts.isoformat(timespec='seconds') if hasattr(e, 'ts') else ''
            if "AI agrees" in msg:
                icon = "✅"
            elif "AI caution" in msg:
                icon = "⚠️"
            else:
                icon = "🤖"
            out.append(f'<div style="margin-bottom:8px;padding:8px 10px;background:#0f1726;border-radius:8px;border-left:3px solid {"#7dffa6" if icon == "✅" else "#ff8e8e"}">'
                f'<span style="font-size:13px">{icon} <b>{ts}</b> — {msg[:120]}</span>'
                f'</div>')
        return "".join(out)

    def rows_events(items):
        if not items:
            return "<tr><td colspan='4'>None</td></tr>"
        out = []
        for e in items:
            details = ""
            if e.data:
                parts = []
                for k, v in e.data.items():
                    if k == "meta":
                        continue
                    if isinstance(v, dict):
                        for mk, mv in v.items():
                            parts.append(f"{mk}={mv}")
                    else:
                        parts.append(f"{k}={v}")
                if parts:
                    details = " | ".join(parts)
            out.append(
                "<tr>"
                f"<td>{e.ts.isoformat(timespec='seconds')}</td>"
                f"<td>{e.level}</td>"
                f"<td>{e.message}</td>"
                f"<td style='font-size:11px;color:#9db0d0;max-width:300px;word-break:break-word'>{details}</td>"
                "</tr>"
            )
        return "".join(out)

    options = []
    for p in presets:
        sel = "selected" if p["id"] == selected else ""
        options.append(f'<option value="{p["id"]}" {sel}>{p["name"]}</option>')

    equity = f"{account.equity:.2f}" if account else "-"
    balance = f"{account.balance:.2f}" if account else "-"
    open_n = account.open_trades if account else len(opens)

    # Settings form fields
    schema = settings_schema()
    fields_html = ""
    current_instruments = [x.strip() for x in ts.instruments.split(",") if x.strip()]
    all_instruments = ["EUR_USD", "GBP_USD", "XAU_USD"]
    for f in schema:
        key = f["key"]
        default = f["default"]
        val = getattr(ts, key, default)
        lbl = f["label"]
        
        # Special case: instruments as checkboxes
        if key == "instruments":
            cb = "".join(
                f'<label style="display:inline-flex;align-items:center;gap:6px;margin-right:16px;cursor:pointer">'
                f'<input type="checkbox" name="instr_{inst}" value="1" {"checked" if inst in current_instruments else ""} '
                f'style="width:18px;height:18px;cursor:pointer"> {inst}'
                f'</label>'
                for inst in all_instruments
            )
            fields_html += f'<div class="field"><label>Instruments (uncheck to disable)</label><div>{cb}</div></div>'
        elif f["type"] == "select":
            opts = "".join(
                f'<option value="{o["value"]}" {"selected" if o["value"] == str(val) else ""}>{o["label"]}</option>'
                for o in f["options"]
            )
            fields_html += f'<div class="field"><label>{lbl}</label><select name="{key}">{opts}</select></div>'
        elif f["type"] == "float":
            step = f.get("step", 0.1)
            fields_html += (
                f'<div class="field"><label>{lbl}</label>'
                f'<input type="number" name="{key}" value="{val}" min="{f["min"]}" max="{f["max"]}" step="{step}" />'
                f'</div>'
            )
        elif f["type"] == "int":
            fields_html += (
                f'<div class="field"><label>{lbl}</label>'
                f'<input type="number" name="{key}" value="{val}" min="{f["min"]}" max="{f["max"]}" step="1" />'
                f'</div>'
            )
        else:
            hint = f.get("hint", "")
            hint_html = f'<span class="hint">{hint}</span>' if hint else ""
            fields_html += (
                f'<div class="field"><label>{lbl}</label>'
                f'<input type="text" name="{key}" value="{val}" />{hint_html}'
                f'</div>'
            )

    # AI status check (dashboard-side)
    ai_status = "<span class='badge-stopped' id='ai-status-badge'>checking...</span>"

    return f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta http-equiv="refresh" content="15" />
  <title>Forex Autopilot</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ font-family: ui-sans-serif, system-ui, sans-serif; margin: 24px; background:#0b1220; color:#e8eefc; }}
    h1,h2,h3 {{ margin: 0 0 12px; }}
    a {{ color:#8cbcff; }}
    .card {{ background:#121a2b; border:1px solid #243251; border-radius:12px; padding:16px; margin-bottom:16px; }}
    .grid {{ display:grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap:12px; }}
    .metric {{ background:#0f1726; border-radius:10px; padding:12px; }}
    .label {{ color:#9db0d0; font-size:12px; }}
    .value {{ font-size:22px; font-weight:700; margin-top:4px; }}
    table {{ width:100%; border-collapse: collapse; }}
    th, td {{ text-align:left; padding:8px 6px; border-bottom:1px solid #243251; font-size:14px; }}
    th {{ color:#9db0d0; font-weight:600; }}
    .err {{ color:#ff8e8e; }}
    .ok {{ color:#7dffa6; }}
    .badge-running {{ background:#173a1e; color:#7dffa6; padding:6px 14px; border-radius:20px; font-weight:600; font-size:14px; }}
    .badge-stopped {{ background:#3a1717; color:#ff8e8e; padding:6px 14px; border-radius:20px; font-weight:600; font-size:14px; }}
    select, input, button {{ background:#0f1726; color:#e8eefc; border:1px solid #3a4d73; border-radius:8px; padding:10px 12px; font-size:14px; }}
    button {{ cursor:pointer; background:#1d4ed8; border-color:#1d4ed8; font-weight:600; }}
    .danger {{ background:#b91c1c; border-color:#b91c1c; }}
    .row {{ display:flex; gap:12px; align-items:center; flex-wrap:wrap; }}
    .muted {{ color:#9db0d0; }}
    .tabs {{ display:flex; gap:4px; margin-bottom:16px; }}
    .tab {{ padding:10px 18px; border-radius:8px 8px 0 0; cursor:pointer; background:#0f1726; border:1px solid #243251; border-bottom:none; }}
    .tab.active {{ background:#121a2b; font-weight:600; border-color:#3a4d73; }}
    .tab-content {{ display:none; }}
    .tab-content.show {{ display:block; }}
    .settings-grid {{ display:grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap:12px; }}
    .field {{ margin-bottom:10px; }}
    .field label {{ display:block; font-size:13px; color:#9db0d0; margin-bottom:4px; }}
    .field input, .field select {{ width:100%; }}
    .hint {{ color:#6b80a0; font-size:11px; margin-left:6px; }}
    pre {{ background:#0a0f1a; padding:12px; border-radius:8px; overflow-x:auto; font-size:12px; }}
  </style>
  <script>
  function switchTab(name) {{
    document.querySelectorAll('.tab-content').forEach(e => e.classList.remove('show'));
    document.querySelectorAll('.tab').forEach(e => e.classList.remove('active'));
    document.getElementById('tab-' + name).classList.add('show');
    document.querySelector(`[data-tab="${{name}}"]`).classList.add('active');
    localStorage.setItem('autopilot-tab', name);
  }}
  function checkAI() {{
    fetch('/api/test-ai').then(r => r.json()).then(d => {{
      const badge = document.getElementById('ai-status-badge');
      if (d.ok) {{
        badge.className = 'badge-running';
        badge.textContent = 'AI: ' + (d.model || 'OK') + ' ✅';
      }} else {{
        badge.className = 'badge-stopped';
        badge.textContent = 'AI: ' + (d.error || 'FAIL') + ' ❌';
      }}
    }}).catch(() => {{
      const badge = document.getElementById('ai-status-badge');
      badge.className = 'badge-stopped';
      badge.textContent = 'AI: UNREACHABLE ❌';
    }});
  }}
  document.addEventListener('DOMContentLoaded', () => {{
    const saved = localStorage.getItem('autopilot-tab');
    if (saved) switchTab(saved);
    checkAI();
    setInterval(checkAI, 30000);
  }});
  </script>
</head>
<body>

<div class="row" style="justify-content:space-between; margin-bottom:16px;">
  <div>
    <h1 style="display:inline">AI Forex Autopilot</h1>
    <span style="margin-left:12px;">{status_badge} {stop_btn} {ai_status}</span>
    <p class="muted" style="margin-top:4px">Broker: <b>{settings.broker}</b> · Mode: <b>{settings.trading_mode}</b> · Auto-refresh 15s</p>
    {"<p class='err'>Broker error: "+err+"</p>" if err else ""}
  </div>
</div>

<div class="tabs">
  <div class="tab active" data-tab="overview" onclick="switchTab('overview')">Overview</div>
  <div class="tab" data-tab="strategy" data-tab="strategy" onclick="switchTab('strategy')">Strategy</div>
  <div class="tab" data-tab="settings" data-tab="settings" onclick="switchTab('settings')">Settings</div>
  <div class="tab" data-tab="log" data-tab="log" onclick="switchTab('log')">Log</div>
  <div class="tab" data-tab="backtest" data-tab="backtest" onclick="switchTab('backtest')">Backtest</div>
</div>

<!-- OVERVIEW -->
<div id="tab-overview" class="tab-content show">
  <div class="card grid">
    <div class="metric"><div class="label">Equity</div><div class="value">{equity}</div></div>
    <div class="metric"><div class="label">Balance</div><div class="value">{balance}</div></div>
    <div class="metric"><div class="label">Open trades</div><div class="value">{open_n}</div></div>
    <div class="metric"><div class="label">Pairs</div><div class="value" style="font-size:14px">{settings.instruments.replace('XAU_USD','<span style="color:#ff6b35">XAU_USD</span>')}{'<br><span style="color:#ff6b35;font-size:12px">WARNING: XAU/USD high risk with this strategy</span>' if 'XAU_USD' in settings.instruments and selected == 'human_sr_h1_m15' else ''}</div></div>
  </div>

  <div class="card">
    <h2>Open trades</h2>
    <table>
      <tr><th>Pair</th><th>Side</th><th>Entry</th><th>SL</th><th>TP</th><th>PnL</th><th>Opened</th><th>Closed</th></tr>
      {rows_trades(opens)}
    </table>
  </div>

  <div class="card">
    <h2>Recent closed</h2>
    <table>
      <tr><th>Pair</th><th>Side</th><th>Entry</th><th>SL</th><th>TP</th><th>PnL</th><th>Opened</th><th>Closed</th></tr>
      {rows_trades(list(reversed(closed)))}
    </table>
  </div>

  <div class="card">
    <h2>🤖 AI second opinions</h2>
    <p class="muted" style="margin-bottom:8px">When the strategy wants to trade, AI reviews the chart and either agrees ✅ or cautions ⚠️. Refreshes with each tick.</p>
    {ai_insights_html(ai_insights)}
  </div>
</div>

<!-- STRATEGY -->
<div id="tab-strategy" class="tab-content">
  <div class="card">
    <h2>Strategy preset</h2>
    <form method="post" action="/set-strategy" class="row">
      <select name="strategy_id" style="min-width:300px">
        {''.join(options)}
      </select>
      <button type="submit">Apply</button>
    </form>
    <p class="muted" style="margin-top:12px"><b>{selected_meta['name']}</b><br/>{selected_meta['description']}</p>
  </div>
</div>

<!-- SETTINGS -->
<div id="tab-settings" class="tab-content">
  <div class="card">
    <h2>Trading settings</h2>
    <p class="muted">Changes take effect on next bot tick (no restart needed).</p>
    <form method="post" action="/save-settings">
      <div class="settings-grid">{fields_html}</div>
      <div style="margin-top:16px"><button type="submit" style="min-width:200px">Save settings</button>
    <button type="button" onclick="testAI()" style="min-width:150px;margin-left:10px;background:#2563eb">Test AI connection</button>
    <span id="ai-test-result" style="margin-left:12px;font-size:14px"></span></div>
  <script>
  function testAI() {{
    const el = document.getElementById('ai-test-result');
    el.textContent = 'Testing...';
    el.style.color = '#9db0d0';
    fetch('/api/test-ai').then(r => r.json()).then(d => {{
      if (d.ok) {{
        el.textContent = '✅ Connected (' + d.model + ')';
        el.style.color = '#7dffa6';
      }} else {{
        el.textContent = '❌ ' + (d.error || 'Failed');
        el.style.color = '#ff8e8e';
      }}
    }}).catch(e => {{
      el.textContent = '❌ Request failed';
      el.style.color = '#ff8e8e';
    }});
  }}
  </script>
    </form>
  </div>
</div>

<!-- LOG -->
<div id="tab-log" class="tab-content">
  <div class="card">
    <h2>Event log</h2>
    <table>
      <tr><th>Time</th><th>Level</th><th>Message</th><th>Details</th></tr>
      {rows_events(events)}
    </table>
  </div>
</div>

<!-- BACKTEST -->
<div id="tab-backtest" class="tab-content">
  <div class="card">
    <h2>Backtest strategy</h2>
    <p class="muted">Uses real MT5 historical data + current strategy. Runs for a few seconds.</p>
    <p class="muted">Uses real MT5 historical data + current human_sr_h1_m15 strategy.</p>
    <div class="row" style="margin-bottom:12px;gap:8px;flex-wrap:wrap">
      <label style="display:inline-flex;align-items:center;gap:6px;cursor:pointer" title="Calls the AI for each signal (costs API tokens). Applies the same confidence filter as live trading.">
        <input type="checkbox" id="bt-ai" style="width:16px;height:16px;cursor:pointer"> 🤖 Use AI second opinions (confidence filter)</label>
      <select id="bt-instrument" style="min-width:140px">
        <option value="EUR_USD">EUR/USD</option>
        <option value="GBP_USD">GBP/USD</option>
        <option value="XAU_USD">XAU/USD (Gold)</option>
      </select>
      <select id="bt-months" style="min-width:100px">
        <option value="1">1 month</option>
        <option value="3" selected>3 months</option>
        <option value="6">6 months</option>
        <option value="12">12 months</option>
      </select>
      <input type="number" id="bt-balance" value="10000" min="100" max="1000000" step="1000" style="width:130px" title="Starting balance" />
      <span class="muted" style="font-size:13px">starting $</span>
      <button onclick="runBacktest()" style="min-width:160px">Run Backtest</button>
    </div>
    <div id="bt-loading" style="display:none;color:#9db0d0;margin-bottom:12px">⏳ Running backtest (this takes a few seconds)...</div>
    <div id="bt-results" style="display:none"></div>
  </div>
</div>

<script>
function runBacktest() {{
  const inst = document.getElementById('bt-instrument').value;
  const months = document.getElementById('bt-months').value;
  const balance = document.getElementById('bt-balance').value || 10000;
  const useAi = document.getElementById('bt-ai').checked ? 1 : 0;
  const resultsDiv = document.getElementById('bt-results');
  const loading = document.getElementById('bt-loading');
  resultsDiv.style.display = 'none';
  loading.style.display = 'block';
  loading.textContent = '⏳ Starting backtest task...';
  fetch('/api/backtest?instrument=' + inst + '&months=' + months + '&starting_equity=' + balance + '&ai=' + useAi)
    .then(r => r.json())
    .then(d => {{
      if (!d.task_id) {{
        loading.style.display = 'none';
        resultsDiv.innerHTML = '<p class="err">Error: ' + (d.error || 'No task ID') + '</p>';
        resultsDiv.style.display = 'block';
        return;
      }}
      pollBacktest(d.task_id, null, (useAi ? '🤖 AI backtest' : months + ' month(s) backtest'));
    }})
    .catch(e => {{
      loading.style.display = 'none';
      resultsDiv.innerHTML = '<p class="err">Request failed: ' + e + '</p>';
      resultsDiv.style.display = 'block';
    }});
}}

function pollBacktest(taskId, startTime, label) {{
  if (!startTime) startTime = Date.now();
  const resultsDiv = document.getElementById('bt-results');
  const loading = document.getElementById('bt-loading');
  const elapsed = Math.floor((Date.now() - startTime) / 1000);
  const dots = '.'.repeat(Math.min(Math.floor(elapsed / 2) % 6 + 1, 5));
  loading.textContent = '⏳ ' + (label || 'Running backtest') + dots + ' (' + elapsed + 's)';
  fetch('/api/backtest-result/' + taskId)
    .then(r => r.json())
    .then(d => {{
      if (d.status === 'running') {{
        setTimeout(() => pollBacktest(taskId, startTime, label), 2000);
        return;
      }}
      loading.style.display = 'none';
      if (!d.ok) {{
        resultsDiv.innerHTML = '<p class="err">Error: ' + (d.error || 'Unknown') + '</p>';
        resultsDiv.style.display = 'block';
        return;
      }}
      const pnlStyle = d.pnl >= 0 ? 'color:#7dffa6' : 'color:#ff8e8e';
      resultsDiv.innerHTML = `
        <table>
          <tr><th>Metric</th><th>Value</th></tr>
          <tr><td>Instrument</td><td>${{d.instrument}}</td></tr>
          <tr><td>Period</td><td>${{d.months}} months</td></tr>
          <tr><td>Data source</td><td>${{d.data_source}}</td></tr>
          <tr><td>AI used</td><td>${{d.ai_used ? 'Yes' : 'No (strategy-only)'}}</td></tr>
          <tr><td>Starting balance</td><td>$${{Number(d.starting_equity).toLocaleString()}}</td></tr>
          <tr><td>Ending equity</td><td>$${{Number(d.ending_equity).toLocaleString()}}</td></tr>
          <tr><td>Total return</td><td style=${{pnlStyle}}>${{d.return_pct >= 0 ? '+' : ''}}${{d.return_pct}}%</td></tr>
          <tr><td>Net PnL</td><td style=${{pnlStyle}}>${{d.pnl >= 0 ? '+' : ''}}$${{d.pnl.toLocaleString()}}</td></tr>
          <tr><td>Total trades</td><td>${{d.trades}}</td></tr>
          <tr><td>Wins</td><td style="color:#7dffa6">${{d.wins}}</td></tr>
          <tr><td>Losses</td><td style="color:#ff8e8e">${{d.losses}}</td></tr>
          <tr><td>Win rate</td><td>${{d.win_rate}}%</td></tr>
          <tr><td>Profit factor</td><td>${{d.profit_factor}}</td></tr>
          <tr><td>Sharpe ratio</td><td>${{d.sharpe}}</td></tr>
          <tr><td>Max drawdown</td><td style="color:#ff8e8e">${{d.max_drawdown_pct}}%</td></tr>
          <tr><td>Avg win</td><td style="color:#7dffa6">$${{d.avg_win}}</td></tr>
          <tr><td>Avg loss</td><td style="color:#ff8e8e">-$${{Math.abs(d.avg_loss)}}</td></tr>
          <tr><td>Max win streak</td><td>${{d.max_win_streak}}</td></tr>
          <tr><td>Max loss streak</td><td>${{d.max_loss_streak}}</td></tr>
          <tr><td colspan="2" class="muted" style="font-size:12px;padding-top:12px">${{d.notes || ''}}</td></tr>
        </table>
      `;
      resultsDiv.style.display = 'block';
    }})
    .catch(e => {{
      loading.style.display = 'none';
      resultsDiv.innerHTML = '<p class="err">Polling failed: ' + e + '</p>';
      resultsDiv.style.display = 'block';
    }});
}}
</script>

</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return _page()


@app.post("/stop")
def stop_bot():
    request_stop()
    return RedirectResponse(url="/", status_code=303)


@app.post("/set-strategy")
def set_strategy(strategy_id: str = Form(...)):
    set_selected_strategy_id(strategy_id)
    return RedirectResponse(url="/", status_code=303)


@app.post("/save-settings")
def save_settings_endpoint(
    position_sizing_mode: str = Form("risk_pct"),
    fixed_lot_volume: float = Form(0.01),
    risk_per_trade_pct: float = Form(0.75),
    daily_loss_limit_pct: float = Form(3.0),
    max_drawdown_pct: float = Form(8.0),
    max_open_trades: int = Form(2),
    max_trades_per_day: int = Form(8),
    min_rr_ratio: float = Form(1.5),
    cooldown_losses: int = Form(3),
    cooldown_minutes: int = Form(60),
    stop_loss_pips: float = Form(0.0),
    take_profit_pips: float = Form(0.0),
    trading_sessions: str = Form("london,newyork"),
    # instruments from checkboxes
    instr_EUR_USD: str = Form("0"),
    instr_GBP_USD: str = Form("0"),
    instr_XAU_USD: str = Form("0"),
    loop_seconds: int = Form(60),
    # AI settings
    ai_provider: str = Form("openai"),
    openai_api_key: str = Form(""),
    openai_base_url: str = Form("https://api.deepseek.com/v1"),
    openai_model: str = Form("deepseek-chat"),
):
    # Build instruments string from checkboxes
    checked = []
    for inst, val in [("EUR_USD", instr_EUR_USD), ("GBP_USD", instr_GBP_USD), ("XAU_USD", instr_XAU_USD)]:
        if val == "1":
            checked.append(inst)
    instruments_str = ",".join(checked) if checked else "EUR_USD,GBP_USD"

    s = TraderSettings(
        position_sizing_mode=position_sizing_mode,
        fixed_lot_volume=fixed_lot_volume,
        risk_per_trade_pct=risk_per_trade_pct,
        daily_loss_limit_pct=daily_loss_limit_pct,
        max_drawdown_pct=max_drawdown_pct,
        max_open_trades=max_open_trades,
        max_trades_per_day=max_trades_per_day,
        min_rr_ratio=min_rr_ratio,
        cooldown_losses=cooldown_losses,
        cooldown_minutes=cooldown_minutes,
        stop_loss_pips=stop_loss_pips,
        take_profit_pips=take_profit_pips,
        trading_sessions=trading_sessions,
        instruments=instruments_str,
        loop_seconds=loop_seconds,
        ai_provider=ai_provider,
        openai_api_key=openai_api_key,
        openai_base_url=openai_base_url,
        openai_model=openai_model,
    )
    save_settings(s)
    return RedirectResponse(url="/#tab-settings", status_code=303)


@app.get("/api/status")
def api_status():
    settings = get_settings()
    store = Store(settings.db_path)
    selected = get_selected_strategy_id(settings.strategy_id)
    running = is_running()
    try:
        broker = make_broker(settings)
        if settings.trading_mode == "paper" and hasattr(broker, "_open"):
            for t in store.open_trades():
                broker._open[t.id] = t
        account = broker.get_account().model_dump()
        if hasattr(broker, "shutdown"):
            broker.shutdown()
    except Exception as exc:
        account = {"error": str(exc)}
    return {
        "running": running,
        "pid": pid(),
        "account": account,
        "strategy_id": selected,
        "strategies": list_strategies(),
        "settings": load_settings().model_dump(),
        "open_trades": [t.model_dump(mode="json") for t in store.open_trades()],
        "events": [e.model_dump(mode="json") for e in store.recent_events(50)],
    }


@app.get("/api/backtest")
def run_backtest_api(instrument: str = "EUR_USD", months: int = 3, starting_equity: float = 10000.0, ai: int = 0):
    """Start backtest in background thread, return task ID immediately."""
    task_id = uuid.uuid4().hex[:12]
    use_ai = ai == 1
    
    with _bt_lock:
        _bt_results[task_id] = {"status": "running", "progress": 0}
    
    def _run():
        try:
            from app.backtest.strategy_bt import run_strategy_backtest
            
            settings = get_settings()
            original = settings.starting_equity
            settings.starting_equity = starting_equity
            try:
                result = run_strategy_backtest(settings, instrument=instrument, months=months, use_ai=use_ai)
            finally:
                settings.starting_equity = original
            
            with _bt_lock:
                _bt_results[task_id] = {
                    "status": "done",
                    "ok": True,
                    "instrument": instrument,
                    "months": months,
                    "starting_equity": starting_equity,
                    "trades": result["total_trades"],
                    "wins": result["wins"],
                    "losses": result["losses"],
                    "win_rate": result["win_rate_pct"],
                    "pnl": round(result["total_pnl"], 2),
                    "ending_equity": result["ending_equity"],
                    "max_drawdown_pct": result["max_drawdown_pct"],
                    "profit_factor": result["profit_factor"],
                    "sharpe": result["sharpe_annual"],
                    "return_pct": result["return_pct"],
                    "avg_win": result["avg_win"],
                    "avg_loss": result["avg_loss"],
                    "max_win_streak": result["max_win_streak"],
                    "max_loss_streak": result["max_loss_streak"],
                    "data_source": "MT5 real history",
                    "ai_used": use_ai,
                    "notes": "Strategy-based backtest (human_sr_h1_m15)" + (" + AI second opinions" if use_ai else ". No AI API calls."),
                }
        except Exception as exc:
            import traceback
            with _bt_lock:
                _bt_results[task_id] = {"status": "done", "ok": False, "error": str(exc), "traceback": traceback.format_exc()}
    
    threading.Thread(target=_run, daemon=True).start()
    return {"task_id": task_id, "status": "started"}


@app.get("/api/backtest-result/{task_id}")
def get_backtest_result(task_id: str):
    """Poll for backtest result."""
    with _bt_lock:
        result = _bt_results.get(task_id)
    if result is None:
        return {"status": "not_found"}
    if result["status"] == "running":
        return {"status": "running"}
    return result


@app.get("/api/test-ai")
def test_ai():
    """Test the AI API connection with a simple ping."""
    ts = load_settings()
    if not ts.openai_api_key:
        return {"ok": False, "error": "No API key configured"}
    try:
        import httpx
        import json
        headers = {
            "Authorization": f"Bearer {ts.openai_api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": ts.openai_model or "deepseek-chat",
            "temperature": 0.1,
            "messages": [
                {"role": "user", "content": "Reply with only the word OK."}
            ],
            "max_tokens": 10,
        }
        with httpx.Client(base_url=ts.openai_base_url, timeout=15.0) as client:
            resp = client.post("/chat/completions", headers=headers, json=body)
            resp.raise_for_status()
            reply = resp.json()["choices"][0]["message"]["content"].strip()
            return {
                "ok": True,
                "model": ts.openai_model,
                "provider": ts.openai_base_url,
                "reply": reply,
            }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.get("/health")
def health():
    return {"ok": True}
