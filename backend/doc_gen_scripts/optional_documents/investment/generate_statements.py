#!/usr/bin/env python3
"""Generate Moomoo-style monthly securities statements with varied clients and holdings."""

from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import tempfile
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


CSS_PATH = Path(__file__).resolve().parent / "statement.css"
CURRENCIES = ["SGD", "USD", "HKD", "CNH", "JPY", "KRW"]
MONTHS = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]
VENUES = ["EDGX", "ARCA", "NASDAQ", "NYSE", "BATS", "IEX"]

CLIENTS = [
    ("Tan Wei Ming", "18 Ardmore Park", "#15-02", "259960"),
    ("Rachel Ong", "8 Nathan Road", "#12-03", "248743"),
    ("Suresh Nair", "15 Jalan Merah Saga", "", "278103"),
    ("Amanda Lim", "31 Amber Road", "#16-02", "439941"),
    ("Jason Chew", "1 Cairnhill Road", "#18-02", "229719"),
    ("Priya Menon", "27 Namly Crescent", "", "267548"),
    ("David Koh", "18 Meyer Road", "#22-05", "437846"),
    ("Elaine Goh", "6 Leedon Heights", "#21-03", "267954"),
    ("Christopher Yap", "12 Nassim Road", "#06-01", "258378"),
    ("Melissa Tan", "3 Sentosa Cove Way", "#04-02", "098136"),
    ("Aaron Teo", "10 Shelford Road", "#04-02", "288353"),
    ("Nurul Hassan", "16 Eng Kong Terrace", "", "598988"),
    ("Jeanette Foo", "3 Draycott Park", "#10-01", "259382"),
    ("Tan Wei Jie", "1 Grange Road", "#23-08", "239693"),
    ("Lim Hui Min", "8 Bin Tong Park", "", "269797"),
    ("Marcus Ong", "9 Tomlinson Road", "#11-04", "247985"),
    ("Clara Ng", "1 Balmoral Road", "#14-03", "259796"),
    ("Daniel Wong", "7 Ridley Park", "", "248480"),
    ("Hannah Lee", "18 Mount Sinai Rise", "#07-02", "276906"),
    ("Benjamin Chua", "5 Belmont Road", "#08-01", "269856"),
]

SGX_STOCKS = [
    ("D05", "DBS", 32.0, 48.0),
    ("O39", "OCBC", 13.0, 18.5),
    ("U11", "UOB", 28.0, 38.0),
    ("Z74", "Singtel", 2.40, 3.60),
    ("C6L", "Singapore Airlines", 6.20, 8.40),
    ("BN4", "Keppel", 6.00, 8.80),
    ("S68", "Singapore Exchange", 9.50, 13.80),
    ("V03", "Venture Corporation", 12.0, 16.5),
    ("C07", "Jardine Cycle & Carriage", 24.0, 34.0),
    ("U96", "Sembcorp Industries", 4.20, 7.40),
    ("H78", "Hongkong Land", 5.40, 7.20),
    ("J36", "Jardine Matheson", 38.0, 52.0),
]

US_STOCKS = [
    ("AAPL", "Apple", 165.0, 240.0),
    ("MSFT", "Microsoft", 370.0, 455.0),
    ("NVDA", "NVIDIA", 95.0, 145.0),
    ("AMZN", "Amazon", 155.0, 230.0),
    ("GOOGL", "Alphabet", 140.0, 195.0),
    ("META", "Meta Platforms", 430.0, 620.0),
    ("TSLA", "Tesla", 170.0, 380.0),
    ("JPM", "JPMorgan Chase", 175.0, 250.0),
    ("V", "Visa", 250.0, 320.0),
    ("UNH", "UnitedHealth", 480.0, 590.0),
    ("WMT", "Walmart", 60.0, 95.0),
    ("HD", "Home Depot", 330.0, 420.0),
    ("COST", "Costco", 720.0, 980.0),
    ("NFLX", "Netflix", 550.0, 780.0),
    ("AMD", "Advanced Micro Devices", 95.0, 180.0),
    ("ORCL", "Oracle", 110.0, 190.0),
    ("CRM", "Salesforce", 220.0, 310.0),
    ("KO", "Coca-Cola", 58.0, 72.0),
    ("PEP", "PepsiCo", 150.0, 185.0),
    ("DIS", "Walt Disney", 85.0, 125.0),
    ("BA", "Boeing", 150.0, 230.0),
    ("CAT", "Caterpillar", 280.0, 390.0),
    ("GS", "Goldman Sachs", 390.0, 560.0),
    ("MCD", "McDonald's", 250.0, 310.0),
    ("NKE", "Nike", 70.0, 110.0),
    ("QCOM", "Qualcomm", 140.0, 210.0),
    ("AVGO", "Broadcom", 140.0, 230.0),
    ("INTU", "Intuit", 520.0, 720.0),
    ("BKNG", "Booking Holdings", 3200.0, 4800.0),
    ("PLTR", "Palantir", 18.0, 45.0),
    ("COIN", "Coinbase", 140.0, 280.0),
    ("MARA", "MARA Holdings", 9.0, 22.0),
    ("RGTI", "Rigetti Computing", 8.0, 22.0),
    ("SOFI", "SoFi Technologies", 7.0, 18.0),
    ("NIO", "NIO", 3.5, 8.5),
]


def money_cents(value: float | Decimal) -> int:
    return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def from_cents(cents: int) -> float:
    return float(Decimal(cents) / 100)


def round_money(value: float | Decimal) -> float:
    return from_cents(money_cents(value))


def convert_sgd(amount: float, fx: float) -> float:
    return round_money(Decimal(str(amount)) * Decimal(str(fx)))


@dataclass
class Holding:
    name: str
    symbol: str
    exchange: str
    currency: str
    start_qty: float
    start_px: float
    end_qty: float
    end_px: float
    buy_qty: float = 0.0
    sell_qty: float = 0.0
    transfer_in: float = 0.0
    transfer_out: float = 0.0
    buy_price: float = 0.0
    sell_price: float = 0.0

    @property
    def start_value(self) -> float:
        if self.start_qty <= 0 or self.start_px <= 0:
            return 0.0
        return round_money(Decimal(str(self.start_qty)) * Decimal(str(self.start_px)))

    @property
    def end_value(self) -> float:
        if self.end_qty <= 0 or self.end_px <= 0:
            return 0.0
        return round_money(Decimal(str(self.end_qty)) * Decimal(str(self.end_px)))

    @property
    def change(self) -> float:
        return round_money(self.end_value - self.start_value)


