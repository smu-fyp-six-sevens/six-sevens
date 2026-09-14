#!/usr/bin/env python3
"""generate_statements.py — Moomoo-style monthly securities statement, used as
Source of Wealth (Individual) supporting evidence in the "Optional Documents"
mastersheet tab (Document Type "Source of Wealth (Individual) - Director & UBO"
or "... - Shareholder").

Rewritten from an HTML/CSS + headless-Chrome pipeline to pure ReportLab canvas
(landscape A4, 6 pages), matching the rest of doc_gen_scripts. All of the
financial computation -- holdings, trades, cash ledgers, NAV reconciliation,
the verify() self-check -- is untouched; only the renderer changed from HTML
strings to canvas drawing. statement.css is kept alongside this file as the
design reference; not used at generation time anymore.

Usage:
cd backend/doc_gen_scripts/optional_documents/investment
python generate_statements.py ../../v15_document_mastersheet.xlsx --outdir out/
python generate_statements.py ../../v15_document_mastersheet.xlsx --outdir out/ --cases C042

Run with no arguments to build one example statement at output/statement_example.pdf.
"""

from __future__ import annotations

import argparse
import random
import re
import sys
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import openpyxl
from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfgen import canvas as pdfcanvas

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from case_facts import seeded_rng as _case_seeded_rng  # noqa: E402

CURRENCIES = ["SGD", "USD", "HKD", "CNH", "JPY", "KRW"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
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
    ("D05", "DBS", 32.0, 48.0), ("O39", "OCBC", 13.0, 18.5), ("U11", "UOB", 28.0, 38.0),
    ("Z74", "Singtel", 2.40, 3.60), ("C6L", "Singapore Airlines", 6.20, 8.40),
    ("BN4", "Keppel", 6.00, 8.80), ("S68", "Singapore Exchange", 9.50, 13.80),
    ("V03", "Venture Corporation", 12.0, 16.5), ("C07", "Jardine Cycle & Carriage", 24.0, 34.0),
    ("U96", "Sembcorp Industries", 4.20, 7.40), ("H78", "Hongkong Land", 5.40, 7.20),
    ("J36", "Jardine Matheson", 38.0, 52.0),
]

US_STOCKS = [
    ("AAPL", "Apple", 165.0, 240.0), ("MSFT", "Microsoft", 370.0, 455.0), ("NVDA", "NVIDIA", 95.0, 145.0),
    ("AMZN", "Amazon", 155.0, 230.0), ("GOOGL", "Alphabet", 140.0, 195.0), ("META", "Meta Platforms", 430.0, 620.0),
    ("TSLA", "Tesla", 170.0, 380.0), ("JPM", "JPMorgan Chase", 175.0, 250.0), ("V", "Visa", 250.0, 320.0),
    ("UNH", "UnitedHealth", 480.0, 590.0), ("WMT", "Walmart", 60.0, 95.0), ("HD", "Home Depot", 330.0, 420.0),
    ("COST", "Costco", 720.0, 980.0), ("NFLX", "Netflix", 550.0, 780.0), ("AMD", "Advanced Micro Devices", 95.0, 180.0),
    ("ORCL", "Oracle", 110.0, 190.0), ("CRM", "Salesforce", 220.0, 310.0), ("KO", "Coca-Cola", 58.0, 72.0),
    ("PEP", "PepsiCo", 150.0, 185.0), ("DIS", "Walt Disney", 85.0, 125.0), ("BA", "Boeing", 150.0, 230.0),
    ("CAT", "Caterpillar", 280.0, 390.0), ("GS", "Goldman Sachs", 390.0, 560.0), ("MCD", "McDonald's", 250.0, 310.0),
    ("NKE", "Nike", 70.0, 110.0), ("QCOM", "Qualcomm", 140.0, 210.0), ("AVGO", "Broadcom", 140.0, 230.0),
    ("INTU", "Intuit", 520.0, 720.0), ("BKNG", "Booking Holdings", 3200.0, 4800.0), ("PLTR", "Palantir", 18.0, 45.0),
    ("COIN", "Coinbase", 140.0, 280.0), ("MARA", "MARA Holdings", 9.0, 22.0), ("RGTI", "Rigetti Computing", 8.0, 22.0),
    ("SOFI", "SoFi Technologies", 7.0, 18.0), ("NIO", "NIO", 3.5, 8.5),
]


# --------------------------------------------------------------------------
# Pure computation -- dataclasses, money/qty formatting, statement builder,
# reconciliation. Unchanged from the HTML-era version: this logic has nothing
# to do with the renderer.
# --------------------------------------------------------------------------

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
        name=name, symbol=symbol, exchange=exchange, currency=currency,
        start_qty=qty_val, start_px=start_px, end_qty=qty_val, end_px=next_price(rng, start_px),
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
        direction=direction, holding=holding, venue=rng.choice(VENUES), when=when,
        price=px, qty=qty_val, amount=amount, fee=fee, platform=platform,
        settlement=settlement, tax=tax, sec=sec, taf=taf,
    )


