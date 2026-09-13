"""A small French web interface, served from the standard library.

No framework, no build step, no dependency: ``python3 -m foot web`` starts a
local server and the whole journey — paste the matches, pick the date and
timezone, add prices if you have them, read a card per match and one summary
table — happens in the browser.

The page is deliberately plain.  Its job is to make the analysis reachable, not
to be admired, and every number it shows comes from the same engine the command
line uses, so the two can never drift apart.
"""

from __future__ import annotations

import datetime as dt
import html
import http.server
import socketserver
import urllib.parse
from collections.abc import Callable

from foot.analysis.engine import AnalysisRun, Engine
from foot.analysis.request import DEFAULT_TIMEZONE, resolve_timezone
from foot.markets.portfolio import build_ticket, plan_stakes
from foot.report.card import render_card, render_rubric_grid
from foot.report.table import render_summary

__all__ = ["build_page", "serve"]

_STYLE = """
:root { color-scheme: light dark; --bg:#fbfbfa; --fg:#1a1a18; --muted:#6b6b66;
        --line:#dcdcd6; --accent:#0b6b4f; --warn:#8a4b00; --card:#ffffff; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#16161a; --fg:#e9e9e4; --muted:#9a9a93; --line:#33333a;
          --accent:#4fd1a5; --warn:#e0a458; --card:#1e1e24; }
}
* { box-sizing: border-box; }
body { margin:0; padding:0 16px; background:var(--bg); color:var(--fg);
       font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
.wrap { max-width: 1080px; margin: 0 auto; padding-block: 28px 60px; }
h1 { font-size:1.5rem; margin:0 0 4px; letter-spacing:-0.01em; }
.sub { color:var(--muted); margin:0 0 26px; font-size:0.92rem; }
form { background:var(--card); border:1px solid var(--line); border-radius:10px;
       padding:20px; margin-bottom:26px; }
label { display:block; font-weight:600; font-size:0.86rem; margin:14px 0 5px; }
label:first-of-type { margin-top:0; }
textarea, input, select { width:100%; padding:9px 11px; border:1px solid var(--line);
       border-radius:7px; background:var(--bg); color:var(--fg); font-size:0.93rem;
       font-family:inherit; }
textarea { min-height:130px; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
.row { display:flex; gap:14px; flex-wrap:wrap; }
.row > div { flex:1 1 200px; min-width:0; }
button { margin-top:18px; padding:11px 26px; border:0; border-radius:7px;
         background:var(--accent); color:#fff; font-size:0.97rem; font-weight:600;
         cursor:pointer; }
button:hover { filter:brightness(1.08); }
.hint { color:var(--muted); font-size:0.82rem; margin-top:5px; }
pre { background:var(--card); border:1px solid var(--line); border-radius:10px;
      padding:16px; overflow-x:auto; font-size:12.5px; line-height:1.5;
      font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
h2 { font-size:1.1rem; margin:30px 0 10px; padding-bottom:6px;
     border-bottom:1px solid var(--line); }
.note { background:var(--card); border-left:3px solid var(--warn); padding:12px 15px;
        border-radius:0 7px 7px 0; margin:16px 0; font-size:0.88rem; }
details { margin:10px 0; }
summary { cursor:pointer; font-weight:600; font-size:0.9rem; padding:6px 0; }
"""

_FORM = """
<form method="post" action="/">
  <label for="matchs">Rencontres à analyser — une par ligne</label>
  <textarea id="matchs" name="matchs" placeholder="{placeholder}">{matchs}</textarea>
  <p class="hint">Formats acceptés&nbsp;: <code>Arsenal - Chelsea</code> ·
    <code>Premier League: Man Utd vs Liverpool 20/09/2026 17:30</code> ·
    <code>it.1 | Inter - Milan | 20/09/2026 20:45 | 1.95 3.50 4.20</code>
    (les trois cotes sont 1&nbsp;/&nbsp;N&nbsp;/&nbsp;2).</p>
  <div class="row">
    <div>
      <label for="date">Date et heure de l'analyse (as_of)</label>
      <input type="datetime-local" id="date" name="date" value="{date}">
    </div>
    <div>
      <label for="tz">Fuseau horaire</label>
      <select id="tz" name="tz">{zones}</select>
    </div>
    <div>
      <label for="book">Bookmaker (facultatif)</label>
      <input type="text" id="book" name="book" value="{book}" placeholder="ex. Pinnacle">
    </div>
  </div>
  <div class="row">
    <div>
      <label for="budget">Budget pour les mises (facultatif)</label>
      <input type="number" step="any" min="0" id="budget" name="budget" value="{budget}"
             placeholder="laisser vide : aucune mise proposée">
      <p class="hint">Aucun capital n'est supposé&nbsp;:
        sans budget, aucune mise n'est chiffrée.</p>
    </div>
    <div>
      <label for="combine">Combiné</label>
      <select id="combine" name="combine">
        <option value="non"{c_non}>Non — paris simples uniquement</option>
        <option value="oui"{c_oui}>Oui — le plus court qui reste pertinent</option>
      </select>
    </div>
  </div>
  <button type="submit">Analyser</button>
</form>
"""

_PLACEHOLDER = (
    "Premier League: Manchester United - Manchester City 14/09/2026 17:30 @ 2.55 3.55 2.65\n"
    "it.1 | Napoli - Bologna | 13/09/2026 20:45 | 1.62 4.00 5.50\n"
    "Levante - Barcelona"
)

