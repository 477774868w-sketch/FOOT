"""A small French web interface, served from the standard library.

No framework, no build step, no dependency: ``python3 -m foot web`` starts a
local server and the whole journey — paste the matches, pick the date and
timezone, add prices and context if you have them, read a card per match and one
summary table — happens in the browser, including on a phone.

Everything the command line can do is reachable here, and by the *same* route:
:func:`analyse_form` is the single entry point, so the browser and the CLI
cannot drift into giving different answers to the same question. The audited
version had no context imports at all in the form — xG, absences and lineups
were command-line-only — which meant the operator's own interface could not
answer four of the twenty-two rubrics.

The page is deliberately plain.  Its job is to make the analysis reachable, not
to be admired.
"""

from __future__ import annotations

import datetime as dt
import hmac
import html
import http.server
import secrets
import socketserver
import ssl
import urllib.parse
from collections.abc import Callable, Mapping
from pathlib import Path

from foot.analysis.engine import AnalysisRun, Engine
from foot.analysis.journey import JourneyResult, run_journey
from foot.analysis.ledgerbook import ForecastBook, record_run
from foot.analysis.request import DEFAULT_TIMEZONE, resolve_timezone
from foot.collect.supplements import SupplementSet
from foot.markets.portfolio import build_ticket, plan_stakes
from foot.report.card import render_card, render_rubric_grid
from foot.report.table import render_summary

__all__ = [
    "access_token",
    "analyse_form",
    "build_page",
    "render_form",
    "render_result",
    "serve",
]

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
textarea.small { min-height:88px; }
details.ctx { margin-top:14px; border:1px solid var(--line); border-radius:8px;
              padding:10px 14px; }
details.ctx button { margin-top:12px; }
ul.rejets { margin:8px 0 0; padding-left:20px; font-size:0.86rem; }
@media (max-width:560px) {
  .row { gap:0; }
  button { width:100%; }
}
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
    (les trois cotes sont 1&nbsp;/&nbsp;N&nbsp;/&nbsp;2).<br>
    Autres marchés, dans la même ligne&nbsp;: <code>1=</code> <code>N=</code>
    <code>2=</code> <code>DC:1N=</code> <code>DNB:1=</code>
    <code>TOTAL:+2.5=</code> <code>AH:H:-0.5=</code> <code>TE:H:+1.5=</code>
    <code>BTTS:oui=</code> — un marché sans prix ne peut pas être comparé.</p>
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
  <button type="submit">Analyser</button>
  <details class="ctx">
    <summary>Mises et combiné (facultatif)</summary>
    <div class="row">
      <div>
        <label for="budget">Budget pour les mises</label>
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
  </details>
  <details class="ctx"{ctx_open}>
    <summary>Contexte à coller — xG, absences, compositions (facultatif)</summary>
    <p class="hint">À n'utiliser que pour ce qu'aucune source active ne sert&nbsp;:
      la liste des fournisseurs, en bas de chaque analyse, dit lesquels le sont.
      Une ligne collée ici est marquée « fournie par l'opérateur » de bout en
      bout. Une ligne illisible est affichée pour correction, jamais devinée.
      Sans heure de publication, une ligne du jour n'est réputée connue que le
      lendemain.</p>
    <label for="xg">xG — <code>date,home,away,home_xg,away_xg</code>
      puis au choix <code>source,statut,publication</code></label>
    <textarea id="xg" name="xg" class="small"
      placeholder="{xg_ph}">{xg}</textarea>
    <label for="absences">Absences — <code>date,equipe,joueur</code> puis au choix
      <code>poste,motif,source,statut,remplacant,jusqu_au,retour</code></label>
    <textarea id="absences" name="absences" class="small"
      placeholder="{abs_ph}">{absences}</textarea>
    <label for="compositions">Compositions — <code>date,equipe,joueur</code> puis au
      choix <code>poste,titulaire,source,statut,publication</code></label>
    <textarea id="compositions" name="compositions" class="small"
      placeholder="{compo_ph}">{compositions}</textarea>
  </details>