def cash_change_map(stmt: Statement) -> dict[str, dict[str, float]]:
    labels = {"Buy Amount": "buy", "Buy Fee": "buy_fee", "Sell Amount": "sell",
              "Sell Fee": "sell_fee", "Corporate Action": "corp"}
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
            errors.append(f"{code} cash {stmt.cash_start[code]} + {from_cents(delta)} != {stmt.cash_end[code]}")
    for h in stmt.holdings:
        expected_qty = round(h.start_qty + h.buy_qty - h.sell_qty + h.transfer_in - h.transfer_out, 4)
        if abs(expected_qty - h.end_qty) > 1e-6:
            errors.append(f"{h.symbol} qty {h.start_qty}+{h.buy_qty}-{h.sell_qty} != {h.end_qty}")
        if money_cents(h.change) != money_cents(h.end_value) - money_cents(h.start_value):
            errors.append(f"{h.symbol} change {h.change} != {h.end_value}-{h.start_value}")
    if money_cents(stmt.end_nav) != money_cents(stmt.portfolio) + money_cents(stmt.cash_balance):
        errors.append(f"NAV {stmt.end_nav} != portfolio {stmt.portfolio} + cash {stmt.cash_balance}")
    for t in stmt.trades:
        if money_cents(t.fee) != money_cents(t.platform + t.settlement + t.tax + t.sec + t.taf):
            errors.append(f"{t.holding.symbol} fee components {t.fee}")
        if money_cents(t.amount) != money_cents(Decimal(str(t.price)) * Decimal(str(t.qty))):
            errors.append(f"{t.holding.symbol} trade amount {t.amount} != {t.price}*{t.qty}")
    for code in CURRENCIES:
        cash_delta = money_cents(stmt.cash_end[code]) - money_cents(stmt.cash_start[code])
        if money_cents(stmt.cash_change["total"][code]) != cash_delta:
            errors.append(f"{code} cash change table {stmt.cash_change['total'][code]} != {from_cents(cash_delta)}")
    if errors:
        raise AssertionError("Statement reconciliation failed:\n" + "\n".join(errors))


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
        cash_events["SGD"].append(CashEvent(when, "Corporate Action", amount, f"{h.symbol} CASH DIVIDEND AT SGD {dps:.2f} PER SHARE"))

    usd_delta = sum(money_cents(ev.amount) for ev in cash_events["USD"])
    usd_end = money_cents(cash_start["USD"]) + usd_delta
    if usd_end < 8000:
        bump = from_cents(8000 + money_cents(rng.uniform(120.0, 900.0)) - usd_end)
        cash_start["USD"] = round_money(cash_start["USD"] + bump)

    stmt = Statement(
        name=name.upper(), address=make_address(street, unit, postal), account_number=account_number,
        account_name=acct_name, account_type=account_type, period_end=period_end, prep_date=prep,
        usd_start_fx=usd_start_fx, usd_end_fx=usd_end_fx, holdings=holdings, trades=trades,
        cash_start=cash_start, cash_end={code: 0.0 for code in CURRENCIES}, cash_events=cash_events,
    )
    return finalize(stmt)


# --------------------------------------------------------------------------
# Renderer -- ReportLab canvas, landscape A4, 6 pages
# --------------------------------------------------------------------------

PAGE_W, PAGE_H = landscape(A4)
MARGIN_TOP = 9 * mm
MARGIN_LEFT = 11 * mm
MARGIN_RIGHT = 11 * mm
MARGIN_BOTTOM = 16 * mm
CONTENT_W = PAGE_W - MARGIN_LEFT - MARGIN_RIGHT

FONT = "Helvetica"
BOLD = "Helvetica-Bold"

ORANGE = colors.HexColor("#ff6a00")
GREY_LABEL = colors.HexColor("#888888")
GREY_BORDER = colors.HexColor("#e3e3e3")
GREY_ROW_BORDER = colors.HexColor("#ececec")
GREY_FILL = colors.HexColor("#f4f4f4")
GREY_FILL_SOFT = colors.HexColor("#fafafa")
GROUP_FILL = colors.HexColor("#f7f7f7")
BADGE_BUY = colors.HexColor("#2f6bff")
BADGE_SELL = colors.HexColor("#ff7a18")
LEGAL_RULE = colors.HexColor("#6cb6f5")
MUTED = colors.HexColor("#666666")
INK = colors.HexColor("#222222")
BLACK = colors.black
WHITE = colors.white

ASSETS = Path(__file__).resolve().parent
SHARED_ASSETS = ASSETS.parent.parent / "assets" / "images"
LOGO_PATH = SHARED_ASSETS / "moomoo_logo.png"
DEFAULT_OUT = ASSETS / "output" / "statement_example.pdf"


def _logo() -> ImageReader | None:
    return ImageReader(str(LOGO_PATH)) if LOGO_PATH.exists() else None


def _text(c: pdfcanvas.Canvas, x: float, y: float, text: str, *, font=FONT, size=7,
          color=INK, align: str = "left") -> None:
    c.setFillColor(color)
    c.setFont(font, size)
    if align == "right":
        c.drawRightString(x, y, text)
    elif align == "center":
        c.drawCentredString(x, y, text)
    else:
        c.drawString(x, y, text)


def _wrapped(c: pdfcanvas.Canvas, text: str, x: float, top: float, width: float, *,
             font=FONT, size=7, leading=None, color=INK) -> float:
    leading = leading or size * 1.4
    c.setFillColor(color)
    c.setFont(font, size)
    y = top
    for line in simpleSplit(text, font, size, width):
        c.drawString(x, y, line)
        y -= leading
    return y


def _hline(c: pdfcanvas.Canvas, x0: float, x1: float, y: float, color=GREY_ROW_BORDER, width: float = 0.5) -> None:
    c.setStrokeColor(color)
    c.setLineWidth(width)
    c.line(x0, y, x1, y)


def _cols(specs: list[tuple[str, float, str]], total_w: float) -> list[tuple[str, float, float, str]]:
    """specs: (label, weight, align) -> [(label, x_left, width, align)]"""
    total_weight = sum(w for _, w, _ in specs) or 1.0
    out = []
    x = 0.0
    for label, weight, align in specs:
        w = total_w * weight / total_weight
        out.append((label, x, w, align))
        x += w
    return out


def _table(c: pdfcanvas.Canvas, x0: float, top: float, cols: list[tuple[str, float, float, str]],
           rows: list[list[str]], *, row_h: float = 15, header_h: float = 15, font_size: float = 7.3,
           header: bool = True, total_row_idx: int | None = None, group_rows: set[int] | None = None) -> float:
    """Generic bordered-bottom data table, header shaded grey. Returns bottom y."""
    group_rows = group_rows or set()
    y = top
    total_w = cols[-1][1] + cols[-1][2]
    if header:
        c.setFillColor(GREY_FILL)
        c.rect(x0, y - header_h, total_w, header_h, fill=1, stroke=0)
        for label, cx, cw, align in cols:
            ax = x0 + cx + (cw / 2 if align == "center" else (cw - 3 if align == "right" else 3))
            _text(c, ax, y - header_h + 3, label, font=BOLD, size=font_size, color=MUTED,
                  align="center" if align == "center" else align)
        y -= header_h
    for i, row in enumerate(rows):
        is_total = total_row_idx is not None and i == total_row_idx
        is_group = i in group_rows
        if is_group:
            c.setFillColor(GROUP_FILL)
            c.rect(x0, y - row_h, total_w, row_h, fill=1, stroke=0)
        for (label, cx, cw, align), val in zip(cols, row):
            ax = x0 + cx + (cw - 3 if align == "right" else 3)
            _text(c, ax, y - row_h + 3, val, font=BOLD if (is_total or is_group) else FONT,
                  size=font_size, align=align)
        _hline(c, x0, x0 + total_w, y - row_h, color=colors.HexColor("#cccccc") if is_total else GREY_ROW_BORDER,
               width=0.8 if is_total else 0.5)
        y -= row_h
    return y