@dataclass
class Trade:
    direction: str
    holding: Holding
    venue: str
    when: datetime
    price: float
    qty: float
    amount: float
    fee: float
    platform: float
    settlement: float
    tax: float
    sec: float = 0.0
    taf: float = 0.0


@dataclass
class CashEvent:
    when: str
    type: str
    amount: float
    comment: str


@dataclass
class Statement:
    name: str
    address: str
    account_number: str
    account_name: str
    account_type: str
    period_end: date
    prep_date: date
    usd_start_fx: float
    usd_end_fx: float
    holdings: list[Holding]
    trades: list[Trade]
    cash_start: dict[str, float]
    cash_end: dict[str, float]
    cash_events: dict[str, list[CashEvent]]
    dividends: list[CashEvent] = field(default_factory=list)
    start_nav: float = 0.0
    end_nav: float = 0.0
    portfolio: float = 0.0
    cash_balance: float = 0.0
    start_parts: dict[str, float] = field(default_factory=dict)
    end_parts: dict[str, float] = field(default_factory=dict)
    pos_change: dict[str, float] = field(default_factory=dict)
    cash_change: dict[str, dict[str, float]] = field(default_factory=dict)


def last_day(year: int, month: int) -> date:
    return date(year, month, monthrange(year, month)[1])


def prev_month_end(value: date) -> date:
    first = value.replace(day=1)
    return first - timedelta(days=1)


def money(value: float, signed: bool = False) -> str:
    cents = money_cents(value)
    if cents == 0:
        return "0.00"
    text = f"{abs(cents) / 100:,.2f}"
    if signed:
        return f"+{text}" if cents > 0 else f"-{text}"
    return text if cents >= 0 else f"-{text}"


def price(value: float | None) -> str:
    if value is None or value <= 0:
        return "-"
    return f"{value:,.4f}"


