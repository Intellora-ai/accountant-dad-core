"""Child 14 — the web app, Slice 1.

Runs against FakeTally. NOTHING here touches real Tally, because there is no
Tally on this machine. Swap the client for the real connector when the VM
exists; no other code changes. That is what the TallyClient boundary is for.

Stdlib only. No framework, no build step, no install.
"""

from __future__ import annotations

import datetime
import html
import json
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

from accountant import pipeline
from accountant import questions as Q
from accountant.extract.adapter import TypedTextExtractor
from accountant.memory.index import MemoryIndex
from accountant.schema import Outcome, Voucher
from accountant.tallyio.client import operation_id_in
from accountant.tallyio.fake import FakeTally

COMPANY = "Demo Traders Pvt Ltd"
ACCOUNTS = (
    "Purchases",
    "Repairs & Maintenance",
    "Sundry Expenses",
    "Printing & Stationery",
    "Rent",
    "Electricity Charges",
    "Cash",
    "Bank",
)


def seed() -> FakeTally:
    """A demo company with real-shaped history.

    Sharma Traders is consistent -> known vendor, posts straight through.
    Verma Cement is inconsistent -> conflicted, asks which account.
    Gupta Hardware is absent      -> unseen, asks.
    """
    hist: list[Voucher] = []

    def add(party: str, account: str, amount: int, n: int, note: str) -> None:
        for i in range(n):
            hist.append(
                Voucher(
                    id=f"h{len(hist)}",
                    date=datetime.date(2026, 1 + (i % 6), 1 + (i % 27)),
                    party=party,
                    narration=note,
                    debit_account=account,
                    credit_account="Cash",
                    amount_paise=amount + i * 1000,
                )
            )

    add("Sharma Traders", "Purchases", 380000, 40, "cement supply")
    add("Verma Cement", "Purchases", 250000, 6, "cement")
    add("Verma Cement", "Repairs & Maintenance", 90000, 4, "site repair")
    add("Kumar Stationers", "Printing & Stationery", 45000, 12, "office supplies")
    add("City Power Board", "Electricity Charges", 720000, 12, "monthly power")
    add("Landlord", "Rent", 2000000, 12, "monthly rent")

    t = FakeTally()
    t.add_company(COMPANY, accounts=ACCOUNTS, vouchers=tuple(hist), backed_up=True)
    return t


TALLY = seed()
DRAFTS: dict[str, pipeline.Draft] = {}
EVENTS: list[tuple[str, str]] = []


def log(kind: str, msg: str) -> None:
    EVENTS.insert(0, (kind, msg))
    del EVENTS[40:]


# ---- rendering --------------------------------------------------------------

CSS = """
:root{color-scheme:light dark}
*{box-sizing:border-box}
body{font:15px/1.55 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
max-width:880px;margin:0 auto;padding:24px 20px 64px}
h1{font-size:20px;margin:0 0 2px}
h2{font-size:14px;text-transform:uppercase;letter-spacing:.06em;opacity:.6;
margin:28px 0 10px;font-weight:600}
.sub{opacity:.6;font-size:13px;margin:0 0 18px}
.warn{border:1px solid #b45309;background:#b4530915;padding:10px 12px;
border-radius:8px;font-size:13px;margin:0 0 22px}
form.entry{display:flex;gap:8px;margin:0 0 6px}
input[type=text]{flex:1;padding:11px 13px;font:inherit;border-radius:8px;
border:1px solid #8884}
button{padding:11px 16px;font:inherit;font-weight:600;border-radius:8px;
border:1px solid #8884;cursor:pointer;background:#8881}
button.primary{background:#2563eb;color:#fff;border-color:#2563eb}
.hint{font-size:12px;opacity:.55;margin:0 0 8px}
.card{border:1px solid #8883;border-radius:10px;padding:14px 16px;margin:0 0 12px}
.valid{border-left:4px solid #16a34a}
.unclear{border-left:4px solid #d97706}
.notvalid{border-left:4px solid #dc2626}
.badge{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.06em;
padding:2px 8px;border-radius:999px;text-transform:uppercase}
.b-valid{background:#16a34a22;color:#16a34a}
.b-unclear{background:#d9770622;color:#d97706}
.b-notvalid{background:#dc262622;color:#dc2626}
table{border-collapse:collapse;width:100%;font-size:13px}
td,th{text-align:left;padding:6px 10px 6px 0;border-bottom:1px solid #8882}
th{opacity:.55;font-weight:600;font-size:11px;text-transform:uppercase}
.num{text-align:right;font-variant-numeric:tabular-nums}
code{font:12px ui-monospace,SFMono-Regular,Menlo,monospace;background:#8881;
padding:1px 5px;border-radius:4px}
.reason{font-size:13px;opacity:.85;margin:6px 0 0}
.opts{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0 0}
.ask{font-size:17px;font-weight:600;margin:10px 0 0;line-height:1.4}
.ev{font-size:12.5px;padding:5px 0;border-bottom:1px solid #8882}
.muted{opacity:.55}
"""