def _page_chrome(c: pdfcanvas.Canvas, stmt: "Statement", page: int, total: int) -> float:
    left = MARGIN_LEFT
    right = PAGE_W - MARGIN_RIGHT
    top = PAGE_H - MARGIN_TOP

    logo = _logo()
    if logo is not None:
        logo_w = 48
        logo_h = logo_w * 226 / 1200
        c.drawImage(logo, left, top - logo_h - 3, width=logo_w, height=logo_h, mask="auto")
    else:
        _text(c, left, top - 15, "moomoo", font=BOLD, size=13.5, color=colors.HexColor("#1a1a1a"))

    title = f"Monthly Statement of {stmt.account_name}"
    _text(c, right, top - 9, title, size=7.5, color=GREY_LABEL, align="right")
    _text(c, right, top - 9 - 7.5 * 1.45, fmt_period(stmt.period_end), size=7.5, color=GREY_LABEL, align="right")

    foot_y = MARGIN_BOTTOM - 6 * mm
    _text(c, left, foot_y + 9, f"Preparation Date: {fmt_prep(stmt.prep_date)}", size=7, color=colors.HexColor("#9a9a9a"))
    _text(c, left, foot_y, f"Client Name: {stmt.name}    Account Number: {stmt.account_number}",
          size=7, color=colors.HexColor("#9a9a9a"))
    _text(c, right, foot_y, f"Page {page} of {total}", size=7, color=colors.HexColor("#9a9a9a"), align="right")

    return top - 32


def _ccy_row(c: pdfcanvas.Canvas, label: str, values: dict[str, float], y: float, *,
             cols: list[tuple[str, float, float, str]], bold: bool = False, signed: bool = True,
             fill=None) -> float:
    row_h = 16
    total_w = cols[-1][1] + cols[-1][2]
    if fill is not None:
        c.setFillColor(fill)
        c.rect(MARGIN_LEFT, y - row_h, total_w, row_h, fill=1, stroke=0)
    _text(c, MARGIN_LEFT + 4, y - row_h + 5, label, font=BOLD if bold else FONT, size=7.8)
    for i, code in enumerate(CURRENCIES):
        _, cx, cw, _ = cols[i + 1]
        _text(c, MARGIN_LEFT + cx + cw - 4, y - row_h + 5, money(values.get(code, 0.0), signed),
              font=BOLD if bold else FONT, size=7.8, align="right")
    _hline(c, MARGIN_LEFT, MARGIN_LEFT + total_w, y - row_h, color=colors.HexColor("#dddddd") if bold else GREY_ROW_BORDER)
    return y - row_h


def _section_head(c: pdfcanvas.Canvas, label: str, y: float, cols: list[tuple[str, float, float, str]]) -> float:
    row_h = 17
    total_w = cols[-1][1] + cols[-1][2]
    c.setFillColor(GREY_FILL)
    c.rect(MARGIN_LEFT, y - row_h, total_w, row_h, fill=1, stroke=0)
    _text(c, MARGIN_LEFT + 4, y - row_h + 5.5, label, font=BOLD, size=8)
    for i, code in enumerate(CURRENCIES):
        _, cx, cw, _ = cols[i + 1]
        _text(c, MARGIN_LEFT + cx + cw - 4, y - row_h + 5.5, code, font=BOLD, size=8, align="right")
    return y - row_h


def _nav_block(c: pdfcanvas.Canvas, label: str, sgd: float, parts: dict[str, float],
               fx: dict[str, str], top: float) -> float:
    h = 44
    x0 = MARGIN_LEFT
    title_w, eq_w = 104, 72
    ccy_w = (CONTENT_W - title_w - eq_w) / 6
    c.setStrokeColor(GREY_BORDER)
    c.setLineWidth(0.7)
    c.rect(x0, top - h, CONTENT_W, h, fill=0, stroke=1)
    c.setFillColor(ORANGE)
    c.rect(x0 + 8, top - h / 2 - 14, 2.5, 28, fill=1, stroke=0)
    _wrapped(c, label, x0 + 15, top - h / 2 + 5, title_w - 20, font=BOLD, size=7.5, leading=9)

    ex = x0 + title_w
    c.setStrokeColor(colors.HexColor("#eeeeee"))
    c.line(ex, top, ex, top - h)
    _text(c, ex + 10, top - 16, "Equal to(SGD)", size=6.3, color=GREY_LABEL)
    _text(c, ex + 10, top - 30, money(sgd), font=BOLD, size=11.5)

    cx = ex + eq_w
    for code in CURRENCIES:
        c.setStrokeColor(colors.HexColor("#eeeeee"))
        c.line(cx, top, cx, top - h)
        _text(c, cx + ccy_w / 2, top - 15, code, size=6.3, color=GREY_LABEL, align="center")
        _text(c, cx + ccy_w / 2, top - 27, money(parts.get(code, 0.0)), font=BOLD, size=8, align="center")
        _text(c, cx + ccy_w / 2, top - 37, f"exchange rate : {fx.get(code, '-')}", size=5.6, color=colors.HexColor("#999999"), align="center")
        cx += ccy_w
    return top - h - 6


# --------------------------------------------------------------------------
# Page 1: Account Information + Changes in NAV + Changes in Cash +
#          (start of) Changes in Position Value
# --------------------------------------------------------------------------