</form>
"""

_XG_PLACEHOLDER = (
    "date,home,away,home_xg,away_xg,source,statut\n"
    "30/08/2026,SSC Napoli,Como 1907,2.31,0.74,Opta,probable"
)
_ABSENCE_PLACEHOLDER = (
    "date,equipe,joueur,poste,motif,source,statut,remplacant\n"
    "12/09/2026,SSC Napoli,Alex Meret,gardien,blessure,club,officiel,Caprile"
)
_LINEUP_PLACEHOLDER = (
    "date,equipe,joueur,poste,titulaire,source,statut,publication\n"
    "13/09/2026,SSC Napoli,Milinkovic-Savic,gardien,oui,club,officiel,13/09/2026 19:30"
)

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


def render_form(
    *,
    matchs: str = "",
    date: str = "",
    timezone: str = DEFAULT_TIMEZONE,
    bookmaker: str = "",
    budget: str = "",
    combine: str = "non",
    context: Mapping[str, str] | None = None,
) -> str:
    """Render the input form alone — the whole operator surface in one place."""
    pasted = dict(context or {})
    return _FORM.format(
        matchs=html.escape(matchs),
        placeholder=html.escape(_PLACEHOLDER),
        date=html.escape(date),
        zones=_zone_options(timezone),
        book=html.escape(bookmaker),
        budget=html.escape(budget),
        c_non=" selected" if combine != "oui" else "",
        c_oui=" selected" if combine == "oui" else "",
        xg=html.escape(pasted.get("xg", "")),
        absences=html.escape(pasted.get("absences", "")),
        compositions=html.escape(pasted.get("compositions", "")),
        xg_ph=html.escape(_XG_PLACEHOLDER),
        abs_ph=html.escape(_ABSENCE_PLACEHOLDER),
        compo_ph=html.escape(_LINEUP_PLACEHOLDER),
        ctx_open=" open" if any(pasted.values()) else "",
    )


def build_page(
    *,
    matchs: str = "",
    date: str = "",
    timezone: str = DEFAULT_TIMEZONE,
    bookmaker: str = "",
    budget: str = "",
    combine: str = "non",
    context: Mapping[str, str] | None = None,
    body: str = "",
) -> str:
    """Render the whole single-page interface."""
    form = render_form(
        matchs=matchs, date=date, timezone=timezone, bookmaker=bookmaker,
        budget=budget, combine=combine, context=context,
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


def analyse_form(
    engine: Engine,
    *,
    matches: str,
    as_of: dt.datetime,
    timezone: str = DEFAULT_TIMEZONE,
    bookmaker: str | None = None,
    pasted: Mapping[str, str] | None = None,
    supplements: SupplementSet | None = None,
) -> JourneyResult:
    """Run one submitted form through the shared journey.

    The browser adds nothing of its own here: :func:`foot.analysis.journey.run_journey`
    is the single path, so the terminal and this page cannot answer the same
    question differently.
    """
    return run_journey(
        engine,
        matches=matches,
        as_of=as_of,
        timezone=timezone,
        bookmaker=bookmaker,
        pasted=pasted,
        supplements=supplements,
    )


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


def render_result(result: JourneyResult, *, budget: float | None, combine: bool) -> str:
    """The page body: what was refused, what was kept, then the analysis itself."""
    parts: list[str] = []
    if result.rejected:
        parts.append(
            '<div class="note"><strong>Lignes non retenues</strong> — corrigez-les '
            "et relancez&nbsp;; rien n'a été deviné à leur place&nbsp;:<ul "
            'class="rejets">'
            + "".join(f"<li>{html.escape(line)}</li>" for line in result.rejected)
            + "</ul></div>"
        )
    if result.notes:
        parts.append(
            '<div class="note"><strong>Lignes lues mais écartées de cette '
            "analyse</strong> — le dossier sportif n'en dépend pas&nbsp;:"
            '<ul class="rejets">'
            + "".join(f"<li>{html.escape(note)}</li>" for note in result.notes)
            + "</ul></div>"
        )
    if result.used:
        parts.append(
            # Same wording as the terminal: the two surfaces answer the same
            # question, so they should not phrase the answer differently.
            '<div class="note">Contexte retenu&nbsp;: '
            + html.escape(", ".join(result.used))
            + ". Ce qui n'apparaît pas ici n'a pas servi à l'analyse.</div>"
        )
    parts.append(render_run(result.run, budget=budget, combine=combine))
    return "\n".join(parts)


_COOKIE = "foot_acces"


def access_token(length: int = 24) -> str:
    """A fresh access token, drawn from the system's own randomness.

    Private access means *this* phone, not the whole network segment. The token
    lives in the URL once, then in a cookie; it never travels in a page, never
    reaches a log, and is worthless without the address it opens.
    """
    return secrets.token_urlsafe(length)


def make_handler(
    engine: Engine,
    *,
    token: str = "",
    book: ForecastBook | None = None,
) -> type[http.server.BaseHTTPRequestHandler]:
    """Build a request handler bound to one engine instance.

    Args:
        token: when set, every request must carry it — once in the address
            (``?jeton=…``), then in a cookie. Without it the page is refused.
        book: when set, every analysis served is journalled **server-side**, so
            a phone that loses its browser tab loses nothing.
    """

    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "foot/2.0"

        def log_message(self, format: str, *args: object) -> None:
            # The base class logs every request to stderr; silence it so the
            # console stays readable.  Real failures still raise and surface.
            _ = (format, args)

        def _send(self, page: str, status: int = 200, *, cookie: str = "") -> None:
            payload = page.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            if cookie:
                self.send_header(
                    "Set-Cookie",
                    f"{_COOKIE}={cookie}; Path=/; HttpOnly; SameSite=Strict",
                )
            self.end_headers()
            self.wfile.write(payload)

        def _supplied_token(self) -> str:
            """Read the token from the address, then from the cookie."""
            query = urllib.parse.urlparse(self.path).query
            from_url = urllib.parse.parse_qs(query).get("jeton", [""])[0]
            if from_url:
                return from_url
            raw = self.headers.get("Cookie", "")
            for part in raw.split(";"):
                name, _, value = part.strip().partition("=")
                if name == _COOKIE:
                    return value
            return ""

        def _authorised(self) -> bool:
            """Compare in constant time, so a wrong token leaks no timing."""
            if not token:
                return True
            return hmac.compare_digest(self._supplied_token(), token)

        def _refuse(self) -> None:
            self._send(
                build_page(
                    body='<div class="note">Accès privé. Ouvrez l\'adresse '
                    "complète fournie au démarrage, jeton compris.</div>"
                ),
                status=403,
            )

        def do_GET(self) -> None:
            if not self._authorised():
                self._refuse()
                return
            now = dt.datetime.now(resolve_timezone(DEFAULT_TIMEZONE))
            self._send(
                build_page(date=now.strftime("%Y-%m-%dT%H:%M")),
                cookie=self._supplied_token() if token else "",
            )

        def do_POST(self) -> None:
            if not self._authorised():
                self._refuse()
                return
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8")
            form = urllib.parse.parse_qs(raw, keep_blank_values=True)
            matchs = form.get("matchs", [""])[0]
            date_text = form.get("date", [""])[0]
            zone = form.get("tz", [DEFAULT_TIMEZONE])[0]
            bookmaker = form.get("book", [""])[0]
            budget_text = form.get("budget", [""])[0]
            combine = form.get("combine", ["non"])[0] == "oui"

            context = {
                "xg": form.get("xg", [""])[0],
                "absences": form.get("absences", [""])[0],
                "compositions": form.get("compositions", [""])[0],
            }

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
                    result = analyse_form(
                        engine,
                        matches=matchs,
                        as_of=as_of,
                        timezone=zone,
                        bookmaker=bookmaker or None,
                        pasted=context,
                    )
                    body = render_result(result, budget=budget, combine=combine)
                    if book is not None:
                        kept = record_run(
                            result.run.analyses,
                            book=book,
                            as_of=as_of,
                            reason="formulaire",
                        )
                        body += (
                            f'<div class="note">Sauvegarde&nbsp;: {len(kept)} '
                            f"prévision(s) conservée(s) côté serveur. "
                            f"Rien n'y est réécrit après coup.</div>"
                        )
                else:
                    body = '<div class="note">Saisissez au moins une rencontre.</div>'
            except (ValueError, KeyError) as error:
                body = f'<div class="note">Erreur&nbsp;: {html.escape(str(error))}</div>'

            self._send(
                build_page(
                    matchs=matchs, date=date_text, timezone=zone, bookmaker=bookmaker,
                    budget=budget_text, combine="oui" if combine else "non",
                    context=context, body=body,
                )
            )

    return Handler


def serve(
    engine: Engine,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    announce: Callable[[str], None] = print,
    token: str = "",
    certfile: str | Path = "",
    keyfile: str | Path = "",
    book: ForecastBook | None = None,
) -> None:
    """Run the interface until interrupted.

    Three things make this safe to expose beyond ``localhost``, and the caller
    must ask for all three — none is switched on silently:

    ``token``
        a private address. Without it anyone reaching the port gets the form.
    ``certfile``/``keyfile``
        HTTPS. A token sent over plain HTTP is a token given away, so the
        announcement says so plainly when TLS is off and the host is not local.
    ``book``
        server-side storage of every analysis served, so the phone holds none
        of the state.

    API keys never reach the browser in any configuration: the engine reads
    them from the server's own environment, and no page template renders them.
    """

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    scheme = "https" if certfile and keyfile else "http"
    with Server((host, port), make_handler(engine, token=token, book=book)) as httpd:
        if scheme == "https":
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(certfile=str(certfile), keyfile=str(keyfile))
            httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
        suffix = f"/?jeton={token}" if token else "/"
        announce(
            f"Interface disponible sur {scheme}://{host}:{port}{suffix}"
            f"  (Ctrl+C pour arrêter)"
        )
        if token and scheme == "http" and host not in {"127.0.0.1", "localhost"}:
            announce(
                "ATTENTION : jeton transmis en clair. Sur un réseau non local, "
                "fournissez un certificat (--certificat/--cle) ou placez le "
                "service derrière un reverse proxy HTTPS."
            )
        if book is not None:
            announce(f"Analyses conservées dans {book.path} (ajout seul).")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            announce("\nArrêt.")