def rupees(paise: int) -> str:
    return f"{paise // 100:,}.{paise % 100:02d}"


def esc(s: object) -> str:
    return html.escape(str(s))


def page(body: str) -> bytes:
    return f"""<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Accountant Dad</title><style>{CSS}</style>
<h1>Accountant Dad</h1>
<p class=sub>{esc(COMPANY)} &middot; posting into Tally</p>
<div class=warn><b>Demo mode.</b> This is talking to a <b>fake Tally</b> running in
memory, not real accounting software. Nothing here touches any real books.</div>
{body}""".encode()


def render_decision(d: pipeline.Draft) -> str:
    out = d.outcome
    cls = {"valid": "valid", "unclear": "unclear", "not_valid": "notvalid"}[out.value]
    badge = {
        "valid": "posted",
        "unclear": "needs an answer",
        "not_valid": "not posted",
    }[out.value]
    v = d.voucher

    rows = "".join(
        f"<tr><td>{esc(k)}</td><td>{esc(val)}</td></tr>"
        for k, val in [
            ("Party", v.party or "—"),
            ("Debit", v.debit_account or "—"),
            ("Credit", v.credit_account),
            ("Amount", f"₹{rupees(v.amount_paise)}"),
            ("GST", f"₹{rupees(v.gst_paise)}" if v.gst_paise else "—"),
            ("Date", v.date),
        ]
    )

    prov = "".join(
        f"<tr><td>{esc(k)}</td><td><code>{esc(s)}</code></td></tr>"
        for k, s in sorted((d.voucher.provenance or {}).items())
    )

    flags = "".join(
        f"<p class=reason>&#9873; <b>{esc(f.detector)}</b> — {esc(f.reason)}</p>"
        for f in d.flags
    )

    ask = ""
    if out is Outcome.UNCLEAR:
        q = pipeline.next_question(d)
        if q is not None:
            buttons = "".join(
                f'<form method=post action=/answer style="display:inline">'
                f'<input type=hidden name=draft value="{esc(d.id)}">'
                f'<input type=hidden name=problem value="{esc(q.problem_id)}">'
                f'<input type=hidden name=value value="{esc(a.value)}">'
                f"<button>{esc(a.label)}</button></form>"
                for a in q.answers
            )
            asked = len(d.answers)
            left = Q.QUESTION_CAP - asked
            ask = (
                f"<p class=ask>{esc(q.text)}</p><div class=opts>{buttons}</div>"
                f"<p class=hint>question {asked + 1} of at most {Q.QUESTION_CAP}"
                f" &middot; {left} left before I save it for you</p>"
            )

    posted = ""
    if d.posted_tally_id:
        posted = (
            f"<p class=reason>Written to Tally as "
            f"<code>{esc(d.posted_tally_id)}</code> "
            f"&middot; operation <code>{esc(d.operation_id)}</code></p>"
            f"<form method=post action=/reverse><input type=hidden name=op "
            f'value="{esc(d.operation_id)}"><button>Undo this entry</button></form>'
        )

    checks_failed = [c for c in d.checks if not c.passed]
    checks_line = (
        f"<p class=reason class=muted>{len(d.checks)} checks run, "
        f"{len(checks_failed)} failed</p>"
    )

    return f"""<div class="card {cls}">
<span class="badge b-{cls}">{badge}</span>
<p class=reason>{esc(d.reason)}</p>
{flags}{ask}{posted}
<h2>Voucher</h2><table>{rows}</table>
<h2>Where each field came from</h2><table>{prov}</table>
{checks_line}
</div>"""