def _draw_page1(c: pdfcanvas.Canvas, stmt: Statement, page: int, total: int) -> list[Holding]:
    y = _page_chrome(c, stmt, page, total)
    left = MARGIN_LEFT

    _text(c, left, y, "Account Information", font=BOLD, size=13)
    y -= 20

    info_cols = _cols([("", 0.14, "l"), ("", 0.36, "l"), ("", 0.14, "l"), ("", 0.36, "l")], CONTENT_W)
    info_rows = [
        ("Name", stmt.name, "Account Name", stmt.account_name),
        ("Account Number", stmt.account_number, "Account Type", stmt.account_type),
        ("Address", stmt.address, "", ""),
    ]
    row_h = 19
    for r, (l1, v1, l2, v2) in enumerate(info_rows):
        ry = y - r * row_h
        (_, x0, w0, _), (_, x1, w1, _), (_, x2, w2, _), (_, x3, w3, _) = info_cols
        c.setFillColor(GREY_FILL_SOFT)
        c.rect(left + x0, ry - row_h, w0, row_h, fill=1, stroke=0)
        if l2:
            c.rect(left + x2, ry - row_h, w2, row_h, fill=1, stroke=0)
        c.setStrokeColor(GREY_BORDER)
        c.setLineWidth(0.6)
        val_w = (w1 + w2 + w3) if not l2 else w1
        c.rect(left + x0, ry - row_h, w0, row_h, fill=0, stroke=1)
        c.rect(left + x1, ry - row_h, val_w, row_h, fill=0, stroke=1)
        if l2:
            c.rect(left + x2, ry - row_h, w2, row_h, fill=0, stroke=1)
            c.rect(left + x3, ry - row_h, w3, row_h, fill=0, stroke=1)
        _text(c, left + x0 + 5, ry - row_h + 6.5, l1, size=7.5, color=MUTED)
        _text(c, left + x1 + 5, ry - row_h + 6.5, v1, font=BOLD, size=8)
        if l2:
            _text(c, left + x2 + 5, ry - row_h + 6.5, l2, size=7.5, color=MUTED)
            _text(c, left + x3 + 5, ry - row_h + 6.5, v2, font=BOLD, size=8)
    y -= len(info_rows) * row_h + 10

    metric_h = 36
    metric_cols = _cols([("", 1, "l")] * 4, CONTENT_W)
    metrics = [
        ("Net Asset Value:", money(stmt.end_nav), "Base Currency: SGD"),
        ("Assets in Transit", "0.00", None),
        ("Portfolio Value", money(stmt.portfolio), None),
        ("Cash Balance", money(stmt.cash_balance), None),
    ]
    for i, (k, v, sub) in enumerate(metrics):
        _, cx, cw, _ = metric_cols[i]
        c.setStrokeColor(GREY_BORDER)
        c.setLineWidth(0.6)
        c.rect(left + cx, y - metric_h, cw, metric_h, fill=0, stroke=1)
        _text(c, left + cx + 8, y - 12, k, size=7.2, color=MUTED)
        _text(c, left + cx + 8, y - 24, v, font=BOLD, size=11)
        if sub:
            _text(c, left + cx + 8, y - metric_h + 6, sub, size=6.3, color=MUTED)
    y -= metric_h + 16

    _text(c, left, y, "Changes in Net Asset Value", font=BOLD, size=11.5)
    y -= 18

    start_end = prev_month_end(stmt.period_end)
    start_fx = {code: fx_label(code, stmt.usd_start_fx) for code in CURRENCIES}
    end_fx = {code: fx_label(code, stmt.usd_end_fx) for code in CURRENCIES}
    y = _nav_block(c, f"Starting Net Asset Value {fmt_stamp(start_end)}", stmt.start_nav, stmt.start_parts, start_fx, y)
    y = _nav_block(c, f"Ending Net Asset Value {fmt_stamp(stmt.period_end)}", stmt.end_nav, stmt.end_parts, end_fx, y)

    ccy_cols = _cols([("", 1.4, "l")] + [("", 1, "r")] * 6, CONTENT_W)
    y = _section_head(c, "+ Changes in Cash", y, ccy_cols)
    cash_map = stmt.cash_change
    for label, key in (("Buy Amount", "buy"), ("Buy Fee", "buy_fee"), ("Sell Amount", "sell"),
                       ("Sell Fee", "sell_fee"), ("Corporate Action", "corp")):
        y = _ccy_row(c, label, cash_map[key], y, cols=ccy_cols)
    y = _ccy_row(c, "Total", cash_map["total"], y, cols=ccy_cols, bold=True)

    y = _section_head(c, "+ Changes in Position Value", y, ccy_cols)
    remaining_rows = int((y - MARGIN_BOTTOM) // 16)
    page1_holdings = stmt.holdings[:max(0, remaining_rows)]
    for h in page1_holdings:
        vals = {code: 0.0 for code in CURRENCIES}
        vals[h.currency] = h.change
        y = _ccy_row(c, f"{h.symbol} {h.name}", vals, y, cols=ccy_cols)

    return stmt.holdings[len(page1_holdings):]


def _draw_page2(c: pdfcanvas.Canvas, stmt: Statement, leftover: list[Holding], page: int, total: int) -> None:
    y = _page_chrome(c, stmt, page, total)
    left = MARGIN_LEFT
    ccy_cols = _cols([("", 1.4, "l")] + [("", 1, "r")] * 6, CONTENT_W)

    if leftover:
        for h in leftover:
            vals = {code: 0.0 for code in CURRENCIES}
            vals[h.currency] = h.change
            y = _ccy_row(c, f"{h.symbol} {h.name}", vals, y, cols=ccy_cols)
        y = _ccy_row(c, "Total", stmt.pos_change, y, cols=ccy_cols, bold=True)
        y = _section_head(c, "+ Changes in Assets in Transit", y, ccy_cols)
        y = _ccy_row(c, "Total", empty_ccy(), y, cols=ccy_cols)
        y -= 12

    _text(c, left, y, "Changes in Position Value", font=BOLD, size=11.5)
    y -= 18
    y = _position_table(c, stmt.holdings, "SGD", left, y)
    y -= 10
    y = _position_table(c, stmt.holdings, "USD", left, y)


POSITION_COLS_SPEC = [
    ("Symbol", 1.6, "l"), ("Exch.", 0.7, "l"), ("Ccy", 0.6, "l"),
    ("Start Qty", 0.9, "r"), ("Start Px", 0.9, "r"), ("Start Value", 1.0, "r"),
    ("End Qty", 0.9, "r"), ("End Px", 0.9, "r"), ("End Value", 1.0, "r"),
    ("Δ Value", 0.95, "r"), ("Buy Qty", 0.75, "r"), ("Sell Qty", 0.75, "r"),
    ("Xfer In", 0.7, "r"), ("Xfer Out", 0.7, "r"),
]


def _position_table(c: pdfcanvas.Canvas, holdings: list[Holding], currency: str, x0: float, top: float) -> float:
    cols = _cols(POSITION_COLS_SPEC, CONTENT_W)
    matching = [h for h in holdings if h.currency == currency]
    if not matching:
        return top
    _text(c, x0, top, "Securities", font=BOLD, size=8.5)
    top -= 14
    rows = []
    start_total = end_total = change_total = 0
    for h in matching:
        start_total += money_cents(h.start_value)
        end_total += money_cents(h.end_value)
        change_total += money_cents(h.change)
        rows.append([
            f"{h.name} {h.symbol}", h.exchange, h.currency,
            qty(h.start_qty) if h.start_qty else "0", price(h.start_px) if h.start_qty else "-", money(h.start_value),
            qty(h.end_qty) if h.end_qty else "0", price(h.end_px) if h.end_qty else "-", money(h.end_value),
            money(h.change, signed=True),
            f"+{qty(h.buy_qty)}" if h.buy_qty else "0", f"-{qty(h.sell_qty)}" if h.sell_qty else "0",
            "0", f"-{qty(h.transfer_out)}" if h.transfer_out else "0",
        ])
    rows.append([f"{currency} Total", "", "", "", "", money(from_cents(start_total)),
                 "", "", money(from_cents(end_total)), money(from_cents(change_total), signed=True),
                 "", "", "", ""])
    return _table(c, x0, top, cols, rows, total_row_idx=len(rows) - 1)


# --------------------------------------------------------------------------
# Page 3: Trades - Securities + Changes in Cash (SGD/USD)
# --------------------------------------------------------------------------

TRADE_COLS_SPEC = [
    ("Direction", 1.1, "l"), ("Symbol", 1.5, "l"), ("Exchange", 0.8, "l"),
    ("Capacity", 0.9, "l"), ("Ccy", 0.6, "l"), ("Date/Time", 1.1, "l"),
    ("Price", 0.8, "r"), ("Quantity", 0.8, "r"), ("Amount", 0.9, "r"),
]


def _fee_line(t: Trade, net: float) -> str:
    extras = []
    if t.direction.startswith("Sell") or t.sec:
        extras.append(f"SEC Fees: {money(t.sec)}")
    if t.direction.startswith("Sell") or t.taf:
        extras.append(f"Trading Activity Fees: {money(t.taf)}")
    extras.append("Consolidated Audit Trail Fees: 0.00")
    extras.append(f"Consumption Tax: {money(t.tax)}")
    return (f"Subtotal: {money(t.fee)}   Number of Transactions: 1   Transaction Amount: {money(t.amount)}   "
            f"Net Transaction Amount: {money(net, signed=True)}   Platform Fees: {money(t.platform)}   "
            f"Settlement Fees: {money(t.settlement)}   " + "   ".join(extras))


def _usd_total_line(kind: str, n: int, fee: int, amt: int, net: int, platform: int,
                     settlement: int, tax: int, sec: int, taf: int) -> str:
    extras = []
    if kind == "Sell" or sec:
        extras.append(f"SEC Fees: {money(from_cents(sec))}")
    if kind == "Sell" or taf:
        extras.append(f"Trading Activity Fees: {money(from_cents(taf))}")
    extras.append("Consolidated Audit Trail Fees: 0.00")
    extras.append(f"Consumption Tax: {money(from_cents(tax))}")
    return (f"Total of Transaction Fee: {money(from_cents(fee))}   Number of Transactions: {n}   "
            f"Number of Orders: {n}   Transactions Amount: {money(from_cents(amt))}   "
            f"Net Transactions Amount: {money(from_cents(net), signed=True)}   "
            f"Platform Fees: {money(from_cents(platform))}   Settlement Fees: {money(from_cents(settlement))}   "
            + "   ".join(extras))


def _draw_trades(c: pdfcanvas.Canvas, stmt: Statement, x0: float, top: float) -> float:
    cols = _cols(TRADE_COLS_SPEC, CONTENT_W)
    _text(c, x0, top, "Trades - Securities", font=BOLD, size=8.5)
    top -= 14
    if not stmt.trades:
        _text(c, x0, top, "No trades this period.", size=7.5, color=GREY_LABEL)
        return top - 16

    total_w = cols[-1][1] + cols[-1][2]
    header_h = 15
    c.setFillColor(GREY_FILL)
    c.rect(x0, top - header_h, total_w, header_h, fill=1, stroke=0)
    for label, cx, cw, align in cols:
        ax = x0 + cx + (cw - 3 if align == "right" else 3)
        _text(c, ax, top - header_h + 4.5, label, font=BOLD, size=7.2, color=MUTED, align=align)
    y = top - header_h

    buy = {"n": 0, "fee": 0, "amt": 0, "net": 0, "platform": 0, "settlement": 0, "tax": 0, "sec": 0, "taf": 0}
    sell = {"n": 0, "fee": 0, "amt": 0, "net": 0, "platform": 0, "settlement": 0, "tax": 0, "sec": 0, "taf": 0}
    for t in stmt.trades:
        when = t.when.strftime("%Y/%m/%d %H:%M:%S")
        if t.direction.startswith("Sell"):
            net = round_money(t.amount - t.fee)
            bucket = sell
        else:
            net = round_money(-(t.amount + t.fee))
            bucket = buy
        row_h = 14
        row = [t.direction, f"{t.holding.name} {t.holding.symbol}", t.venue, "Agency", "USD", when,
               price(t.price), qty(t.qty), money(t.amount)]
        for (label, cx, cw, align), val in zip(cols, row):
            ax = x0 + cx + (cw - 3 if align == "right" else 3)
            _text(c, ax, y - row_h + 4, val, size=7.2, align=align)
        y -= row_h
        note_h = 20
        _wrapped(c, _fee_line(t, net), x0 + 4, y - 6, total_w - 8, font=FONT, size=6.4, leading=8, color=MUTED)
        y -= note_h
        _hline(c, x0, x0 + total_w, y, color=GREY_ROW_BORDER)

        bucket["n"] += 1
        bucket["fee"] += money_cents(t.fee)
        bucket["amt"] += money_cents(t.amount)
        bucket["net"] += money_cents(net)
        bucket["platform"] += money_cents(t.platform)
        bucket["settlement"] += money_cents(t.settlement)
        bucket["tax"] += money_cents(t.tax)
        bucket["sec"] += money_cents(t.sec)
        bucket["taf"] += money_cents(t.taf)

    y -= 6
    if buy["n"] or sell["n"]:
        bar_h = 18
        entry_h = 24
        lines_needed = (1 if buy["n"] else 0) + (1 if sell["n"] else 0)
        box_h = bar_h + lines_needed * entry_h
        c.setStrokeColor(GREY_BORDER)
        c.setLineWidth(0.6)
        c.rect(x0, y - box_h, total_w, box_h, fill=0, stroke=1)
        c.setFillColor(colors.HexColor("#d9d9d9"))
        c.rect(x0, y - bar_h, total_w, bar_h, fill=1, stroke=0)
        _text(c, x0 + 10, y - bar_h + 6, "USD Total", font=BOLD, size=9.5)
        ly = y - bar_h
        if buy["n"]:
            c.setFillColor(BADGE_BUY)
            c.roundRect(x0 + 8, ly - 17, 42, 14, 6, fill=1, stroke=0)
            _text(c, x0 + 29, ly - 12.5, "Buy", font=BOLD, size=7, color=WHITE, align="center")
            _wrapped(c, _usd_total_line("Buy", buy["n"], buy["fee"], buy["amt"], buy["net"],
                                         buy["platform"], buy["settlement"], buy["tax"], buy["sec"], buy["taf"]),
                     x0 + 58, ly - 7, total_w - 66, size=6.8, leading=8.6, color=colors.HexColor("#444444"))
            ly -= entry_h
        if sell["n"]:
            c.setFillColor(BADGE_SELL)
            c.roundRect(x0 + 8, ly - 17, 42, 14, 6, fill=1, stroke=0)
            _text(c, x0 + 29, ly - 12.5, "Sell", font=BOLD, size=7, color=WHITE, align="center")
            _wrapped(c, _usd_total_line("Sell", sell["n"], sell["fee"], sell["amt"], sell["net"],
                                         sell["platform"], sell["settlement"], sell["tax"], sell["sec"], sell["taf"]),
                     x0 + 58, ly - 7, total_w - 66, size=6.8, leading=8.6, color=colors.HexColor("#444444"))
            ly -= entry_h
        y -= box_h
    return y - 12


def _cash_panel(c: pdfcanvas.Canvas, code: str, stmt: Statement, x0: float, top: float, width: float,
                *, compact: bool = False) -> float:
    start = stmt.cash_start[code]
    end = stmt.cash_end[code]
    events = stmt.cash_events.get(code, [])
    box_w = 190 if not compact else 175
    ledger_x = x0 + box_w + 12
    ledger_w = width - box_w - 12

    row_h = 15 if compact else 17
    box_head_h = 16
    box_h = box_head_h + row_h * 4

    c.setStrokeColor(GREY_BORDER)
    c.setLineWidth(0.6)
    c.rect(x0, top - box_h, box_w, box_h, fill=0, stroke=1)
    c.setFillColor(GREY_FILL)
    c.rect(x0, top - box_head_h, box_w, box_head_h, fill=1, stroke=0)
    _text(c, x0 + 8, top - box_head_h + 5, code, font=BOLD, size=8)
    by = top - box_head_h
    for label, val, muted in (("Starting Cash", money(start), False), ("Ending Cash", money(end), False),
                              ("Ending Settled Cash", money(end), True), ("Ending Unsettled Cash", "0.00", True)):
        _text(c, x0 + 8, by - row_h + 5, label, size=7.5, color=MUTED if muted else INK)
        _text(c, x0 + box_w - 8, by - row_h + 5, val, size=7.5, color=MUTED if muted else INK, align="right")
        by -= row_h

    ledger_cols = _cols([("Date/Time", 1.6, "l"), ("Type", 1.1, "l"), ("Amount", 0.8, "r"), ("Comment", 2.2, "l")], ledger_w)
    header_h = 15
    c.setStrokeColor(GREY_BORDER)
    c.setLineWidth(0.6)
    total_lh = header_h + max(1, len(events) or 1) * row_h
    c.rect(ledger_x, top - total_lh, ledger_w, total_lh, fill=0, stroke=1)
    c.setFillColor(GREY_FILL)
    c.rect(ledger_x, top - header_h, ledger_w, header_h, fill=1, stroke=0)
    for label, cx, cw, align in ledger_cols:
        ax = ledger_x + cx + (cw - 3 if align == "right" else 3)
        _text(c, ax, top - header_h + 4.5, label, font=BOLD, size=7.2, color=MUTED, align="left" if align == "l" else align)
    ly = top - header_h
    total = 0
    if events:
        for ev in events:
            total += money_cents(ev.amount)
            for (label, cx, cw, align), val in zip(ledger_cols, [ev.when, ev.type, money(ev.amount, signed=True), ev.comment]):
                ax = ledger_x + cx + (cw - 3 if align == "r" else 3)
                _text(c, ax, ly - row_h + 5, val, size=7.2, align="right" if align == "r" else "left")
            ly -= row_h
            _hline(c, ledger_x, ledger_x + ledger_w, ly, color=GREY_ROW_BORDER)
        _text(c, ledger_x + 4, ly - row_h + 5, f"{code} Total", font=BOLD, size=7.2)
        _text(c, ledger_x + ledger_cols[2][1] + ledger_cols[2][2] - 3, ly - row_h + 5,
              money(from_cents(total), signed=True), font=BOLD, size=7.2, align="right")
        ly -= row_h
    else:
        _text(c, ledger_x + 4, ly - row_h + 5, f"{code} Total", font=BOLD, size=7.2)
        _text(c, ledger_x + ledger_cols[2][1] + ledger_cols[2][2] - 3, ly - row_h + 5, "0.00", font=BOLD, size=7.2, align="right")
        ly -= row_h

    return min(top - box_h, ly) - (10 if not compact else 8)


def _draw_page3(c: pdfcanvas.Canvas, stmt: Statement, page: int, total: int) -> None:
    y = _page_chrome(c, stmt, page, total)
    y = _draw_trades(c, stmt, MARGIN_LEFT, y)
    _text(c, MARGIN_LEFT, y, "Changes in Cash", font=BOLD, size=11.5)
    y -= 18
    y = _cash_panel(c, "SGD", stmt, MARGIN_LEFT, y, CONTENT_W)
    _cash_panel(c, "USD", stmt, MARGIN_LEFT, y, CONTENT_W)


def _draw_page4(c: pdfcanvas.Canvas, stmt: Statement, page: int, total: int) -> None:
    y = _page_chrome(c, stmt, page, total)
    for code in ("HKD", "CNH", "JPY", "KRW"):
        y = _cash_panel(c, code, stmt, MARGIN_LEFT, y, CONTENT_W, compact=True)


# --------------------------------------------------------------------------
# Page 5: Ending Positions
# --------------------------------------------------------------------------

ENDING_COLS_SPEC = [
    ("Symbol", 1.7, "l"), ("Exch.", 0.7, "l"), ("Ccy", 0.6, "l"),
    ("Settled Qty", 0.9, "r"), ("Unsettled Qty", 0.95, "r"), ("Quantity", 0.85, "r"),
    ("Mult.", 0.55, "r"), ("Closing Px", 0.85, "r"), ("Market Value", 0.95, "r"),
    ("FX Rate", 0.85, "r"), ("Convert to SGD", 1.0, "r"),
]


def _ending_table(c: pdfcanvas.Canvas, holdings: list[Holding], currency: str, fx: float, x0: float, top: float) -> float:
    cols = _cols(ENDING_COLS_SPEC, CONTENT_W)
    matching = [h for h in holdings if h.currency == currency and h.end_qty > 0]
    if not matching:
        return top
    _text(c, x0, top, "Securities", font=BOLD, size=8.5)
    top -= 14
    rows = []
    total = 0
    for h in matching:
        sgd = convert_sgd(h.end_value, fx)
        total += money_cents(sgd)
        rows.append([f"{h.name} {h.symbol}", h.exchange, h.currency, qty(h.end_qty), "0", qty(h.end_qty),
                     "1", price(h.end_px), money(h.end_value), f"{fx:.6f}", money(sgd)])
    rows.append([f"Total (Number: {len(matching)})", "", "", "", "", "", "", "", "", "", money(from_cents(total))])
    return _table(c, x0, top, cols, rows, total_row_idx=len(rows) - 1)


def _draw_page5(c: pdfcanvas.Canvas, stmt: Statement, page: int, total: int) -> None:
    y = _page_chrome(c, stmt, page, total)
    _text(c, MARGIN_LEFT, y, "Ending Positions", font=BOLD, size=11.5)
    y -= 18
    y = _ending_table(c, stmt.holdings, "SGD", 1.0, MARGIN_LEFT, y)
    y -= 10
    _ending_table(c, stmt.holdings, "USD", stmt.usd_end_fx, MARGIN_LEFT, y)


# --------------------------------------------------------------------------
# Page 6: Important Notice
# --------------------------------------------------------------------------

NOTICE_ITEMS = [
    "Confirmation for transactions are processed by Moomoo Financial Singapore Pte. Ltd. (\"Moomoo Singapore\") as a licensed broker-dealer and custodian in Singapore. Unless otherwise stated, the above transactions are executed by Moomoo Singapore in the name of an agent.",
    "Securities are held on behalf by Moomoo Singapore and/or its appointed custodian.",
    "Please promptly report any inaccuracy or discrepancy found in this statement to Moomoo Singapore Customer Service Department at clientservice@sg.moomoo.com within 14 calendar days from the date of this document.",
    "If this is a margin account, interest charged on the debit balance in your account and/or the amount you borrowed is shown on this statement. The margin interest rates may change from time to time without prior notice.",
    "The closing prices on this document are unaudited and may come from third-party sources. Moomoo Singapore does not warrant the accuracy of the prices.",
    "Year to date income and realized gain/loss figures in this statement are for informational purposes only and should not be used for income tax purposes.",
    "For additional information about your account, see \"Terms & Policies\" at www.moomoo.com/sg .",
    "For information on fees, see our pricing structure at www.moomoo.com/sg/broker-about/pricing .",
    "Moomoo Singapore is a member of the Financial Institutions Dispute Resolution Centre (FIDReC). Unresolved issues can be brought up to FIDReC at www.fidrec.com.sg for mediation.",
    "The account name can be changed at the client's discretion. The account name should not be used for any legal verification.",
]


def _draw_page6(c: pdfcanvas.Canvas, stmt: Statement, page: int, total: int) -> None:
    y = _page_chrome(c, stmt, page, total)
    left = MARGIN_LEFT
    _text(c, left, y, "Important Notice", font=BOLD, size=13)
    y -= 20
    for i, item in enumerate(NOTICE_ITEMS, start=1):
        y = _wrapped(c, f"{i}. {item}", left, y, CONTENT_W, size=8, leading=11, color=colors.HexColor("#333333"))
        y -= 5
    y -= 8
    c.setStrokeColor(LEGAL_RULE)
    c.setLineWidth(1)
    c.line(left, y, left + CONTENT_W * 0.38, y)
    y -= 14
    _text(c, left, y, "Moomoo Financial Singapore Pte. Ltd.", font=BOLD, size=10.5)
    y -= 15
    y = _wrapped(c, ("Moomoo Singapore is a CMS and MPI licence holder with EFA status licensed by the "
                     "Monetary Authority of Singapore.\nwww.moomoo.com/sg\n10 Marina Boulevard MBFC Tower 2 #31-01, "
                     "Singapore 018983\nGST Reg No: 201942579C").replace("\n", "  |  "),
                 left, y, CONTENT_W, size=8.5, leading=12)


# --------------------------------------------------------------------------
# PDF assembly
# --------------------------------------------------------------------------

def _save_pdf(output_path: str, draw_fn, *, synthetic_tag: bool = True, producer: str = "Moomoo eStatements") -> None:
    tmp_path = Path(output_path).with_suffix(".tmp.pdf")
    c = pdfcanvas.Canvas(str(tmp_path), pagesize=(PAGE_W, PAGE_H))
    draw_fn(c)
    c.save()

    reader = PdfReader(str(tmp_path))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    creation = datetime.now().strftime("D:%Y%m%d%H%M%S")
    meta = {"/Producer": producer, "/Creator": producer, "/CreationDate": creation}
    if synthetic_tag:
        meta["/Subject"] = "SYNTHETIC-TEST-DATA"
        meta["/Keywords"] = "SYNTHETIC-TEST-DATA"
    writer.add_metadata(meta)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as handle:
        writer.write(handle)
    tmp_path.unlink(missing_ok=True)


def generate_investment_statement(output_path: str, stmt: Statement, *, synthetic_tag: bool = True) -> dict:
    verify(stmt)
    total_pages = 6

    def draw(c: pdfcanvas.Canvas) -> None:
        leftover = _draw_page1(c, stmt, 1, total_pages)
        c.showPage()
        _draw_page2(c, stmt, leftover, 2, total_pages)
        c.showPage()
        _draw_page3(c, stmt, 3, total_pages)
        c.showPage()
        _draw_page4(c, stmt, 4, total_pages)
        c.showPage()
        _draw_page5(c, stmt, 5, total_pages)
        c.showPage()
        _draw_page6(c, stmt, 6, total_pages)

    _save_pdf(output_path, draw, synthetic_tag=synthetic_tag)
    return {"output_path": output_path, "name": stmt.name, "account_number": stmt.account_number, "nav": stmt.end_nav}


# --------------------------------------------------------------------------
# Mastersheet-driven persona -- same idea as build_investment_html() in the
# old generate_optional_docs.py: build a statement off the seeded rng, then
# override identity/address from the mastersheet row.
# --------------------------------------------------------------------------

DOC_YEAR = 2026
DOC_MONTH = 8  # statements dated August 2026, same anchor the HTML pipeline used

SOW_TYPES = {
    "Source of Wealth (Individual) - Director & UBO",
    "Source of Wealth (Individual) - Shareholder",
}

_POSTAL_RE = re.compile(r"(?:Singapore\s*)?(\d{6})\s*$", re.I)


def split_address(raw: str) -> tuple[str, str, str]:
    parts = [p.strip() for p in (raw or "").split(",") if p.strip()]
    postal = ""
    kept: list[str] = []
    for part in parts:
        match = _POSTAL_RE.search(part)
        if match and not postal:
            postal = match.group(1)
            remainder = _POSTAL_RE.sub("", part).strip()
            remainder = re.sub(r"Singapore\s*$", "", remainder, flags=re.I).strip()
            if remainder:
                kept.append(remainder)
        else:
            kept.append(part)
    street = kept[0] if kept else (raw or "").strip()
    unit = ", ".join(kept[1:]) if len(kept) > 1 else ""
    return street, unit, postal


def _month_end(year: int, month: int) -> date:
    return date(year, month, monthrange(year, month)[1])


def build_persona(row: dict[str, Any]) -> dict:
    case_id = str(row.get("Case ID") or "")
    filename = str(row.get("Filename") or "")
    rng = _case_seeded_rng(f"{case_id}|{filename}|investment")
    stmt = build_statement(rng, period_end=_month_end(DOC_YEAR, DOC_MONTH))

    individual_name = str(row.get("Individual Name (auto)") or "").strip()
    home = str(row.get("Residential Address (auto)") or "").strip()
    fallback = str(row.get("Company Address (auto)") or "")
    street, unit, postal = split_address(home or fallback)

    if individual_name:
        stmt.name = individual_name.upper()
    stmt.address = make_address(street, unit, postal or "000000")
    return dict(stmt=stmt)


def _rows(ws) -> list[dict[str, Any]]:
    header = [c.value for c in ws[1]]
    return [dict(zip(header, r)) for r in ws.iter_rows(min_row=2, values_only=True) if any(r)]


def load_cases(xlsx: str | Path, cases: list[str] | None = None) -> list[dict[str, Any]]:
    wb = openpyxl.load_workbook(str(xlsx), data_only=True)
    ws = wb["Optional Documents"]
    out = []
    for row in _rows(ws):
        if str(row.get("Document Type") or "").strip() not in SOW_TYPES:
            continue
        case_id = str(row.get("Case ID") or "")
        if cases and case_id not in cases:
            continue
        out.append(row)
    return out


def generate(xlsx: str | Path, outdir: str | Path, cases: list[str] | None = None,
             synthetic_tag: bool = True) -> list[Path]:
    written = []
    for row in load_cases(xlsx, cases):
        filename = str(row.get("Filename") or f"{row.get('Case ID')}_InvestmentStatement.pdf")
        path = Path(outdir) / filename
        persona = build_persona(row)
        generate_investment_statement(str(path), synthetic_tag=synthetic_tag, **persona)
        written.append(path)
        print(f"{row.get('Case ID')}  {filename}  {row.get('Individual Name (auto)')}")
    return written


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Moomoo-style monthly securities statement PDFs.")
    parser.add_argument("xlsx", nargs="?", help="Mastersheet path")
    parser.add_argument("--outdir", "-o", type=Path, default=ASSETS / "output", help="Output directory")
    parser.add_argument("--cases", nargs="*", help="Case IDs to include; default is all")
    parser.add_argument("--no-synthetic-tag", action="store_true", help="omit the SYNTHETIC-TEST-DATA metadata tag")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.xlsx:
        persona = build_persona({
            "Case ID": "DEMO",
            "Filename": "statement_example.pdf",
            "Individual Name (auto)": "Tan Wei Ming",
            "Residential Address (auto)": "18 Ardmore Park, #15-02, Singapore 259960",
        })
        print(generate_investment_statement(str(DEFAULT_OUT), **persona))
        return
    args.outdir.mkdir(parents=True, exist_ok=True)
    written = generate(args.xlsx, args.outdir, cases=args.cases, synthetic_tag=not args.no_synthetic_tag)
    print(f"Wrote {len(written)} investment statement PDF(s) to {args.outdir}")


if __name__ == "__main__":
    main()