_COMMON_ZONES = (
    "Europe/Paris", "Europe/London", "Europe/Madrid", "Europe/Rome",
    "Europe/Berlin", "Europe/Lisbon", "UTC", "America/New_York",
)


def _zone_options(selected: str) -> str:
    return "".join(
        f'<option value="{html.escape(zone)}"'
        f'{" selected" if zone == selected else ""}>{html.escape(zone)}</option>'
        for zone in _COMMON_ZONES
    )


def build_page(
    *,
    matchs: str = "",
    date: str = "",
    timezone: str = DEFAULT_TIMEZONE,
    bookmaker: str = "",
    budget: str = "",
    combine: str = "non",
    body: str = "",
) -> str:
    """Render the whole single-page interface."""
    form = _FORM.format(
        matchs=html.escape(matchs),
        placeholder=html.escape(_PLACEHOLDER),
        date=html.escape(date),
        zones=_zone_options(timezone),
        book=html.escape(bookmaker),
        budget=html.escape(budget),
        c_non=" selected" if combine != "oui" else "",
        c_oui=" selected" if combine == "oui" else "",
    )
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>foot — analyse de rencontres</title><style>{_STYLE}</style></head>
<body><div class="wrap">
<h1>foot — analyse de rencontres</h1>
<p class="sub">Dossier sportif scellé avant toute lecture de cote · grille des 22 rubriques ·
comparaison des marchés disponibles · une décision par rencontre.</p>
{form}
{body}
</div></body></html>"""


def _escape_block(title: str, text: str) -> str:
    return f"<h2>{html.escape(title)}</h2><pre>{html.escape(text)}</pre>"


def render_run(run: AnalysisRun, *, budget: float | None, combine: bool) -> str:
    """Turn an analysis run into the page body."""
    parts: list[str] = [_escape_block("Récapitulatif", render_summary(run))]

    missing = run.registry_report.missing_capabilities()
    if missing:
        parts.append(
            '<div class="note">Capacités sans fournisseur accessible&nbsp;: '
            + html.escape(", ".join(c.value for c in missing))
            + ". Les rubriques concernées sont marquées indisponibles&nbsp;; "
            "aucune valeur n'est inventée pour les combler.</div>"
        )

    for analysis in run.analyses:
        label = analysis.resolved.request.label()
        parts.append(
            f"<details open><summary>{html.escape(label)} — "
            f"{html.escape(analysis.headline()[:90])}</summary>"
            f"<pre>{html.escape(render_card(analysis))}</pre>"
        )
        if analysis.rubrics:
            parts.append(
                f"<details><summary>Grille des 22 rubriques</summary>"
                f"<pre>{html.escape(render_rubric_grid(analysis))}</pre></details>"
            )
        parts.append("</details>")

    decisions = [
        (f"{a.resolved.fixture.home} – {a.resolved.fixture.away}", a.decision)
        for a in run.analyses
        if a.decision is not None and a.resolved.fixture is not None
    ]
    if budget:
        parts.append(_escape_block("Mises", plan_stakes(decisions, budget=budget).render()))
    if combine:
        parts.append(_escape_block("Combiné", build_ticket(decisions).render()))
    parts.append(_escape_block("Fournisseurs", run.registry_report.render()))
    return "\n".join(parts)


def make_handler(engine: Engine) -> type[http.server.BaseHTTPRequestHandler]:
    """Build a request handler bound to one engine instance."""

    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "foot/2.0"

        def log_message(self, format: str, *args: object) -> None:
            # The base class logs every request to stderr; silence it so the
            # console stays readable.  Real failures still raise and surface.
            _ = (format, args)

        def _send(self, page: str, status: int = 200) -> None:
            payload = page.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:
            now = dt.datetime.now(resolve_timezone(DEFAULT_TIMEZONE))
            self._send(build_page(date=now.strftime("%Y-%m-%dT%H:%M")))

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8")
            form = urllib.parse.parse_qs(raw, keep_blank_values=True)
            matchs = form.get("matchs", [""])[0]
            date_text = form.get("date", [""])[0]
            zone = form.get("tz", [DEFAULT_TIMEZONE])[0]
            bookmaker = form.get("book", [""])[0]
            budget_text = form.get("budget", [""])[0]
            combine = form.get("combine", ["non"])[0] == "oui"

            body = ""
            try:
                zone_info = resolve_timezone(zone)
                as_of = (
                    dt.datetime.fromisoformat(date_text).replace(tzinfo=zone_info)
                    if date_text
                    else dt.datetime.now(zone_info)
                )
                budget = float(budget_text) if budget_text.strip() else None
                if matchs.strip():
                    run = engine.run(
                        matchs, as_of=as_of, timezone=zone,
                        bookmaker=bookmaker or None, quoted_at=as_of,
                    )
                    body = render_run(run, budget=budget, combine=combine)
                else:
                    body = '<div class="note">Saisissez au moins une rencontre.</div>'
            except (ValueError, KeyError) as error:
                body = f'<div class="note">Erreur&nbsp;: {html.escape(str(error))}</div>'

            self._send(
                build_page(
                    matchs=matchs, date=date_text, timezone=zone, bookmaker=bookmaker,
                    budget=budget_text, combine="oui" if combine else "non", body=body,
                )
            )

    return Handler


def serve(
    engine: Engine,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    announce: Callable[[str], None] = print,
) -> None:
    """Run the interface until interrupted."""

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    with Server((host, port), make_handler(engine)) as httpd:
        announce(f"Interface disponible sur http://{host}:{port}  (Ctrl+C pour arrêter)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            announce("\nArrêt.")