def render_home(banner: str = "") -> bytes:
    ours = TALLY.list_our_vouchers(COMPANY)
    tb = TALLY.trial_balance(COMPANY)

    posted_rows = (
        "".join(
            f"<tr><td>{esc(v.party)}</td><td>{esc(v.debit_account)}</td>"
            f"<td class=num>₹{rupees(v.amount_paise)}</td>"
            f"<td><code>{esc(operation_id_in(v.narration))}</code></td></tr>"
            for v in ours
        )
        or "<tr><td colspan=4 class=muted>nothing posted yet</td></tr>"
    )

    tb_rows = "".join(
        f"<tr><td>{esc(k)}</td><td class=num>₹{rupees(abs(val))} "
        f"{'Dr' if val > 0 else 'Cr'}</td></tr>"
        for k, val in sorted(tb.items())
    )

    events = (
        "".join(f"<div class=ev>{esc(m)}</div>" for _, m in EVENTS)
        or '<div class="ev muted">nothing yet</div>'
    )

    return page(f"""{banner}
<form class=entry method=post action=/entry>
<input type=text name=text autofocus
 placeholder="paid Sharma Traders 4200 for cement including 18% GST">
<button class=primary>Send</button></form>
<p class=hint>Try: <b>paid Sharma Traders 4200 for cement</b>
(known, posts straight through)
&middot; <b>paid Verma Cement 900 for bags</b> (used two accounts, asks)
&middot; <b>paid Gupta Hardware 1500 for tools</b> (never seen, asks)</p>

<h2>What we posted</h2>
<table><tr><th>Party<th>Account<th class=num>Amount<th>Operation</tr>
{posted_rows}</table>

<h2>Trial balance</h2>
<table>{tb_rows}</table>

<h2>Activity</h2>{events}""")


# ---- server -----------------------------------------------------------------


def _run(text: str) -> pipeline.Draft:
    accounts = TALLY.read_accounts(COMPANY)
    history = TALLY.read_vouchers(COMPANY)
    index = MemoryIndex.from_vouchers(history)
    d = pipeline.build_draft(
        COMPANY, text.encode(), "text/plain", TypedTextExtractor(), accounts, index
    )
    d = pipeline.evaluate(d, accounts, history, index)
    if d.outcome is Outcome.VALID:
        d = pipeline.post(d, TALLY)
        log(
            "post",
            f"posted {d.voucher.party} ₹{rupees(d.voucher.amount_paise)} "
            f"to {d.voucher.debit_account}",
        )
    elif d.outcome is Outcome.UNCLEAR:
        log("ask", f"asked about {d.voucher.party or 'unknown party'}")
    else:
        log("block", f"refused: {d.reason}")
    DRAFTS[d.id] = d
    return d


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _form(self) -> dict[str, str]:
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n).decode()
        return {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}

    def log_message(self, format: str, *args: object) -> None:  # quiet
        pass

    def do_GET(self) -> None:
        if self.path.startswith("/health"):
            self._send(json.dumps({"ok": True, "company": COMPANY}).encode())
            return
        self._send(render_home())

    def do_POST(self) -> None:
        form = self._form()

        if self.path == "/entry":
            text = (form.get("text") or "").strip()
            if not text:
                self._send(render_home())
                return
            d = _run(text)
            self._send(page(render_decision(d) + '<p><a href="/">&larr; back</a></p>'))
            return

        if self.path == "/answer":
            d = DRAFTS.get(form.get("draft", ""))
            if d is None:
                self._send(render_home("<div class=warn>draft expired</div>"))
                return
            value = form.get("value", "")
            problem = form.get("problem", "which_account")

            accounts = TALLY.read_accounts(COMPANY)
            history = TALLY.read_vouchers(COMPANY)
            index = MemoryIndex.from_vouchers(history)

            if value == Q.HANDOVER:
                d.answers.extend((f"gave_up_{i}", "") for i in range(Q.QUESTION_CAP))
                log("saved", f"saved {d.voucher.party or 'entry'} for you to finish")
            elif value in (Q.YES,):
                d.answers.append((problem, "yes"))
            elif value == Q.RETYPE:
                log("retype", "asked to type it again")
                self._send(
                    render_home(
                        "<div class=warn>Type it again with the right numbers.</div>"
                    )
                )
                return
            else:
                d = pipeline.answer(d, value, problem_id=problem)
                index.record(d.voucher.party, value)  # learn it

            d = pipeline.evaluate(d, accounts, history, index)

            if d.outcome is Outcome.VALID:
                d = pipeline.post(d, TALLY)
                log("post", f"answered {d.voucher.party}, posted")
            else:
                log("block", f"answer did not clear it: {d.reason}")
            DRAFTS[d.id] = d
            self._send(page(render_decision(d) + '<p><a href="/">&larr; back</a></p>'))
            return

        if self.path == "/reverse":
            op = form.get("op", "")
            ok = TALLY.reverse_by_operation_id(COMPANY, op)
            log("undo", f"reversed {op}" if ok else f"nothing to reverse for {op}")
            self._send(render_home())
            return

        self._send(render_home(), 404)


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    print(f"Accountant Dad (demo, fake Tally) -> http://{host}:{port}")
    HTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    serve()
