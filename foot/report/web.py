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

from foot.analysis.diagnostics import CheckReport, run_checks
from foot.analysis.engine import AnalysisRun, Engine
from foot.analysis.journey import JourneyResult, run_journey
from foot.analysis.ledgerbook import ForecastBook, record_run
from foot.analysis.request import DEFAULT_TIMEZONE, parse_requests, resolve_timezone
from foot.analysis.supervisor import LedgerJob, Supervisor, WatchStore
from foot.collect.cache import Cache
from foot.collect.supplements import SupplementSet
from foot.markets.portfolio import build_ticket, plan_stakes
from foot.report.card import render_card, render_rubric_grid
from foot.report.table import render_summary

__all__ = [
    "access_token",
    "analyse_form",
    "build_page",
    "render_checks",
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
.actions { display:flex; gap:10px; flex-wrap:wrap; }
.actions button { flex:1 1 140px; }
button.second { background:transparent; color:var(--accent);
                box-shadow: inset 0 0 0 1.5px var(--accent); }
@media (max-width:560px) { .actions { flex-direction:column; } }
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
      <label for="date">Date et heure de l'analyse</label>
      <input type="datetime-local" id="date" name="date" value="{date}">
      <p class="hint">Vide&nbsp;: analyse <strong>maintenant</strong>, avec ce
        que la collecte rapporte. Une date remplie <strong>rejoue</strong> cet
        instant&nbsp;: rien de publié après n'y entre.</p>
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
  <div class="actions">
    <button type="submit" name="action" value="analyser">Analyser</button>
    <button type="submit" name="action" value="suivre" class="second">Suivre</button>
    <button type="submit" name="action" value="bilan" class="second">Bilan</button>
    <button type="submit" name="action" value="controle" class="second">Contrôles</button>
  </div>
  <p class="hint"><strong>Analyser</strong> produit une fiche par rencontre.
    <strong>Suivre</strong> lance le contrôle T−75/T−60 côté serveur — il
    continue même si vous fermez l'onglet. <strong>Bilan</strong> mesure les
    prévisions déjà enregistrées. <strong>Contrôles</strong> appelle réellement
    vos fournisseurs sur la rencontre saisie et dit, famille par famille, ce qui
    revient — sans jamais afficher une clé.</p>
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
    live: bool = False,
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
        live=live,
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


def _render_ledger(
    book: ForecastBook | None, supervisor: Supervisor | None = None
) -> str:
    """The Bilan screen: the journal, and the measurement it actually runs.

    The measurement happens **on the server**, in the background, because
    fetching results for every competition takes longer than a phone will hold a
    request open. The page starts it and reads its state; pressing Bilan again
    shows how far it has got, then the finished report.
    """
    if book is None:
        return (
            '<div class="note">Aucun journal n\'est conservé par ce serveur. '
            "Démarrez-le avec <code>--journal</code> pour que chaque analyse "
            "servie soit enregistrée.</div>"
        )
    lines = list(book)
    written = book.render(limit=25)
    retrospective = sum(1 for f in lines if f.retrospective)
    note = (
        f'<div class="note">Journal&nbsp;: <code>{html.escape(str(book.path))}</code> — '
        f"{len(lines)} ligne(s), dont {retrospective} rejeu(x) rétrospectif(s). "
        f"Sauvegarde&nbsp;: copiez ce fichier ; il se restaure en le remettant "
        f"en place, et il se relit ligne à ligne. Mesure&nbsp;: "
        f"<code>foot mesurer</code> une fois les résultats connus.</div>"
    )
    body = note + _render_measurement(supervisor)
    return body + _escape_block("Prévisions enregistrées", written)


def _render_measurement(supervisor: Supervisor | None) -> str:
    """Start or report the server-side measurement."""
    if supervisor is None:
        return (
            '<div class="note">La mesure côté serveur n\'est pas active ici. '
            "Depuis un terminal&nbsp;: <code>foot mesurer</code>.</div>"
        )
    job = supervisor.ledger_job()
    if job is None:
        return (
            '<div class="note">Aucune mesure lancée. Appuyez de nouveau sur '
            "<strong>Bilan</strong> pour en démarrer une&nbsp;: elle tourne sur "
            "le serveur, et vous pourrez revenir voir le résultat.</div>"
        )
    finished, progress, report, error = job.snapshot()
    if error:
        return f'<div class="note">Mesure interrompue&nbsp;: {html.escape(error)}</div>'
    if not finished:
        return (
            f'<div class="note">Mesure en cours — {html.escape(progress)}. '
            f"Elle continue si vous fermez l'onglet&nbsp;; rappuyez sur "
            f"<strong>Bilan</strong> pour voir où elle en est.</div>"
        )
    return _escape_block("Bilan mesuré", report)


def render_checks(report: CheckReport) -> str:
    """The Contrôles screen: what each service actually returned, just now."""
    verdict = (
        '<div class="note">Tout ce qui a été demandé est revenu. '
        "Les chiffres ci-dessous sont mesurés, pas annoncés.</div>"
        if report.ready
        else '<div class="note">Une ligne au moins n\'est pas au vert. '
        "Le symbole dit laquelle&nbsp;: <code>●</code> obtenu · "
        "<code>◐</code> service joignable mais rien à servir · "
        "<code>✗</code> refusé (clé ou quota) · <code>○</code> injoignable · "
        "<code>·</code> clé absente.</div>"
    )
    return verdict + _escape_block("Contrôle des connexions", report.render())


def _run_checks_screen(
    engine: Engine, *, matches: str, bookmaker: str, zone: str, cache: Cache | None = None
) -> str:
    """Run the control on the first line typed, or explain what is missing."""
    line = matches.strip().splitlines()[0] if matches.strip() else ""
    if not line:
        return (
            '<div class="note">Saisissez une rencontre à venir, puis appuyez de '
            "nouveau sur <strong>Contrôles</strong>&nbsp;: le contrôle appelle "
            "les fournisseurs <em>sur cette rencontre</em>, faute de quoi il ne "
            "vérifierait rien de réel.</div>"
        )
    report = run_checks(
        engine,
        fixture_line=line,
        bookmaker=bookmaker,
        timezone=zone,
        cache=cache,
    )
    return render_checks(report)


def _start_watch(
    supervisor: Supervisor | None,
    *,
    matches: str,
    zone: dt.tzinfo,
    bookmaker: str,
) -> str:
    """The Suivre screen: start a server-side watch, then show every watch."""
    if supervisor is None:
        return (
            '<div class="note">Le suivi côté serveur n\'est pas actif sur ce '
            "serveur.</div>"
        )
    if matches.strip():
        kickoff = _next_kickoff(matches, zone)
        if kickoff is None:
            return (
                '<div class="note">Pour suivre une rencontre, indiquez son '
                "heure de coup d'envoi dans la ligne, par exemple "
                "<code>Napoli - Bologna 13/09/2026 20:45</code>.</div>"
            )
        supervisor.start(
            matches=matches.strip().splitlines()[0],
            kickoff=kickoff,
            bookmaker=bookmaker,
        )
    return _escape_block("Suivis en cours", supervisor.render()) + (
        '<div class="note">Un suivi lancé ici tourne sur le serveur&nbsp;: il '
        "continue si vous fermez l'onglet, et s'arrête si le serveur s'arrête. "
        "Rechargez cette page pour voir où il en est.</div>"
    )


def _next_kickoff(matches: str, zone: dt.tzinfo) -> dt.datetime | None:
    """Read the kick-off out of the first line, through the shared parser."""
    requests = parse_requests(matches, today=dt.datetime.now(zone).date())
    for request in requests:
        if request.date is not None and request.time is not None:
            return dt.datetime.combine(request.date, request.time, tzinfo=zone)
    return None


def make_handler(
    engine: Engine,
    *,
    token: str = "",
    book: ForecastBook | None = None,
    supervisor: Supervisor | None = None,
    measurement: Callable[[LedgerJob], str] | None = None,
    cache: Cache | None = None,
) -> type[http.server.BaseHTTPRequestHandler]:
    """Build a request handler bound to one engine instance.

    Args:
        token: when set, every request must carry it — once in the address
            (``?jeton=…``), then in a cookie. Without it the page is refused.
        book: when set, every analysis served is journalled **server-side**, so
            a phone that loses its browser tab loses nothing.
        supervisor: holds the watches started from the page. They run on the
            server, so closing the tab does not cancel the T−75 check.
        measurement: what **Bilan** runs in the background. It is injected
            because collecting results is the caller's business — which
            provider, which season — and this module's business is the screen.
        cache: the store the analyses already fill. **Contrôles** reads through
            it so that checking the connections does not spend a second set of
            credits on data fetched minutes earlier.
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
            # The date field is left **empty**: an empty field means "now", and
            # pre-filling it with the current time turned the ordinary journey
            # — open, type the teams, press Analyser — into a replay of the
            # minute the page happened to be loaded. A replay is a deliberate
            # choice, so it has to be typed deliberately.
            self._send(
                build_page(),
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

            action = form.get("action", ["analyser"])[0]
            body = ""
            try:
                zone_info = resolve_timezone(zone)
                as_of = (
                    dt.datetime.fromisoformat(date_text).replace(tzinfo=zone_info)
                    if date_text
                    else dt.datetime.now(zone_info)
                )
                budget = float(budget_text) if budget_text.strip() else None
                if action == "bilan":
                    if supervisor is not None and measurement is not None:
                        supervisor.measure_async(measurement)
                    body = _render_ledger(book, supervisor)
                elif action == "controle":
                    body = _run_checks_screen(
                        engine,
                        matches=matchs,
                        bookmaker=bookmaker,
                        zone=zone,
                        cache=cache,
                    )
                elif action == "suivre":
                    body = _start_watch(
                        supervisor,
                        matches=matchs,
                        zone=zone_info,
                        bookmaker=bookmaker,
                    )
                elif matchs.strip():
                    result = analyse_form(
                        engine,
                        matches=matchs,
                        as_of=as_of,
                        timezone=zone,
                        bookmaker=bookmaker or None,
                        pasted=context,
                        # An empty date field means "now", like the terminal's
                        # missing --date. The two surfaces must not differ here.
                        live=not date_text,
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
    watch_engine: Engine | None = None,
    measurement: Callable[[LedgerJob], str] | None = None,
    cache: Cache | None = None,
    watch_store: WatchStore | None = None,
    public_url: str = "",
    behind_tls: bool = False,
) -> None:
    """Run the interface until interrupted.

    Three things make this safe to expose beyond ``localhost``, and the caller
    must ask for all three — none is switched on silently:

    ``token``
        a private address. Without it anyone reaching the port gets the form.
    ``certfile``/``keyfile``
        HTTPS. A token sent over plain HTTP is a token given away, so the
        announcement says so plainly when TLS is off and the host is not local.
        ``behind_tls`` says the opposite case out loud: a managed host that
        terminates TLS at its edge leaves this process speaking plain HTTP on a
        private network, and warning about a plaintext token there would be
        false. It is a claim the caller makes, not something detected — so it is
        an explicit flag and never a default.
    ``book``
        server-side storage of every analysis served, so the phone holds none
        of the state. It is a plain append-only file: backing it up is copying
        it, and restoring it is putting it back.

    ``watch_engine``
        the engine the **Suivre** button uses. It exists because a watch needs
        team sheets read minutes apart, and the day-to-day engine caches them
        for six hours; sharing one engine made the T−60 check replay T−75's
        answer.

    ``watch_store``
        where the watches are written down. With it, a restart of the server —
        a deployment, a host putting the instance to sleep — **resumes** the
        watches whose kick-off is still ahead, and declares « manqué » the ones
        whose kick-off went by while it was down. Without it, a restart loses
        them, and the page says so rather than implying otherwise.

    The **Suivre** button starts its watch on the server, so the T−75 check
    happens whether or not the tab is still open.

    API keys never reach the browser in any configuration: the engine reads
    them from the server's own environment, and no page template renders them.
    """

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    scheme = "https" if certfile and keyfile else "http"
    supervisor = Supervisor(
        engine, book=book, watch_engine=watch_engine, store=watch_store
    )
    resumed, missed = supervisor.resume()
    handler = make_handler(
        engine,
        token=token,
        book=book,
        supervisor=supervisor,
        measurement=measurement,
        cache=cache,
    )
    with Server((host, port), handler) as httpd:
        if scheme == "https":
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(certfile=str(certfile), keyfile=str(keyfile))
            httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
        local = host in {"127.0.0.1", "localhost"} and not public_url
        base = public_url.rstrip("/") or f"{scheme}://{host}:{port}"
        if not token:
            announce(f"Interface disponible sur {base}/  (Ctrl+C pour arrêter)")
        elif local:
            # Your own terminal: printing the token is the fastest way to open
            # the page, and nobody else reads this console.
            announce(f"Interface disponible sur {base}/?jeton={token}")
        else:
            # A hosted instance keeps its console in the provider's log, which
            # outlives the session and is read by whoever can see the dashboard.
            # The address goes there; the token stays where it was set.
            announce(
                f"Interface disponible sur {base}/?jeton=VOTRE_JETON — "
                f"remplacez VOTRE_JETON par la valeur de la variable "
                f"d'environnement qui le porte. Elle n'est pas recopiée ici."
            )
        if token and scheme == "http" and not behind_tls and not local:
            announce(
                "ATTENTION : jeton transmis en clair. Sur un réseau non local, "
                "fournissez un certificat (--certificat/--cle), placez le "
                "service derrière un reverse proxy HTTPS, ou — si c'est déjà le "
                "cas — démarrez-le avec --https-en-amont."
            )
        if book is not None:
            announce(f"Analyses conservées dans {book.path} (ajout seul).")
        if watch_store is not None:
            announce(
                f"Suivis conservés dans {watch_store.path} : "
                f"{len(resumed)} repris, {len(missed)} manqué(s) pendant l'arrêt."
            )
            for handle in missed:
                announce(f"  MANQUÉ : {handle.matches.strip().splitlines()[0][:60]}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            announce("\nArrêt.")