def qty(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return f"{int(round(value))}"
    return f"{value:.4f}".rstrip("0").rstrip(".")


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def fmt_period(value: date) -> str:
    return f"{MONTHS[value.month - 1]} {value.year}"


def fmt_stamp(value: date) -> str:
    return f"{value.year}{value.month:02d}{value.day:02d}"


def fmt_prep(value: date) -> str:
    return f"{value.year}/{value.month:02d}/{value.day:02d}"


def pick_qty(rng: random.Random, px: float) -> float:
    if px >= 800:
        return rng.choice([1, 1, 2])
    if px >= 250:
        n = rng.choice([1, 2, 2, 3, 4, 5, 6, 8])
        if rng.random() < 0.18:
            return round(n + rng.choice([0.1234, 0.2468, 0.5, 0.5779, 0.8]), 4)
        return float(n)
    if px >= 80:
        return float(rng.choice([2, 3, 4, 5, 6, 8, 10, 12, 15, 18, 20]))
    if px >= 20:
        return float(rng.choice([8, 10, 12, 15, 20, 25, 30, 40, 50, 80]))
    return float(rng.choice([20, 30, 40, 50, 80, 100, 120, 150, 200]))


def pick_price(rng: random.Random, low: float, high: float) -> float:
    return round(rng.uniform(low, high), 4)


def next_price(rng: random.Random, start: float) -> float:
    moved = start * (1 + rng.uniform(-0.11, 0.16))
    return round(max(0.20, moved), 4)


def logo_svg() -> str:
    return """<svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true">
        <path fill="#ff6a00" d="M4.2 18.2c0-6.4 3.2-12.6 8.4-14.6 1.6-.6 3.2-.4 4.4.6-2.6 1.1-4.7 4.2-4.7 8.1 0 2.6 1.1 5 3.4 6.7H6.1c-.8-.8-1.9-2.3-1.9-4.8z"/>
        <circle cx="15.6" cy="7.2" r="2.3" fill="#ff6a00"/>
    </svg>"""


def page_chrome(inner: str, stmt: Statement, page: int, total: int) -> str:
    title = f"Monthly Statement of {stmt.account_name}"
    return f"""
<div class="page">
    <div class="topbar">
        <div class="logo">{logo_svg()}<div class="logo-word">moomoo</div></div>
        <div class="doc-meta">{title}<br>{fmt_period(stmt.period_end)}</div>
    </div>
    {inner}
    <div class="footer">
        <div class="left">
            Preparation Date: {fmt_prep(stmt.prep_date)}<br>
            Client Name: {stmt.name} &nbsp;&nbsp; Account Number: {stmt.account_number}
        </div>
        <div>Page {page} of {total}</div>
    </div>
</div>
"""


def ccy_cells(values: dict[str, float], signed: bool = False) -> str:
    return "".join(f"<span>{money(values.get(code, 0.0), signed)}</span>" for code in CURRENCIES)


def nav_row(label: str, sgd: float, parts: dict[str, float], fx: dict[str, str]) -> str:
    cells = []
    for code in CURRENCIES:
        cells.append(
            f'<div class="nav-ccy"><div class="k">{code}</div>'
            f'<div class="v">{money(parts.get(code, 0.0))}</div>'
            f'<div class="fx">exchange rate : {fx.get(code, "-")}</div></div>'
        )
    return f"""
    <div class="nav-block">
        <div class="nav-title">{label}</div>
        <div class="nav-eq"><div class="k">Equal to(SGD)</div><div class="v">{money(sgd)}</div></div>
        {''.join(cells)}
    </div>
    """


def holding_change_row(h: Holding) -> str:
    vals = {code: 0.0 for code in CURRENCIES}
    vals[h.currency] = h.change
    return (
        f'<div class="section-row"><span>{h.symbol} {h.name}</span>'
        f"{ccy_cells(vals, signed=True)}</div>"
    )


def position_table(holdings: list[Holding], currency: str) -> str:
    rows = []
    start_total = end_total = change_total = 0
    for h in holdings:
        if h.currency != currency:
            continue
        start_total += money_cents(h.start_value)
        end_total += money_cents(h.end_value)
        change_total += money_cents(h.change)
        start_px = price(h.start_px) if h.start_qty else "-"
        end_px = price(h.end_px) if h.end_qty else "-"
        sell = f"-{qty(h.sell_qty)}" if h.sell_qty else "0"
        buy = f"+{qty(h.buy_qty)}" if h.buy_qty else "0"
        t_out = f"-{qty(h.transfer_out)}" if h.transfer_out else "0"
        rows.append(f"""
        <tr>
            <td class="left">{h.name}<span class="sym">{h.symbol}</span></td>
            <td class="left">{h.exchange}</td>
            <td class="left">{h.currency}</td>
            <td>{qty(h.start_qty) if h.start_qty else "0"}</td>
            <td>{start_px}</td>
            <td>{money(h.start_value)}</td>
            <td>{qty(h.end_qty) if h.end_qty else "0"}</td>
            <td>{end_px}</td>
            <td>{money(h.end_value)}</td>
            <td>{money(h.change, signed=True)}</td>
            <td>{buy}</td>
            <td>{sell}</td>
            <td>0</td>
            <td>{t_out}</td>
        </tr>""")
    if not rows:
        return ""
    return f"""
    <div class="subhead">Securities</div>
    <table class="data">
        <thead>
            <tr>
                <th class="left" rowspan="2">Symbol</th>
                <th class="left" rowspan="2">Exchange</th>
                <th class="left" rowspan="2">Currency</th>
                <th colspan="3">Starting</th>
                <th colspan="3">Ending</th>
                <th rowspan="2">Changes in<br>Position Value</th>
                <th rowspan="2">Buy Quantity</th>
                <th rowspan="2">Sell Quantity</th>
                <th rowspan="2">Transfer In</th>
                <th rowspan="2">Transfer Out</th>
            </tr>
            <tr>
                <th>Quantity</th><th>Closing Price</th><th>Position Value</th>
                <th>Quantity</th><th>Closing Price</th><th>Position Value</th>
            </tr>
        </thead>
        <tbody>
            {''.join(rows)}
            <tr class="total">
                <td class="left" colspan="3">{currency} Total</td>
                <td></td><td></td>                <td>{from_cents(start_total)}</td>
                <td></td><td></td><td>{from_cents(end_total)}</td>
                <td>{money(from_cents(change_total), signed=True)}</td>
                <td colspan="4"></td>
            </tr>
        </tbody>
    </table>
    """


def ending_table(holdings: list[Holding], currency: str, fx: float) -> str:
    rows = []
    total = 0
    count = 0
    for h in holdings:
        if h.currency != currency or h.end_qty <= 0:
            continue
        sgd = convert_sgd(h.end_value, fx)
        total += money_cents(sgd)
        count += 1
        rows.append(f"""
        <tr>
            <td class="left">{h.name}<span class="sym">{h.symbol}</span></td>
            <td class="left">{h.exchange}</td>
            <td class="left">{h.currency}</td>
            <td>{qty(h.end_qty)}</td>
            <td>0</td>
            <td>{qty(h.end_qty)}</td>
            <td>1</td>
            <td>{price(h.end_px)}</td>
            <td>{money(h.end_value)}</td>
            <td>{fx:.6f}</td>
            <td>{money(sgd)}</td>
        </tr>""")
    if not rows:
        return ""
    return f"""
    <div class="subhead">Securities</div>
    <table class="data">
        <thead>
            <tr>
                <th class="left">Symbol</th>
                <th class="left">Exchange</th>
                <th class="left">Currency</th>
                <th>Settled Quantity</th>
                <th>Unsettled Quantity</th>
                <th>Quantity</th>
                <th>Multiplier</th>
                <th>Closing Price</th>
                <th>Market Value</th>
                <th>Exchange Rate</th>
                <th>Convert to Base<br>Currency(SGD)</th>
            </tr>
        </thead>
        <tbody>
            {''.join(rows)}
            <tr class="total">
                <td class="left" colspan="8">Total</td>
                <td colspan="2">Number: {count}</td>
                <td>{money(from_cents(total))}</td>
            </tr>
        </tbody>
    </table>
    """


def fee_line(t: Trade, net: float) -> str:
    extras = []
    if t.direction.startswith("Sell") or t.sec:
        extras.append(f"SEC Fees: <strong>{money(t.sec)}</strong>")
    if t.direction.startswith("Sell") or t.taf:
        extras.append(f"Trading Activity Fees: <strong>{money(t.taf)}</strong>")
    extras.append("Consolidated Audit Trail Fees: <strong>0.00</strong>")
    extras.append(f"Consumption Tax: <strong>{money(t.tax)}</strong>")
    return (
        f"Subtotal: <strong>{money(t.fee)}</strong> &nbsp; "
        f"Number of Transactions: <strong>1</strong> &nbsp; "
        f"Transaction Amount: <strong>{money(t.amount)}</strong> &nbsp; "
        f"Net Transaction Amount: <strong>{money(net, signed=True)}</strong> &nbsp; "
        f"Platform Fees: <strong>{money(t.platform)}</strong> &nbsp; "
        f"Settlement Fees: <strong>{money(t.settlement)}</strong> &nbsp; "
        + " &nbsp; ".join(extras)
    )


def usd_total_row(kind: str, n: int, fee: int, amt: int, net: int,
                  platform: int, settlement: int, tax: int, sec: int, taf: int) -> str:
    extras = []
    if kind == "Sell" or sec:
        extras.append(f"SEC Fees: <strong>{money(from_cents(sec))}</strong>")
    if kind == "Sell" or taf:
        extras.append(f"Trading Activity Fees: <strong>{money(from_cents(taf))}</strong>")
    extras.append("Consolidated Audit Trail Fees: <strong>0.00</strong>")
    extras.append(f"Consumption Tax: <strong>{money(from_cents(tax))}</strong>")
    return f"""
    <div class="usd-total-row">
        <span class="badge {kind.lower()}">{kind}</span>
        <div class="usd-total-copy">
            Total of Transaction Fee:<strong>{money(from_cents(fee))}</strong> &nbsp;
            Number of Transactions: <strong>{n}</strong> &nbsp;
            Number of Orders: <strong>{n}</strong> &nbsp;
            Transactions Amount: <strong>{money(from_cents(amt))}</strong> &nbsp;
            Net Transactions Amount: <strong>{money(from_cents(net), signed=True)}</strong> &nbsp;
            Platform Fees: <strong>{money(from_cents(platform))}</strong> &nbsp;
            Settlement Fees: <strong>{money(from_cents(settlement))}</strong> &nbsp;
            {" &nbsp; ".join(extras)}
        </div>
    </div>
    """


def trades_html(stmt: Statement) -> str:
    if not stmt.trades:
        return "<p class=\"subhead\">Trades - Securities</p><p style=\"font-size:8px;color:#888\">No trades this period.</p>"
    blocks = ["<div class=\"subhead\">Trades - Securities</div><table class=\"data\"><thead><tr>",
              "<th class=\"left\">Direction</th><th class=\"left\">Symbol</th><th class=\"left\">Exchange</th>",
              "<th class=\"left\">Order Capacity</th><th class=\"left\">Currency</th><th class=\"left\">Date/Time</th>",
              "<th>Price</th><th>Quantity</th><th>Amount</th></tr></thead><tbody>"]
    buy = {"n": 0, "fee": 0, "amt": 0, "net": 0, "platform": 0, "settlement": 0, "tax": 0, "sec": 0, "taf": 0}
    sell = {"n": 0, "fee": 0, "amt": 0, "net": 0, "platform": 0, "settlement": 0, "tax": 0, "sec": 0, "taf": 0}
    for t in stmt.trades:
        when = t.when.strftime("%Y/%m/%d<br>%H:%M:%S")
        if t.direction.startswith("Sell"):
            net = round_money(t.amount - t.fee)
            bucket = sell
        else:
            net = round_money(-(t.amount + t.fee))
            bucket = buy
        blocks.append(f"""
        <tr>
            <td class="left">{t.direction}</td>
            <td class="left">{t.holding.name}<span class="sym">{t.holding.symbol}</span></td>
            <td class="left">{t.venue}</td>
            <td class="left">Agency</td>
            <td class="left">USD</td>
            <td class="left">{when}</td>
            <td>{price(t.price)}</td>
            <td>{qty(t.qty)}</td>
            <td>{money(t.amount)}</td>
        </tr>""")
        blocks.append(
            f'<tr><td colspan="9" class="left"><div class="trade-fees">{fee_line(t, net)}</div></td></tr>'
        )
        bucket["n"] += 1
        bucket["fee"] += money_cents(t.fee)
        bucket["amt"] += money_cents(t.amount)
        bucket["net"] += money_cents(net)
        bucket["platform"] += money_cents(t.platform)
        bucket["settlement"] += money_cents(t.settlement)
        bucket["tax"] += money_cents(t.tax)
        bucket["sec"] += money_cents(t.sec)
        bucket["taf"] += money_cents(t.taf)
    blocks.append("</tbody></table>")
    if buy["n"] or sell["n"]:
        blocks.append('<div class="usd-total"><div class="usd-total-bar">USD Total</div>')
        if buy["n"]:
            blocks.append(usd_total_row(
                "Buy", buy["n"], buy["fee"], buy["amt"], buy["net"],
                buy["platform"], buy["settlement"], buy["tax"], buy["sec"], buy["taf"],
            ))
        if sell["n"]:
            blocks.append(usd_total_row(
                "Sell", sell["n"], sell["fee"], sell["amt"], sell["net"],
                sell["platform"], sell["settlement"], sell["tax"], sell["sec"], sell["taf"],
            ))
        blocks.append("</div>")
    return "".join(blocks)


def cash_panel(code: str, stmt: Statement, compact: bool = False) -> str:
    start = stmt.cash_start[code]
    end = stmt.cash_end[code]
    events = stmt.cash_events.get(code, [])
    rows = []
    total = 0
    for ev in events:
        total += money_cents(ev.amount)
        rows.append(
            f"<tr><td class='left'>{ev.when}</td><td class='left'>{ev.type}</td>"
            f"<td>{money(ev.amount, signed=True)}</td><td class='left'>{ev.comment}</td></tr>"
        )
    if not rows:
        rows.append(f"<tr class='total'><td class='left' colspan='2'>{code} Total</td><td>0.00</td><td></td></tr>")
    else:
        rows.append(
            f"<tr class='total'><td class='left' colspan='2'>{code} Total</td>"
            f"<td>{money(from_cents(total), signed=True)}</td><td></td></tr>"
        )
    wrap_cls = "cash-wrap compact" if compact else "cash-wrap"
    return f"""
    <div class="{wrap_cls}">
        <div class="cash-box">
            <h3>{code}</h3>
            <div class="row"><span>Starting Cash</span><span>{money(start)}</span></div>
            <div class="row"><span>Ending Cash</span><span>{money(end)}</span></div>
            <div class="row muted"><span>Ending Settled Cash</span><span>{money(end)}</span></div>
            <div class="row muted"><span>Ending Unsettled Cash</span><span>0.00</span></div>
        </div>
        <div class="cash-ledger">
            <table class="data">
                <thead><tr><th class="left">Date/Time</th><th class="left">Type</th><th>Amount</th><th class="left">Comment</th></tr></thead>
                <tbody>{''.join(rows)}</tbody>
            </table>
        </div>
    </div>
    """


def notice_html() -> str:
    return """
    <h2>Important Notice</h2>
    <div class="notice">
    <ol>
        <li>Confirmation for transactions are processed by Moomoo Financial Singapore Pte. Ltd. ("Moomoo Singapore") as a licensed broker-dealer and custodian in Singapore. Unless otherwise stated, the above transactions are executed by Moomoo Singapore in the name of an agent.</li>
        <li>Securities are held on behalf by Moomoo Singapore and/or its appointed custodian.</li>
        <li>Please promptly report any inaccuracy or discrepancy found in this statement to Moomoo Singapore Customer Service Department at clientservice@sg.moomoo.com within 14 calendar days from the date of this document.</li>
        <li>If this is a margin account, interest charged on the debit balance in your account and/or the amount you borrowed is shown on this statement. The margin interest rates may change from time to time without prior notice.</li>
        <li>The closing prices on this document are unaudited and may come from third-party sources. Moomoo Singapore does not warrant the accuracy of the prices.</li>
        <li>Year to date income and realized gain/loss figures in this statement are for informational purposes only and should not be used for income tax purposes.</li>
        <li>For additional information about your account, see "Terms &amp; Policies" at www.moomoo.com/sg .</li>
        <li>For information on fees, see our pricing structure at www.moomoo.com/sg/broker-about/pricing .</li>
        <li>Moomoo Singapore is a member of the Financial Institutions Dispute Resolution Centre (FIDReC). Unresolved issues can be brought up to FIDReC at www.fidrec.com.sg for mediation.</li>
        <li>The account name can be changed at the client's discretion. The account name should not be used for any legal verification.</li>
    </ol>
    <div class="legal">
        <strong>Moomoo Financial Singapore Pte. Ltd.</strong><br>
        Moomoo Singapore is a CMS and MPI licence holder with EFA status licensed by the Monetary Authority of Singapore.<br>
        www.moomoo.com/sg<br>
        10 Marina Boulevard MBFC Tower 2 #31-01, Singapore 018983<br>
        GST Reg No: 201942579C
    </div>
    </div>
    """


def cash_change_map(stmt: Statement) -> dict[str, dict[str, float]]:
    labels = {
        "Buy Amount": "buy",
        "Buy Fee": "buy_fee",
        "Sell Amount": "sell",
        "Sell Fee": "sell_fee",
        "Corporate Action": "corp",
    }
    out = {key: {code: 0.0 for code in CURRENCIES} for key in
           ("buy", "buy_fee", "sell", "sell_fee", "corp", "total")}
    for code, events in stmt.cash_events.items():
        for ev in events:
            bucket = labels.get(ev.type)
            if bucket:
                out[bucket][code] = round_money(out[bucket][code] + ev.amount)
    for key in ("buy", "buy_fee", "sell", "sell_fee", "corp"):
        for code in CURRENCIES:
            out["total"][code] = round_money(out["total"][code] + out[key][code])
    return out


def fx_label(code: str, value: float) -> str:
    if code == "SGD":
        return "1.000000"
    if code == "USD":
        return f"{value:.6f}"
    return "-"


def empty_ccy() -> dict[str, float]:
    return {code: 0.0 for code in CURRENCIES}


def holdings_value(holdings: list[Holding], currency: str, which: str) -> int:
    total = 0
    for h in holdings:
        if h.currency != currency:
            continue
        total += money_cents(h.start_value if which == "start" else h.end_value)
    return total


def finalize(stmt: Statement) -> Statement:
    for h in stmt.holdings:
        h.end_qty = round(h.start_qty + h.buy_qty - h.sell_qty + h.transfer_in - h.transfer_out, 4)
        if h.end_qty <= 0:
            h.end_qty = 0.0
            h.end_px = 0.0

    for t in stmt.trades:
        t.fee = round_money(t.platform + t.settlement + t.tax + t.sec + t.taf)
        t.amount = round_money(Decimal(str(t.price)) * Decimal(str(t.qty)))

    for code in CURRENCIES:
        events = stmt.cash_events.setdefault(code, [])
        delta = sum(money_cents(ev.amount) for ev in events)
        stmt.cash_start[code] = round_money(stmt.cash_start.get(code, 0.0))
        stmt.cash_end[code] = from_cents(money_cents(stmt.cash_start[code]) + delta)

    stmt.cash_change = cash_change_map(stmt)
    stmt.pos_change = empty_ccy()
    stmt.pos_change["SGD"] = from_cents(sum(money_cents(h.change) for h in stmt.holdings if h.currency == "SGD"))
    stmt.pos_change["USD"] = from_cents(sum(money_cents(h.change) for h in stmt.holdings if h.currency == "USD"))

    sgd_pos_start = holdings_value(stmt.holdings, "SGD", "start")
    sgd_pos_end = holdings_value(stmt.holdings, "SGD", "end")
    usd_pos_start = holdings_value(stmt.holdings, "USD", "start")
    usd_pos_end = holdings_value(stmt.holdings, "USD", "end")

    stmt.start_parts = empty_ccy()
    stmt.end_parts = empty_ccy()
    stmt.start_parts["SGD"] = from_cents(money_cents(stmt.cash_start["SGD"]) + sgd_pos_start)
    stmt.start_parts["USD"] = from_cents(money_cents(stmt.cash_start["USD"]) + usd_pos_start)
    stmt.end_parts["SGD"] = from_cents(money_cents(stmt.cash_end["SGD"]) + sgd_pos_end)
    stmt.end_parts["USD"] = from_cents(money_cents(stmt.cash_end["USD"]) + usd_pos_end)

    portfolio_cents = sgd_pos_end
    for h in stmt.holdings:
        if h.currency == "USD" and h.end_qty > 0:
            portfolio_cents += money_cents(convert_sgd(h.end_value, stmt.usd_end_fx))
    cash_cents = money_cents(stmt.cash_end["SGD"]) + money_cents(convert_sgd(stmt.cash_end["USD"], stmt.usd_end_fx))
    start_cash_cents = money_cents(stmt.cash_start["SGD"]) + money_cents(convert_sgd(stmt.cash_start["USD"], stmt.usd_start_fx))
    start_port_cents = sgd_pos_start
    for h in stmt.holdings:
        if h.currency == "USD" and h.start_qty > 0:
            start_port_cents += money_cents(convert_sgd(h.start_value, stmt.usd_start_fx))

    stmt.portfolio = from_cents(portfolio_cents)
    stmt.cash_balance = from_cents(cash_cents)
    stmt.end_nav = from_cents(portfolio_cents + cash_cents)
    stmt.start_nav = from_cents(start_port_cents + start_cash_cents)
    return stmt


def verify(stmt: Statement) -> None:
    errors: list[str] = []
    for code in CURRENCIES:
        delta = sum(money_cents(ev.amount) for ev in stmt.cash_events.get(code, []))
        expected = money_cents(stmt.cash_start[code]) + delta
        if expected != money_cents(stmt.cash_end[code]):
            errors.append(
                f"{code} cash {stmt.cash_start[code]} + {from_cents(delta)} != {stmt.cash_end[code]}"
            )
    for h in stmt.holdings:
        expected_qty = round(h.start_qty + h.buy_qty - h.sell_qty + h.transfer_in - h.transfer_out, 4)
        if abs(expected_qty - h.end_qty) > 1e-6:
            errors.append(f"{h.symbol} qty {h.start_qty}+{h.buy_qty}-{h.sell_qty} != {h.end_qty}")
        if money_cents(h.change) != money_cents(h.end_value) - money_cents(h.start_value):
            errors.append(f"{h.symbol} change {h.change} != {h.end_value}-{h.start_value}")
    if money_cents(stmt.end_nav) != money_cents(stmt.portfolio) + money_cents(stmt.cash_balance):
        errors.append(f"NAV {stmt.end_nav} != portfolio {stmt.portfolio} + cash {stmt.cash_balance}")
    pos_sgd = from_cents(sum(money_cents(h.change) for h in stmt.holdings if h.currency == "SGD"))
    pos_usd = from_cents(sum(money_cents(h.change) for h in stmt.holdings if h.currency == "USD"))
    if money_cents(stmt.pos_change["SGD"]) != money_cents(pos_sgd):
        errors.append("SGD position change total mismatch")
    if money_cents(stmt.pos_change["USD"]) != money_cents(pos_usd):
        errors.append("USD position change total mismatch")
    for t in stmt.trades:
        if money_cents(t.fee) != money_cents(t.platform + t.settlement + t.tax + t.sec + t.taf):
            errors.append(f"{t.holding.symbol} fee components {t.fee}")
        if money_cents(t.amount) != money_cents(Decimal(str(t.price)) * Decimal(str(t.qty))):
            errors.append(f"{t.holding.symbol} trade amount {t.amount} != {t.price}*{t.qty}")
    for code in CURRENCIES:
        cash_delta = money_cents(stmt.cash_end[code]) - money_cents(stmt.cash_start[code])
        if money_cents(stmt.cash_change["total"][code]) != cash_delta:
            errors.append(
                f"{code} cash change table {stmt.cash_change['total'][code]} != {from_cents(cash_delta)}"
            )


def render(stmt: Statement) -> str:
    start_end = prev_month_end(stmt.period_end)
    start_fx = {code: fx_label(code, stmt.usd_start_fx) for code in CURRENCIES}
    end_fx = {code: fx_label(code, stmt.usd_end_fx) for code in CURRENCIES}
    cash_map = stmt.cash_change
    pos_change = stmt.pos_change

    change_rows = [holding_change_row(h) for h in stmt.holdings]
    page1_changes = change_rows[:7]
    leftover_changes = change_rows[7:]
    transit_block = (
        '<div class="section-head"><span>+ Changes in Assets in Transit</span>'
        "<span>SGD</span><span>USD</span><span>HKD</span><span>CNH</span><span>JPY</span><span>KRW</span></div>"
        f'<div class="section-total"><span>Total</span>{ccy_cells({c: 0.0 for c in CURRENCIES})}</div>'
    )
    total_block = f'<div class="section-total"><span>Total</span>{ccy_cells(pos_change, True)}</div>'

    page1 = f"""
    <h1>Account Information</h1>
    <table class="info-table">
        <tr>
            <td class="lbl">Name</td><td class="val">{stmt.name}</td>
            <td class="lbl">Account Name</td><td class="val">{stmt.account_name}</td>
        </tr>
        <tr>
            <td class="lbl">Account Number</td><td class="val">{stmt.account_number}</td>
            <td class="lbl">Account Type</td><td class="val">{stmt.account_type}</td>
        </tr>
        <tr>
            <td class="lbl">Address</td><td class="val" colspan="3">{stmt.address}</td>
        </tr>
    </table>
    <table class="metrics">
        <tr>
            <td><div class="k">Net Asset Value:</div><div class="v">{money(stmt.end_nav)}</div><div class="k">Base Currency: SGD</div></td>
            <td><div class="k">Assets in Transit</div><div class="v">0.00</div></td>
            <td><div class="k">Portfolio Value</div><div class="v">{money(stmt.portfolio)}</div></td>
            <td><div class="k">Cash Balance</div><div class="v">{money(stmt.cash_balance)}</div></td>
        </tr>
    </table>
    <h2>Changes in Net Asset Value</h2>
    {nav_row("Starting Net Asset Value " + fmt_stamp(start_end), stmt.start_nav, stmt.start_parts, start_fx)}
    {nav_row("Ending Net Asset Value " + fmt_stamp(stmt.period_end), stmt.end_nav, stmt.end_parts, end_fx)}
    <div class="section-head"><span>+ Changes in Cash</span><span>SGD</span><span>USD</span><span>HKD</span><span>CNH</span><span>JPY</span><span>KRW</span></div>
    <div class="section-row"><span>Buy Amount</span>{ccy_cells(cash_map['buy'], True)}</div>
    <div class="section-row"><span>Buy Fee</span>{ccy_cells(cash_map['buy_fee'], True)}</div>
    <div class="section-row"><span>Sell Amount</span>{ccy_cells(cash_map['sell'], True)}</div>
    <div class="section-row"><span>Sell Fee</span>{ccy_cells(cash_map['sell_fee'], True)}</div>
    <div class="section-row"><span>Corporate Action</span>{ccy_cells(cash_map['corp'], True)}</div>
    <div class="section-total"><span>Total</span>{ccy_cells(cash_map['total'], True)}</div>
    <div class="section-head"><span>+ Changes in Position Value</span><span>SGD</span><span>USD</span><span>HKD</span><span>CNH</span><span>JPY</span><span>KRW</span></div>
    {''.join(page1_changes)}
    {'' if leftover_changes else total_block + transit_block}
    """

    page2_top = ""
    if leftover_changes:
        page2_top = "".join(leftover_changes) + total_block + transit_block

    page2 = f"""
    {page2_top}
    <h2>Changes in Position Value</h2>
    {position_table(stmt.holdings, 'SGD')}
    {position_table(stmt.holdings, 'USD')}
    """

    page3 = f"""
    {trades_html(stmt)}
    <h2>Changes in Cash</h2>
    {cash_panel('SGD', stmt)}
    {cash_panel('USD', stmt)}
    """

    page4 = "".join(cash_panel(code, stmt, compact=True) for code in ("HKD", "CNH", "JPY", "KRW"))

    page5 = f"""
    <h2>Ending Positions</h2>
    {ending_table(stmt.holdings, 'SGD', 1.0)}
    {ending_table(stmt.holdings, 'USD', stmt.usd_end_fx)}
    """

    pages = [page1, page2, page3, page4, page5, notice_html()]
    body = "".join(page_chrome(html, stmt, i, len(pages)) for i, html in enumerate(pages, 1))
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Monthly Statement - {stmt.name}</title>
    <style>{CSS_PATH.read_text(encoding="utf-8")}</style>
</head>
<body>
{body}
</body>
</html>
"""


def make_address(street: str, unit: str, postal: str) -> str:
    street = street.upper()
    if unit:
        return f"{street},{unit},Singapore,{postal}"
    return f"{street},Singapore,{postal}"


def make_holding(rng: random.Random, symbol: str, name: str, exchange: str, currency: str,
                 low: float, high: float) -> Holding:
    start_px = pick_price(rng, low, high)
    qty_val = pick_qty(rng, start_px)
    return Holding(
        name=name,
        symbol=symbol,
        exchange=exchange,
        currency=currency,
        start_qty=qty_val,
        start_px=start_px,
        end_qty=qty_val,
        end_px=next_price(rng, start_px),
    )


def add_trade(rng: random.Random, holding: Holding, direction: str, period_end: date) -> Trade:
    day = rng.randint(4, max(4, period_end.day - 2))
    when = datetime(period_end.year, period_end.month, min(day, period_end.day),
                    rng.randint(20, 22), rng.randint(0, 59), rng.randint(0, 59))
    platform = 0.99
    settlement = round_money(rng.choice([0.01, 0.02, 0.03]))
    tax = 0.09
    sec = round_money(rng.choice([0.00, 0.01, 0.01, 0.02])) if direction.startswith("Sell") else 0.0
    taf = round_money(rng.choice([0.00, 0.01])) if direction.startswith("Sell") else 0.0
    fee = round_money(platform + settlement + tax + sec + taf)
    if direction.startswith("Buy"):
        qty_val = pick_qty(rng, holding.end_px)
        px = round(holding.end_px * rng.uniform(0.97, 1.04), 4)
        holding.buy_qty = qty_val
        holding.buy_price = px
        holding.end_qty = round(holding.start_qty + qty_val, 4)
        amount = round_money(Decimal(str(px)) * Decimal(str(qty_val)))
    else:
        qty_val = holding.start_qty
        px = round(holding.start_px * rng.uniform(0.92, 1.12), 4)
        holding.sell_qty = qty_val
        holding.sell_price = px
        holding.end_qty = 0.0
        holding.end_px = 0.0
        amount = round_money(Decimal(str(px)) * Decimal(str(qty_val)))
    return Trade(
        direction=direction,
        holding=holding,
        venue=rng.choice(VENUES),
        when=when,
        price=px,
        qty=qty_val,
        amount=amount,
        fee=fee,
        platform=platform,
        settlement=settlement,
        tax=tax,
        sec=sec,
        taf=taf,
    )


def build_statement(rng: random.Random, period_end: date | None = None) -> Statement:
    name, street, unit, postal = rng.choice(CLIENTS)
    if period_end is None:
        year = rng.randint(2024, 2026)
        month = rng.randint(1, 12)
        period_end = last_day(year, month)
    prep = period_end + timedelta(days=1)
    suffix = f"{rng.randint(1000, 9999)}"
    account_type = rng.choice(["Margin", "Margin", "Margin", "Cash"])
    acct_name = f"{account_type} Account ({suffix}) - Securities"
    account_number = f"1008{rng.randint(10000000, 99999999)}{suffix}"

    usd_start_fx = round(rng.uniform(1.2680, 1.3550), 6)
    usd_end_fx = round(usd_start_fx * rng.uniform(0.988, 1.012), 6)

    holdings: list[Holding] = []
    if rng.random() < 0.75:
        sym, nm, lo, hi = rng.choice(SGX_STOCKS)
        holdings.append(make_holding(rng, sym, nm, "SGX", "SGD", lo, hi))

    us_count = rng.randint(5, 9)
    us_picks = rng.sample(US_STOCKS, us_count)
    for sym, nm, lo, hi in us_picks:
        holdings.append(make_holding(rng, sym, nm, "US", "USD", lo, hi))

    trades: list[Trade] = []
    usd_names = [h for h in holdings if h.currency == "USD"]
    if usd_names and rng.random() < 0.7:
        affordable = [h for h in usd_names if h.end_px < 900]
        target = rng.choice(affordable or usd_names)
        trades.append(add_trade(rng, target, "Buy to Open", period_end))
    sellable = [h for h in usd_names if h.start_qty and h.end_value < 900]
    if sellable and rng.random() < 0.45:
        target = rng.choice(sellable)
        if target not in [t.holding for t in trades]:
            trades.append(add_trade(rng, target, "Sell to Close", period_end))

    cash_start = {code: 0.0 for code in CURRENCIES}
    cash_start["SGD"] = round_money(rng.uniform(2.0, 420.0))
    cash_start["USD"] = round_money(rng.uniform(480.0, 4200.0))
    cash_events: dict[str, list[CashEvent]] = {code: [] for code in CURRENCIES}

    for t in trades:
        if t.direction.startswith("Buy"):
            cash_events["USD"].append(CashEvent("-", "Buy Amount", -t.amount, ""))
            cash_events["USD"].append(CashEvent("-", "Buy Fee", -t.fee, ""))
        else:
            cash_events["USD"].append(CashEvent("-", "Sell Amount", t.amount, ""))
            cash_events["USD"].append(CashEvent("-", "Sell Fee", -t.fee, ""))

    sgd_hold = [h for h in holdings if h.currency == "SGD" and (h.end_qty or h.start_qty)]
    if sgd_hold and rng.random() < 0.55:
        h = sgd_hold[0]
        dps = rng.choice([0.16, 0.22, 0.32, 0.41, 0.54, 0.64, 0.81])
        qty_for_div = h.end_qty if h.end_qty else h.start_qty
        amount = round_money(Decimal(str(qty_for_div)) * Decimal(str(dps)))
        day = rng.randint(8, min(26, period_end.day))
        when = f"{period_end.year}/{period_end.month:02d}/{day:02d} {rng.randint(8, 11):02d}:{rng.randint(0, 59):02d}:{rng.randint(0, 59):02d}"
        cash_events["SGD"].append(
            CashEvent(when, "Corporate Action", amount, f"{h.symbol} CASH DIVIDEND AT SGD {dps:.2f} PER SHARE")
        )

    usd_delta = sum(money_cents(ev.amount) for ev in cash_events["USD"])
    usd_end = money_cents(cash_start["USD"]) + usd_delta
    if usd_end < 8000:
        bump = from_cents(8000 + money_cents(rng.uniform(120.0, 900.0)) - usd_end)
        cash_start["USD"] = round_money(cash_start["USD"] + bump)

    stmt = Statement(
        name=name.upper(),
        address=make_address(street, unit, postal),
        account_number=account_number,
        account_name=acct_name,
        account_type=account_type,
        period_end=period_end,
        prep_date=prep,
        usd_start_fx=usd_start_fx,
        usd_end_fx=usd_end_fx,
        holdings=holdings,
        trades=trades,
        cash_start=cash_start,
        cash_end={code: 0.0 for code in CURRENCIES},
        cash_events=cash_events,
    )
    return finalize(stmt)


def find_chrome() -> Path:
    candidates = [
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
        Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
    ]
    for path in candidates:
        if path.exists():
            return path
    raise SystemExit(
        "Chrome, Chromium, or Edge is required to export PDFs."
    )


def html_to_pdf(html: str, pdf_path: Path) -> None:
    chrome = find_chrome()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        html_path = tmp_dir / "statement.html"
        html_path.write_text(html, encoding="utf-8")
        result = subprocess.run(
            [
                str(chrome),
                "--headless",
                "--disable-gpu",
                "--no-pdf-header-footer",
                f"--print-to-pdf={pdf_path.resolve()}",
                html_path.resolve().as_uri(),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=45,
        )
        if result.returncode != 0 or not pdf_path.exists() or pdf_path.stat().st_size < 1000:
            raise SystemExit(
                "Failed to write PDF with Chrome.\n"
                + (result.stderr or result.stdout or f"exit {result.returncode}")
            )


def write_index(output_dir: Path, rows: list[dict[str, str]]) -> None:
    body = []
    for row in rows:
        body.append(
            "<tr>"
            f"<td><a href=\"{row['filename']}\">{row['filename']}</a></td>"
            f"<td>{row['name']}</td>"
            f"<td>{row['account_number']}</td>"
            f"<td>{row['period']}</td>"
            f"<td>{row['holdings']}</td>"
            f"<td>{row['nav']}</td>"
            "</tr>"
        )
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Generated Investment Statements</title>
    <style>
        body {{ font-family: Helvetica, Arial, sans-serif; margin: 40px; color: #222; }}
        table {{ border-collapse: collapse; width: 100%; }}
        th, td {{ padding: 10px 12px; border-bottom: 1px solid #ddd; text-align: left; }}
        a {{ color: #c45c12; }}
    </style>
</head>
<body>
    <h1>Monthly Securities Statements</h1>
    <p>{len(rows)} PDF statements generated from the Moomoo layout.</p>
    <table>
        <thead><tr><th>File</th><th>Client</th><th>Account</th><th>Period</th><th>Holdings</th><th>NAV (SGD)</th></tr></thead>
        <tbody>{''.join(body)}</tbody>
    </table>
</body>
</html>
"""
    (output_dir / "index.html").write_text(html, encoding="utf-8")


def generate(count: int, output_dir: Path, seed: int | None) -> list[dict[str, str]]:
    rng = random.Random(seed)
    output_dir.mkdir(parents=True, exist_ok=True)
    for stale in output_dir.glob("statement_*.html"):
        stale.unlink()
    for stale in output_dir.glob("statement_*.pdf"):
        stale.unlink()
    for name in ("index.html", "manifest.json", "statement.css"):
        path = output_dir / name
        if path.exists():
            path.unlink()

    used: set[str] = set()
    rows: list[dict[str, str]] = []
    for index in range(1, count + 1):
        stmt = build_statement(rng)
        while stmt.name in used and len(used) < len(CLIENTS):
            stmt = build_statement(rng)
        used.add(stmt.name)
        verify(stmt)
        filename = f"statement_{index:03d}_{slug(stmt.name)}.pdf"
        html_to_pdf(render(stmt), output_dir / filename)
        nav = stmt.end_nav
        row = {
            "filename": filename,
            "name": stmt.name,
            "address": stmt.address,
            "account_number": stmt.account_number,
            "account_name": stmt.account_name,
            "account_type": stmt.account_type,
            "period": fmt_period(stmt.period_end),
            "holdings": ", ".join(h.symbol for h in stmt.holdings if h.end_qty > 0),
            "nav": money(nav),
            "portfolio_symbols": len([h for h in stmt.holdings if h.end_qty > 0]),
            "trade_count": len(stmt.trades),
        }
        rows.append(row)

    (output_dir / "manifest.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    write_index(output_dir, rows)
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate Moomoo-style monthly securities statements as PDFs."
    )
    parser.add_argument("-n", "--count", type=int, default=10, help="Number of statements (default: 10)")
    parser.add_argument(
        "-o", "--out",
        type=Path,
        default=Path(__file__).resolve().parent / "output",
        help="Output directory (default: ./output)",
    )
    parser.add_argument("--seed", type=int, default=None, help="Optional random seed")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.count < 1:
        raise SystemExit("--count must be at least 1")
    if not CSS_PATH.exists():
        raise SystemExit("statement.css must sit next to this script.")
    rows = generate(args.count, args.out, args.seed)
    print(f"Generated {len(rows)} PDFs in {args.out}")
    print(f"Open {args.out / 'index.html'} to browse them.")


if __name__ == "__main__":
    main()
