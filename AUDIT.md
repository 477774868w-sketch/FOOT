# Audit du dépôt et correspondance avec les 22 rubriques

> **Révision du 13/09/2026 — suite à la revue indépendante du commit `8813955`.**
> Les neuf défauts signalés ont été **reproduits sur données de contrôle avant
> toute correction**, chacun assorti d'un test qui échouait sur la version
> auditée. Voir la section 7 pour le détail et les chiffres recalculés.

**Branche** `claude/code-masterpiece-o2mbbl` · **commit de départ** `0aa80a2` ·
**date de l'audit** 2026-09-13.

---

## 0. Avertissement préalable — le protocole n'a pas été fourni

Le document canonique des 22 rubriques **n'est pas présent dans le dépôt et n'est
pas arrivé avec la demande**. Vérifié :

```console
$ find . -path ./.git -prune -o -iname "*protocol*" -print -o -iname "*rubri*" -print
./README.md          # seul fichier ; aucune trace du protocole
```

La grille utilisée est donc une **reconstruction fidèle à partir de votre propre
cahier des charges** (§2 à §11 de votre message), et non une invention : chaque
rubrique renvoie au paragraphe qui la motive. Elle est stockée **comme donnée**
dans `foot/analysis/rubrics.py` et remplaçable sans toucher à une ligne de
logique :

```python
from foot.analysis.rubrics import load_rubrics
RUBRICS = load_rubrics("mon_protocole.json")   # même effet sur tous les rapports
```

Si votre liste canonique diffère, transmettez-la : seul ce fichier change.

---

## 1. État vérifié du dépôt avant travaux

| Contrôle | Résultat mesuré |
|---|---|
| Branche / commit | `claude/code-masterpiece-o2mbbl` @ `0aa80a2`, arbre propre |
| `pytest -q` | **148 passés, 0 échec** |
| `python3 tests/run_tests.py` | **148 passés** (sans dépendance) |
| `ruff check .` | propre |
| `mypy .` | propre, 45 fichiers |

Les chiffres annoncés dans le README de départ étaient donc exacts. Le moteur
mathématique (Dixon-Coles, L-BFGS, Elo, Kelly, marchés, backtest) a été
**conservé intégralement** ; aucune réécriture générale n'a eu lieu.

---

## 2. Contrainte externe majeure — accès réseau

Mesurée, pas supposée. `python3 -m foot fournisseurs` sonde à chaque exécution.

| Hôte | Résultat | Conséquence |
|---|---|---|
| `raw.githubusercontent.com` (openfootball) | **200 OK** | résultats + calendriers réels, 5 championnats |
| `www.football-data.co.uk` | **403 CONNECT** (politique de sortie) | adaptateur écrit et testé, mais injoignable ici |
| `api.football-data.org`, `api.openligadb.de`, `thesportsdb.com` | **403 CONNECT** | non intégrés |
| `fbref.com`, `understat.com` (xG) | **403 CONNECT** | **aucune source xG** |
| `api.the-odds-api.com`, `api.sofascore.com` (cotes) | **403 CONNECT** | **aucune source de cotes** |

**Conséquences assumées, jamais contournées :**

- la phase sportive est **réelle** (résultats openfootball, saisons 2024-25 à
  2026-27, EN/ES/DE/IT/FR) ;
- les **cotes proviennent de votre saisie**, ce que votre §2 prévoit
  explicitement ; le système ne prétend jamais les avoir recoupées ;
- les rubriques xG, compositions, absences, météo et arbitre sont marquées
  **indisponibles**, avec le motif exact. Aucune valeur n'est substituée.

Un refus de politique (403) n'est **pas réessayé** — cela ne dirait rien de
nouveau — et il est classé `ProviderBlockedError`, distinct d'une erreur
ordinaire, pour que le rapport nomme le blocage plutôt que de le noyer.

---

## 3. Correspondance avec les 22 rubriques

Légende : **✅ intégré et vérifié** (utilisé par l'analyse principale, couvert par
un test) · **🟡 partiel** · **⛔ bloqué par une dépendance externe précise**.

| # | Rubrique | Statut | Implémentation | Test |
|---|---|---|---|---|
| 01 | Identification et statut de la rencontre | ✅ | `analysis/request.py`, `analysis/engine.py::_resolve` | `test_acceptance.py::test_every_typed_line_reaches_the_summary_table`, `::test_a_started_match_gets_no_prematch_recommendation` |
| 02 | Traçabilité des sources | ✅ | `provenance.py` (`Evidence`, `Ledger`) | `::test_two_sites_republishing_one_feed_are_one_confirmation`, `::test_a_contradiction_is_surfaced_not_averaged` |
| 03 | Séparation sport/cotes, scellement | ✅ | `analysis/dossier.py` (`SportInput`, `seal`, `ExposureAudit`) | `::test_the_sport_input_admits_no_market_evidence`, `::test_prices_are_read_only_after_the_dossier_is_sealed` |
| 04 | Forme récente dans un historique long | ✅ | `engine.py::_findings` → pondération temporelle Dixon-Coles | `::test_findings_separate_what_moves_the_model_from_what_is_only_shown` |
| 05 | Qualité des adversaires à l'époque | ✅ | estimation conjointe attaque/défense (`models/dixon_coles.py`) | `test_dixon_coles.py::test_parameter_recovery_improves_with_more_data` |
| 06 | Différentiel domicile/extérieur | ✅ | paramètre `home_advantage` | `::test_a_declared_variable_actually_changes_the_forecast` |
| 07 | Buts, xG, npxG, tirs, grosses occasions | ⛔ | — | **bloqué** : aucun fournisseur xG joignable (403 sur fbref, understat) |
| 08 | Penalties, exclusions, périodes déformantes | ⛔ | — | **bloqué** : exige des événements détaillés |
| 09 | 11 contre 11 et état du score | ⛔ | — | **bloqué** : idem ; explicitement **non déduit** des totaux |
| 10 | Gardien titulaire et remplaçant | ⛔ | — | **bloqué** : aucun fournisseur de compositions |
| 11 | Absences, retours, minutes attendues | ⛔ | — | **bloqué** : aucun fournisseur d'absences |
| 12 | Entraîneur, système, styles | ⛔ | — | **bloqué** : idem |
| 13 | Repos, déplacements, rotation | 🟡 | jours de repos calculés depuis le calendrier | `::test_findings_separate...` — **affiché, non utilisé** : aucun coefficient de fatigue validé |
| 14 | Coups de pied arrêtés, transitions, banc | ⛔ | — | **bloqué** : statistiques avancées |
| 15 | Météo, pelouse, arbitre | ⛔ | — | **bloqué** : aucun fournisseur |
| 16 | Donnée → variable → effet | ✅ | `dossier.Finding` (`kind`, `model_variable`, `effect`) | `::test_findings_separate_what_moves_the_model_from_what_is_only_shown` |
| 17 | Références et régularisation | ✅ | Dixon-Coles + Poisson + Elo, ridge ∝ 1/n | `test_dixon_coles.py::test_the_ridge_penalty_shrinks_ratings_without_polluting_the_likelihood` |
| 18 | Scénarios et contre-analyse | ✅ | `engine.py::_scenarios`, `_counter_analysis` | `test_markets_engine.py::test_robustness_uses_the_worst_credible_scenario` |
| 19 | Comparaison des marchés | ✅ (cotes fournies) | `markets/catalogue.py` (36 offres), `markets/selection.py` | `::test_every_family_matches_a_hand_computed_grid_sum` |
| 20 | Loi jointe et règlements asiatiques | ✅ | `markets/settlement.py`, `markets/pricing.py` | `::test_a_whole_asian_line_pushes_and_a_quarter_line_half_settles`, `::test_a_same_match_combination_is_an_intersection_not_a_product` |
| 21 | Compositions probables puis officielles | 🟡 | `analysis/lineups.py` (plan + révision), `analysis/watch.py` (boucle exécutée), `collect/footballdata_org.py` (adaptateur) | `test_daily_use.py::test_the_watch_retries_until_the_sheet_is_published` — **le contrôle est exécuté par `foot suivre`** ; la source automatique attend une clé, et le rapport le nomme |
| 22 | Décision, confiance, risque, restitution | ✅ | `report/card.py`, `report/table.py`, `report/web.py` | `::test_the_web_interface_renders_a_full_report` |

**Bilan : 13 rubriques intégrées et vérifiées, 2 partielles, 7 bloquées par une
dépendance externe nommée.** Aucune rubrique bloquée ne produit d'affirmation.

Une fonction présente mais jamais appelée par l'analyse principale n'est pas
comptée comme intégrée : c'est pourquoi la colonne « Implémentation » nomme le
chemin d'exécution, pas seulement le module.

---

## 4. Les deux points techniques que vous avez signalés

### Shin — la documentation était trop large, le code était juste

Reproduit avant tout jugement :

```console
$ python3 -c "from foot.market.devig import shin_insider_fraction
print(shin_insider_fraction([1/3.5]*3))"
0.0
```

Avec 3.50 / 3.50 / 3.50, `Σ 1/cote = 0,857 < 1` : **il n'existe effectivement pas
de racine**, puisque la démonstration d'existence suppose `B ≥ 1`. Le code gérait
déjà le cas (`if booksum <= 1.0: return 0.0`, repli sur la normalisation).
**C'est le README qui affirmait trop** — « for genuine decimal odds », sans la
condition. Corrigé, et le comportement est désormais épinglé par
`test_markets_engine.py::test_shin_has_no_root_on_an_arbitrage_book`.

### Kelly simultané — le garde-fou était accidentel

`kelly_portfolio` refusait les paris qui se recoupent uniquement parce que leurs
probabilités ne sommaient pas à 1. Deux paris qui se recoupent **dont les
probabilités somment à 1 par coïncidence** passaient et étaient dimensionnés
comme exclusifs. Deux corrections :

1. la précondition est désormais **explicite et typée** — `NotMutuallyExclusiveError`
   avec un message qui nomme l'erreur (`market/kelly.py`) ;
2. surtout, `markets/portfolio.py::verify_exclusive` **vérifie l'exclusivité sur
   la grille des scores** : deux sélections sont exclusives si et seulement si
   aucune case ne les fait gagner ensemble. C'est décidable, et c'est décidé.

```console
$ verify_exclusive([victoire domicile, plus de 2.5], grille)
OverlapError: « Victoire domicile (1) » et « Plus de 2.5 buts » gagnent tous
deux sur le score 2-1 …
```

---

## 5. Défauts trouvés et corrigés pendant les travaux

| Défaut | Détection | Correction | Test |
|---|---|---|---|
| Lignes quart mal tarifées : mélange des **profils** au lieu des résultats unitaires **par case** — espérance juste, variance fausse | contrôle croisé contre `ScoreMatrix` | `markets/pricing.py` ; écart d'espérance vérifié à 1e-16, croissance logarithmique désormais correcte |
| Alias d'équipe `"losc"` défini deux fois | `ruff F601` | doublon supprimé |
| Moteur couplé à `OpenFootballProvider` par `isinstance` | tests d'acceptation avec un fournisseur factice | protocole `SeasonSource` ; `SeasonData` déplacé dans le contrat |
| `both_teams_to_score` sans docstring | test d'architecture existant | docstring ajoutée |
| Espérance calculée par `p × cote − 1` sur des marchés remboursables | revue du §8 | `SettlementProfile.expected_value` ; écart démontré : 0,035 contre 0,285 avec 25 % de remboursement |

---

## 6. Ce qui reste à configurer pour lever les blocages

Rien dans le code. Uniquement des accès réseau sortants :

| Pour débloquer | Il faut | Rubriques libérées |
|---|---|---|
| Cotes et arbitre | accès à `www.football-data.co.uk` — **adaptateur déjà écrit** (`collect/footballdata.py`) | 15 (arbitre), 19 (historique de cotes pour la validation) |
| xG et statistiques avancées | un fournisseur joignable (fbref, Understat, StatsBomb API) + un adaptateur | 07, 08, 09, 14 |
| Compositions et absences | un fournisseur joignable + un adaptateur | 10, 11, 12, 21 |
| Météo | une API météo joignable | 15 |

En attendant, `--resultats-csv` et `--cotes-csv` permettent l'**import manuel**,
documenté comme solution de secours et marqué comme tel dans la traçabilité.


---

## 7. Revue indépendante du commit `8813955` — corrections

Les **38 tests** de régression de `tests/test_regression_review.py` échouaient
sur la version auditée. Ils passent désormais ; ils sont nommés d'après la
numérotation de la revue (4 pour le point 1, 3 pour le 2, 6 pour le 3, 3 pour
les points 4 à 7, 10 pour le 8, 3 pour le 9). Les 8 derniers portent sur les
défauts trouvés **en vérifiant les corrections** (§7.2) : eux aussi ont d'abord
été écrits pour échouer.

### 7.1 Défauts reproduits, puis corrigés

| # | Défaut reproduit | Correction | Test |
|---|---|---|---|
| 1a | Rencontre du 12/09 analysée le 13/09 sans horaire : « Match nul @ 3.40 » recommandé alors qu'aucune rencontre ne figurait au calendrier | `KickoffStatus.UNVERIFIED` ; la tolérance ±3 jours qui glissait silencieusement une date sur une autre est supprimée — date exacte ou rien, avec les rencontres proches signalées comme candidates | `test_r1a_*` |
| 1b | `build_sport_input()` retenait les scores du jour même avec un `as_of` à minuit | Coupure sur la **date de disponibilité** (date du match + 1 j pour une source sans horaire), pas sur la date du match ; `SportInput.knowledge_cutoff` et `cutoff_rule` sont portés jusqu'au rapport | `test_r1b_*` |
| 2 | `ManualProvider` et `FootballDataProvider` ne satisfaisaient pas `SeasonSource` : les options CSV n'alimentaient rien. Le moteur figeait le premier fournisseur même en panne | `season()` implémenté sur les deux ; le moteur conserve la **liste ordonnée** et bascule sur le suivant | `test_r2a/b/c` |
| 3 | Seules les 3 cotes 1–N–2 atteignaient le sélecteur (3 offres sur 36) | Notation `BTTS:oui=`, `TOTAL:+2.5=`, `DC:1N=`, `DNB:1=`, `AH:H:-0.5=`, `TE:H:+1.5=` ; une ligne absente du catalogue est **construite à la demande** ; la fiche nomme les marchés réellement comparés | `test_r3a–f` |
| 4a | 180 rencontres distinctes → 135 évaluations dont 15 doublons aux dates frontières | Fenêtres **semi-ouvertes** `[coupure, fin)` : 120 évaluations, 0 doublon | `test_r4a` |
| 4b | L'ablation Dixon-Coles opposait un modèle *réglé* à un Poisson *fixe* : trois paramètres variaient | Chaque ablation déclare `varied` et **une seule** dimension ; une ablation à deux dimensions lève une erreur | `test_r4b` |
| 4c | Un gain positif suffisait à afficher « apport confirmé » | Verdict fondé sur l'**IC bootstrap par blocs de la différence appariée** ; un IC contenant zéro donne « NON CONFIRMÉ » | `test_r4c` |
| 5a | « recommandé » possible avec confiance D et `model_converged=False` | Un modèle non convergé renvoie `BLOCKED` ; une confiance D renvoie `NO_BET` | `test_r5a` |
| 5b | Quatre variations fixes de ±15 % présentées comme une résistance au pire scénario | `ScenarioKind` : **sensibilité** (arbitraire), **incertitude d'estimation** (dérivée de σ ≈ √(2/(m·λ)), rapportée mais **non filtrante**), **événement sportif documenté** (refusé sans source) | `test_r5b/c` |
| 6 | `cote_équitable × (1 + EV_min)` : 1,67 au lieu de 1,70 sur 40 %/35 %/25 %, soit +1,95 % d'EV réelle au lieu de +3 % | `SettlementProfile.odds_for_expected_value()` inverse exactement l'affine ; utilisé aussi par la condition d'annulation | `test_r6_*` |
| 7 | Le dossier était scellé avec `evidence` vide alors que des constats citaient des sources | Les preuves du jeu de données (URL, date du fait, date de relevé) traversent `build_sport_input` jusqu'au dossier scellé et au rapport | `test_r7a–d` |
| 8 | Protocole absent du dépôt ; chargeur non raccordé | `protocole/protocole-22-rubriques.{md,json}` ; `EngineConfig.rubrics_path` et `--protocole` ; chaque rubrique déclare **donnée / traitement / effet** et son état d'implémentation | `test_r8a–j` |
| 9 | Un test réseau imprimait « IGNORÉ » puis retournait : compté comme réussi | `unittest.SkipTest` (compris par pytest et par le lanceur maison) ; `Summary` compte **séparément** réussites, échecs et ignorés | `test_r9_*` |

### 7.2 Défauts trouvés en corrigeant

| Défaut | Détection | Correction | Test |
|---|---|---|---|
| Le jeu de contrôle des tests était dégénéré (scores parfaitement séparables) : la vraisemblance n'avait pas de maximum intérieur et le modèle ne convergeait pas — ce que l'ancien code masquait en recommandant quand même | le nouveau garde-fou de convergence a refusé le jeu | jeu de contrôle régénéré avec une variété réaliste de scores, ensemencé donc reproductible | `test_r5a` |
| L'étiquette « réglage de la demi-vie → apport confirmé » signifiait en réalité que la référence battait le réglage : les rôles *avec* / *sans* étaient inversés | lecture de la sortie recalculée | les paires déclarent explicitement quel run porte le composant ; le résultat dit maintenant que le réglage **dégrade** | `test_r4b` |
| `Summary` en `slots=True` cassait tout chargement dynamique du lanceur | le test de régression du point 9 | `slots` retiré, avec la raison en commentaire | `test_r9_*` |
| Collision de nom entre le jeu de suppléments et l'offre construite à la demande | mypy | renommage | — |
| Cotes groupées dans un même champ (`1=1.62 N=4.00 2=5.50 BTTS:oui=1.85`) : seul un jeton par segment `\|` était lu, le bloc restait collé au nom de l'équipe extérieure et une saisie valide devenait « équipe inconnue » | parcours réel `it.1 \| Napoli - Bologna` rejoué après correction | extraction jeton par jeton **à l'intérieur** de chaque champ ; un champ vidé de ses prix est supprimé pour que la forme `@ 2.10 3.40 3.60`, qui doit finir la ligne, reste lisible | `test_r3d/e` |
| Un marché refusé par conception (corners, cartons, buteurs) faisait remonter `UnsupportedMarketError` depuis l'analyse de ligne, dont le contrat est de ne jamais lever | même parcours | `MatchRequest.refused_markets` : le jeton quitte la ligne et la fiche le **nomme** avec le motif du refus | `test_r3f` |
| La composition importée renseignait R10 mais n'atteignait pas le plan T−75/T−60 : la même fiche affichait « composition officielle relevée » **et** « aucune composition relevée à ce jour » | parcours réel avec `--compositions-csv` | `record_supplied_lineups()` transforme les feuilles importées en observation réelle et rend un verdict motivé ; R21 est renseignée | `test_r8g` |
| La grille répondait « fournissez la donnée via `--xg-csv` » alors que le fichier venait d'être fourni mais qu'aucune ligne n'était appariable — une boucle sans issue pour l'opérateur | même parcours | le blocage compte les lignes reçues et dit qu'aucune n'est exploitable, avec la cause probable | `test_r8h` |
| Contre-analyse figée : « les xG […] sont indisponibles ici » sur une fiche affichant deux sections plus haut un ratio buts/xG, et « donnée qui trancherait : la composition officielle » alors qu'elle était citée | lecture de la fiche produite | le mécanisme d'invalidation, la donnée qui trancherait et l'hypothèse de contexte suivent ce qui a réellement été fourni | `test_r8i` |
| Chaque constat recevait **toutes** les clés de preuve du dossier : un coefficient d'attaque était « prouvé » par une feuille de composition | lecture de la section 7 de la fiche | les constats du modèle ne citent que les jeux de données dont ils sont issus ; les suppléments se citent eux-mêmes | `test_r7d` |
| La fiche annonçait « non renseignées faute de source accessible » pour des rubriques sans adaptateur : les trois états étaient reconfondus à l'affichage | lecture de la fiche produite | la section 2 groupe par état et donne pour chacun l'action qui le lèverait | `test_r8j` |

### 7.3 États d'implémentation, distingués précisément

La grille distingue désormais quatre états, et le rapport les affiche :

| Symbole | État | Rubriques ici |
|---|---|---|
| ● | opérationnel | R01–R06, R13, R16–R18, R20, R22 |
| ◐ | opérationnel sur données fournies | R07, R10, R11, R21 — via `--xg-csv`, `--absences-csv`, `--compositions-csv` ; R19 — via les cotes saisies en ligne |
| ○ | développé mais inaccessible | R15 (arbitre, météo : `foot.collect.footballdata` écrit, hôte bloqué) |
| · | non développé | R08, R09, R12, R14 — aucun traitement n'est écrit, et la rubrique dit ce qu'il faudrait (§17.6) |

Les imports opérateur annoncés dans la grille sont **réellement exposés par la
CLI** : `test_r8e` échoue si une option déclarée n'existe pas. Vérifié sur
données réelles (Napoli – Bologna, Serie A 2026-27, `as_of` 13/09/2026 12:00) :

```
sans import           ✗ indisponible : 9   ✓ traitée : 13   (8 marchés cotés comparés sur 36)
avec les 3 imports    ✗ indisponible : 5   ✓ traitée : 17   (3 scénarios sportifs documentés)
```

Une ligne xG portant sur un match absent de l'historique n'est pas comptée comme
fournie : la grille répond alors « *n* ligne(s) importées, aucune exploitable »,
et non « fournissez la donnée ».

### 7.4 Performances recalculées

Validation chronologique, Premier League, 790 matchs réels (openfootball,
saisons 2024-25 à 2026-27), **486 rencontres évaluées hors échantillon** en
4 plis **disjoints** :

```
  réglé (régularisation)     RPS=0.21000   skill +8.35%
  référence                  RPS=0.21040   skill +8.17%
  sans correction bas scores RPS=0.21052   skill +8.12%
  sans régularisation        RPS=0.21075   skill +8.02%
  réglé (demi-vie)           RPS=0.21154   skill +7.67%
  sans décroissance          RPS=0.21180   skill +7.56%
  taux de base               RPS=0.22912
  IC 95% du RPS (bootstrap par blocs) : [0.20094, 0.22104]

  correction bas scores (rho)          gain +0.00012  IC [-0.00023, +0.00041]  NON CONFIRMÉ
  décroissance temporelle              gain +0.00140  IC [-0.00020, +0.00296]  NON CONFIRMÉ
  régularisation (ridge)               gain +0.00035  IC [-0.00009, +0.00082]  NON CONFIRMÉ
  réglage chrono. de la demi-vie       gain -0.00114  IC [-0.00225, -0.00014]  NON CONFIRMÉ
  réglage chrono. du ridge             gain +0.00040  IC [-0.00026, +0.00103]  NON CONFIRMÉ
  modèle d'équipes                     gain +0.01872  IC [+0.00830, +0.02921]  apport confirmé
```

**Lecture.** Un seul composant confirme son apport : le modèle d'équipes. Le
réglage chronologique de la demi-vie **dégrade** le score, et son intervalle est
entièrement négatif — c'est un résultat défavorable au système, il est publié
tel quel. Les chiffres diffèrent de ceux du commit `8813955` (501 évaluations)
précisément parce que les fenêtres ne se chevauchent plus.

### 7.5 Vérifications

```
pytest                     241 réussis, 1 ignoré
python3 tests/run_tests.py 241 réussi(s), 0 échec(s), 1 ignoré(s) sur 242
ruff check .               propre
mypy .                     propre, 79 fichiers
```

Les performances de §7.4 ont été **recalculées après ces corrections** et sont
identiques au chiffre près : aucune des corrections de cette passe ne touche à
l'estimation, elles portent sur la saisie, la traçabilité et la restitution.

---

## 8. Seconde revue indépendante du commit `6ee19c2` — corrections

`tests/test_regression_review2.py` compte **34 tests**. Les 28 premiers ont été
écrits avant toute correction : **27 échouaient** sur `6ee19c2`, le 28ᵉ
(`test_d4e`) protégeait un comportement déjà correct. Les 6 suivants portent sur
des défauts trouvés **en vérifiant** les corrections (§8.2) et sur la
correspondance avec le protocole. Chacun exerce le **parcours utilisateur**, pas
seulement la fonction interne.

### 8.1 Défauts reproduits, puis corrigés

| # | Défaut reproduit sur `6ee19c2` | Correction | Test |
|---|---|---|---|
| 1 | `ZeroDivisionError` dans `_documented_scenarios()` sur un import xG valide : un match sans but donnait un ratio nul, et **tout le lot** était perdu | `foot/analysis/xg.py` : le rapport buts/xG devient la moyenne a posteriori d'un modèle **Gamma-Poisson** conjugué, `(buts + k)/(xG + k)`. Finie pour toute entrée finie, elle tend vers le rapport brut quand l'échantillon grandit et vers 1 quand il est mince. L'incertitude a posteriori — pas un seuil arbitraire — décide si un écart mérite un scénario | `test_d1a–d` |
| 2 | La coupure de disponibilité ne portait que sur l'estimation : les scénarios lisaient encore le résultat du jour (« 4 buts pour 1.00 xG ») | `SportInput` porte désormais les **suppléments filtrés** : estimation, constats, scénarios, contrôle des compositions et décision lisent tous la même entrée coupée. Trois dates distinguées — fait, publication, récupération ; sans heure de publication, l'antériorité dans la journée n'est pas démontrable et la ligne n'est réputée connue que le lendemain | `test_d2a–d` |
| 3 | Une feuille du 1er août 2025 devenait « composition officielle » d'un match du 14 septembre 2026 ; `any(CONFIRMED)` étendait le statut d'une ligne à tout le relevé ; le gardien retenu pouvait être sur le banc | Une feuille est lue **par équipe et par rencontre** (`lineup_for`), garde son **propre** statut, et déclare sa **complétude** (11 titulaires). Une nouvelle feuille **remplace** la précédente, l'ancienne restant visible dans `superseded`. Les absences portent une fenêtre de validité (`jusqu_au`, `retour`, sinon 21 jours déclarés comme hypothèse) | `test_d3a–f` |
| 4 | `ManualProvider.odds()` servait 3 prix, **0** atteignait le sélecteur ; 90 matchs importés donnaient **270** au dossier | Protocole `OddsSource` consulté **après le scellement** ; déduplication des matchs et des rencontres par `(date, domicile, extérieur)` ; `--calendrier-csv` et `--cotes-relevees` exposés par la CLI ; l'heure de relevé d'un prix importé n'est plus écrasée | `test_d4a–g` |
| 5 | Une absence de gardien produisait exactement la même baisse de 15 % du rythme **offensif** qu'une absence de buteur (1.751745 → 1.488983 dans les deux cas) ; `gate_kinds` incluait SENSITIVITY contre ce qu'affirmait la doc | `foot/analysis/absence.py` : barème **par poste**, agissant sur le bon canal — un gardien absent fait monter l'attaque **adverse**, pas baisser la sienne — atténué de moitié si un remplaçant est nommé, borné en composition. Les trois rôles de scénario sont désormais séparés et nommés : **écarter** (événement documenté), **déclasser** (sensibilité), **informer** (incertitude d'estimation) | `test_d5a–e` |
| 6 | `command_web()` n'offrait ni ne transmettait les imports xG, absences, compositions | `analyse_form()` est le **point d'entrée unique** du navigateur et des tests, appelant le même `engine.run()` que la CLI ; formulaire avec les trois zones de collage, lignes rejetées affichées pour correction, contexte réellement retenu affiché, mise en page téléphone | `test_d6a–d` |
| 7 | La validation mesurait un ridge **fixe** tandis que le moteur en recommande un **adaptatif** : les chiffres publiés décrivaient une configuration que personne n'exécute | La variante « configuration de production » est ajoutée aux runs mesurés ; un test échoue si la constante et `EngineConfig.ridge_pseudo_matches` divergent | `test_d7a/b` |

### 8.2 Défauts trouvés en vérifiant les corrections

| Défaut | Détection | Correction | Test |
|---|---|---|---|
| Une rencontre absente du calendrier s'affichait « hors prématch », ce qui dit que le coup d'envoi est passé — l'opérateur était envoyé chercher une heure qui n'a jamais existé | parcours réel `Arsenal – Chelsea`, rejoué en CLI | état « non vérifiée » distinct, avec son propre compteur au récapitulatif | `test_d4f` |
| Le risque principal annonçait « analyse de sensibilité » y compris quand le pire cas venait d'un **événement documenté** | lecture de la fiche produite sur données réelles | la ligne nomme le type dont vient le pire cas, et rapporte séparément la fragilité en sensibilité (qui déclasse, n'écarte pas) | vérifié au parcours §8.4 |
| Une rubrique composite (R07 : buts, xG, npxG, xGA, tirs, grosses occasions, qualité des tirs) passait « traitée » sur un seul xG importé | lecture de la grille | `Rubric.sub_requirements` : la fiche nomme ce qui est couvert et ce qui manque ; l'état devient **partielle** | `test_d5e` |

### 8.3 Correspondance avec le protocole original

Le texte intégral fourni est stocké tel quel dans
[`protocole/FOOT_Protocole_original_22_rubriques.md`](protocole/FOOT_Protocole_original_22_rubriques.md).

Les numéros R01–R22 du moteur **ne sont pas** ceux du protocole : les constats
portent des numéros codés en dur, les renuméroter les casserait silencieusement.
La correspondance est donc déclarée par rubrique (`Rubric.protocol_sections`) et
exportée dans le protocole généré. Les **21 sections méthodologiques** (§2 à §22)
sont toutes rattachées à au moins une rubrique.

### 8.4 Parcours réel vérifié

Même lot de 3 rencontres, exécuté en CLI puis dans le navigateur, avec et sans
imports (données openfootball réelles, `as_of` 13/09/2026 12:00) :

```
CLI 3 lignes restituées · navigateur 3 lignes restituées
empreintes de dossier et décisions : IDENTIQUES sur les 3
contexte retenu : 4 ligne(s) xG, 2 absence(s) · rejets : 0
1  SSC Napoli – Bologna FC 1909   Victoire extérieur (2)  5.50  +27.4%  B  recommandé
2  Arsenal FC – Chelsea FC        —                        —      —      —  non vérifiée
3  Dijon – Sochaux                —                        —      —      —  équipe inconnue
```

Serveur réellement démarré et interrogé : `GET /` → 200 avec les trois zones de
contexte ; `POST /` → 200 avec l'analyse, la ligne invalide signalée et les
marchés comparés ; accès depuis une autre machine du réseau vérifié avec
`--hote 0.0.0.0`.

### 8.5 Performances, avec la configuration réellement recommandée

Même campagne que §7.4 (Premier League, 790 matchs réels, 4 plis disjoints,
486 rencontres hors échantillon), la variante de production incluse :

```
  réglé (régularisation)       RPS=0.21000   skill +8.35%
  configuration de production  RPS=0.21024   skill +8.24%   ← celle qui recommande
  référence                    RPS=0.21040   skill +8.17%
  sans correction bas scores   RPS=0.21052   skill +8.12%
  sans régularisation          RPS=0.21075   skill +8.02%
  réglé (demi-vie)             RPS=0.21154   skill +7.67%
  sans décroissance            RPS=0.21180   skill +7.56%
  taux de base                 RPS=0.22912
```

**Lecture.** La configuration de production bat la référence à ridge fixe sans
atteindre la meilleure variante. Les verdicts d'ablation sont inchangés : seul
le modèle d'équipes confirme son apport (+0,01872, IC [+0,00830, +0,02921]) ;
le réglage chronologique de la demi-vie dégrade (−0,00114, IC entièrement
négatif). Aucun avantage de rentabilité n'est revendiqué : sans cotes relevées
au moment de la décision, seule la qualité probabiliste est mesurée.

### 8.6 Vérifications

```
pytest -m "not network"      267 réussis, 1 ignoré, 8 déselectionnés
pytest -m "network"          8 réussis
python3 tests/run_tests.py --sans-reseau
                             267 réussi(s), 0 échec(s), 1 ignoré(s) sur 268
ruff check .                 propre
mypy .                       propre, 82 fichiers
```

L'ignoré est `test_r9_sample`, volontairement ignoré pour prouver que le
compteur distingue bien « ignoré » de « réussi ».

Contrôles GitHub Actions ajoutés dans
[`.github/workflows/verification.yml`](.github/workflows/verification.yml) :
un job **bloquant** (lanceur sans dépendance, pytest hors réseau, ruff, mypy,
et vérification que le protocole stocké correspond au code) et un job **réseau
informatif** (`continue-on-error`), parce qu'un hôte injoignable est un fait
d'environnement et non un défaut du code.

**Un défaut de la CI trouvé par la CI.** Le premier passage du workflow a échoué
sur `ruff` : installé sans version épinglée, il avait stabilisé `PLR0917` (trop
d'arguments positionnels) depuis la version locale. Une vérification qui change
de verdict sans qu'une ligne de code ne bouge ne dit rien sur le code. Deux
corrections :

- les quatre signatures signalées sont passées en **arguments par mot-clé** —
  la règle avait raison sur le fond : un appel à neuf arguments positionnels
  s'inverse sans bruit ;
- `pytest`, `ruff` et `mypy` sont désormais **épinglés** dans le workflow, aux
  versions contre lesquelles ces chiffres ont été obtenus. Mettre à jour un
  outil devient un changement délibéré.

Le second passage a échoué sur `mypy`, pour une raison voisine : en CI `pytest`
est importable, donc `pytest.mark.network` a un type concret que la branche de
repli (`None`) contredisait ; ici `pytest` vit dans un interpréteur séparé et
mypy ne le voit pas, donc l'erreur ne pouvait pas apparaître localement. Le
marqueur est désormais annoté de façon à se vérifier **à l'identique dans les
deux cas**.

**Limite connue du poste de développement** : `pytest` n'y étant pas importable
par l'interpréteur qui exécute `mypy`, la vérification de types locale est
strictement plus faible que celle de la CI sur les fichiers qui l'importent.
C'est la CI qui fait foi.

Le job **réseau a réussi sur GitHub Actions**, là où `football-data.co.uk` est
bloqué dans l'environnement de développement : le blocage constaté ici ne décrit
donc pas l'environnement d'exécution final, comme la revue l'avait noté.

### 8.7 Ce qui reste à développer

Ces points relèvent de la **seconde livraison** annoncée par la revue. Ils sont
nommés ici comme reste-à-faire, pas comme fonctions :

| Exigence du protocole | État réel |
|---|---|
| §7 cartons rouges, §21 xG à 11 contre 11 et selon l'état du score | **non développé** — exige des événements horodatés ; aucun adaptateur écrit, aucune entrée opérateur définie |
| §10 entraîneur, changement de système ; §11 confrontation des styles | **non développé** — aucune source ni schéma d'import |
| §21 coups de pied arrêtés, profondeur du banc | **non développé** |
| §12 météo, pelouse, arbitre | **développé mais inaccessible** — `foot.collect.footballdata` écrit, hôte bloqué par la politique de sortie de cet environnement |
| Barème d'impact par poste (`ROLE_IMPACTS`) | **hypothèse déclarée** — oriente le canal ; son apport prospectif n'est pas mesuré hors échantillon |
| Régularisation du ratio buts/xG (`PSEUDO_GOALS = 4`) | **hypothèse déclarée** — non calibrée hors échantillon |
| Mesure des décisions (rendements, évolution jusqu'à la clôture) | **non fait** — exige des prix relevés avant match et conservés ; aucun fournisseur de cotes n'est accessible ici |

---

## 9. Troisième revue indépendante du commit `8ed1584` — corrections

Les **16 tests** de `tests/test_regression_review3.py` exercent le parcours
utilisateur — `Engine.run`, `analyse_form`, et la commande `analyser` telle
qu'un opérateur l'invoque. **11 des 13 premiers échouaient** sur `8ed1584` ;
les deux autres, `e2c` et `e3c`, gardaient un comportement déjà correct. Les 3
derniers portent sur des défauts trouvés en vérifiant les corrections.

### 9.1 Défauts reproduits, puis corrigés

| # | Reproduit sur `8ed1584` | Correction | Test |
|---|---|---|---|
| 1 | Cote relevée 48 h avant : `Engine.run` gardait l'ancienneté (« aucun pari »), `analyse_form` la ramenait à **zéro** (« recommandé ») | L'âge appartient au **prix**, pas au run : `Quote(price, quoted_at, bookmaker)` porte l'heure et la source de chaque cote jusqu'au sélecteur. Une cote tapée à l'instant et une cote importée de l'avant-veille coexistent sur la même rencontre, chacune avec son vrai âge | `test_e1a–c` |
| 2 | Onze joueurs probables à 18 h puis les **mêmes** confirmés à 19 h 30 : les onze lignes officielles rejetées comme doublons, feuille restée probable | La déduplication porte sur la **version** de la feuille — `(date, publication, source, statut)` — et non sur le joueur. `lineup_versions()` groupe et **trie**, `lineup_for()` rend la dernière disponible, les précédentes sont enregistrées comme remplacées. Résultat indépendant de l'ordre des lignes | `test_e2a–c` |
| 3 | Blessure annoncée le 12, retour confirmé le 13, toutes deux connues : `absences_for` gardait la blessure | L'état de chaque joueur est **résolu chronologiquement** avant tout calcul : la dernière déclaration connue à `as_of` décide. Un retour confirmé annule, une rechute ultérieure rétablit. Les déclarations se dédupliquent aussi par version, sinon la ligne du retour disparaissait | `test_e3a–d` |
| 4 | Fuseau UTC, analyse à 12 h : une absence publiée à 13 h était **exclue** au navigateur et **intégrée** en terminal, la CLI lisant « 13:00 » en Europe/Paris | `foot/analysis/journey.py` : une seule fonction lit les entrées, horodate le contexte et appelle le moteur. `analyse_form` et `command_analyser` l'appellent tous deux — il n'y a plus rien à garder synchronisé, donc plus rien qui puisse diverger | `test_e4a–d` |

### 9.2 Défauts trouvés en vérifiant les corrections

| Défaut | Détection | Correction | Test |
|---|---|---|---|
| Le registre de preuves filtrait le contexte par **date** et non par disponibilité : une absence refusée par le dossier y figurait encore comme source | test `e4a`, qui inspecte le registre | `build_sport_input` dérive les preuves du contexte du jeu **déjà coupé** : le registre et le dossier ne peuvent plus se contredire | `test_e4a` |
| Sans horodatage, une blessure de la veille est admise et le retour du jour ne l'est pas — on retenait l'information défavorable et on écartait la favorable, **du même émetteur**, en silence | lecture de la fiche produite | La règle de disponibilité reste inchangée, elle est juste ; mais `pending_absence_updates()` nomme les déclarations refusées, et la fiche les affiche avec le geste qui les rendrait exploitables | `test_e3e/f` |
| Le résumé de contexte était formulé différemment au terminal et au navigateur | comparaison des deux sorties | même libellé des deux côtés, produit par `JourneyResult.render_context()` | `test_e4d` |
| Un message d'erreur de date en **anglais** dans une interface française, affichant des motifs `strptime` | POST réel sur le serveur | message en français nommant des exemples de formats | — |

### 9.3 Avant / après, sur le parcours réel

```
1. cote importée relevée 48 h avant l'analyse
   avant   Engine.run   : âge 48 h · aucun pari
           analyse_form : âge  0 h · recommandé        ← divergence
   après   Engine.run   : âge 48 h · aucun pari
           analyse_form : âge 48 h · aucun pari

2. feuille probable (18 h) puis officielle (19 h 30), même onze
   avant   lignes 11 · rejets 11 · officielle False · remplacées 0
   après   lignes 22 · rejets  0 · officielle True  · titulaires 11 · remplacées 1

3. blessure 12/09 puis retour confirmé 13/09, horodatés
   avant   absences actives ['Martin'] · scénario « absences décisives »
   après   absences actives []         · aucun scénario d'absence

4. fuseau UTC, analyse 12 h, absence publiée à 13 h
   avant   CLI : intégrée au dossier      formulaire : exclue     ← divergence
   après   CLI : exclue et signalée       formulaire : exclue et signalée
```

Lot réel de 3 rencontres (openfootball), joué en terminal puis au navigateur
avec les mêmes imports : **3 lignes restituées de part et d'autre, empreintes de
dossier et décisions identiques**, même contexte retenu (4 lignes xG, 2
absences), aucun rejet. Serveur démarré et interrogé : `GET` et `POST` à 200, le
contexte retenu et la ligne invalide affichés.

### 9.4 Vérifications

```
pytest -m "not network"      283 réussis, 1 ignoré, 8 déselectionnés
pytest -m "network"          8 réussis
python3 tests/run_tests.py --sans-reseau
                             283 réussi(s), 0 échec(s), 1 ignoré(s) sur 284
ruff check .                 propre
mypy .                       propre, 84 fichiers
```

---

## 10. Quatrième revue indépendante du commit `627f447` — corrections

Deux problèmes temporels. Les **12 tests** de `tests/test_regression_review4.py`
échouaient tous sur `627f447` et passent par les entrées réelles — formulaire et
ligne de commande.

### 10.1 Défauts reproduits, puis corrigés

| # | Reproduit sur `627f447` | Correction | Test |
|---|---|---|---|
| 1a | Cote importée **sans heure de relevé** : le prix recevait l'heure de l'analyse, son âge devenait zéro, la décision « recommandé » | Dans `price_catalogue`, une `Quote` dont `quoted_at` vaut `None` ne reprend plus l'horodatage global : elle garde un **âge inconnu**. Le sélecteur écarte alors ce prix avec « ancienneté inconnue — confirmez l'heure de relevé avant de jouer ce prix » | `test_f1a/b` |
| 1b | Cote relevée **24 h après** `as_of` : âge −24 h, `is_stale()` renvoyait `False`, décision « recommandé ». Le contrôle portait sur les prix trop vieux, pas sur ceux venus du futur | Un prix postérieur à l'instant d'analyse n'existait pas à cet instant : il est **exclu de cette analyse** dès le calcul, avec son motif porté par `PricedOffer.exclusion`. Les autres marchés valides sont conservés et comparés | `test_f1c–f` |
| 2 | Une déclaration publiée après `as_of` était réintroduite dans les **constats du dossier scellé** avec le type `FACT` : l'empreinte changeait (`77bd5949…` → `239d908a…`). Le résumé annonçait « 2 absences » en comptant les lignes lues | Les avertissements d'import quittent le dossier scellé pour le **compte rendu d'import** (`MatchAnalysis.import_notes`, agrégé par `JourneyResult.notes`). Le décompte porte sur ce que le moteur pouvait voir à `as_of`, pas sur ce que le fichier contenait | `test_f2a–c/e/f` |
| 2b | Le message réclamait « ajoutez l'heure de publication » **alors qu'elle existait** — il envoyait corriger un fichier correct | `AbsenceRow.unavailable_because()` : deux causes, deux messages. Une publication postérieure à l'analyse n'est pas une publication sans heure, et une seule des deux est du ressort de l'opérateur | `test_f2d` |

### 10.2 Une distinction que la correction a rendue nécessaire

Refuser de dater un prix sans heure a d'abord fait tomber une cote **saisie sur
la ligne de match**, que rien n'horodatait explicitement. La règle juste tient en
une phrase :

- une cote **tapée par l'opérateur** est donnée maintenant : sans heure
  explicite, elle est datée de l'instant d'analyse, qui est le moment où il l'a
  lue ;
- une cote **importée** garde l'heure que sa source déclare ; si la source n'en
  déclare aucune, son âge est réellement inconnu, et l'inventer fabriquerait une
  fraîcheur qu'elle n'a jamais eue.

De même, la vérification d'ancienneté n'existe que pour une analyse **datée** :
sans `as_of`, il n'y a pas de contrôle de fraîcheur à échouer.

### 10.3 Avant / après

```
1. cote importée sans heure de relevé
   avant   quoted_at = heure d'analyse · âge 0 h · recommandé
   après   quoted_at = None            · âge inconnu · aucun pari
           motif : « ancienneté inconnue : aucune heure de relevé fournie —
                     confirmez l'heure de relevé avant de jouer ce prix »

2. cote relevée 24 h APRÈS l'analyse
   avant   âge −24 h · is_stale() False · recommandé
   après   prix exclu · « cote relevée le 2026-09-14 12:00 CEST, postérieure à
           l'heure d'analyse : exclue de cette analyse » · sous condition de prix

3. retour publié à 18 h, analyse à 12 h
   avant   empreinte 77bd5949c3e06b24 → 239d908a59a5223c · 10 findings · « 2 absences »
   après   empreinte 77bd5949c3e06b24 → 77bd5949c3e06b24 ·  9 findings · « 1 absence »
           compte rendu d'import : « publication du 2026-09-13 18:00 CEST
           postérieure à l'heure d'analyse (2026-09-13 12:00 CEST) : hors de ce dossier »

4. deux causes, deux messages
   publication future  → « postérieure à l'heure d'analyse : hors de ce dossier »
   publication absente → « sans heure de publication : l'antériorité dans la
                           journée n'est pas démontrable »
```

Terminal et navigateur produisent des **notes et un décompte identiques**,
vérifié sur le parcours réel ; le serveur affiche l'exclusion dans un encart
« Lignes lues mais écartées de cette analyse ».

### 10.4 Vérifications

```
pytest -m "not network"      295 réussis, 1 ignoré, 8 déselectionnés
pytest -m "network"          8 réussis
python3 tests/run_tests.py --sans-reseau
                             295 réussi(s), 0 échec(s), 1 ignoré(s) sur 296
ruff check .                 propre
mypy .                       propre, 85 fichiers
```

---

## 11. Usage quotidien depuis le téléphone — travaux à partir de `9cb285f`

Objectif : saisir des rencontres depuis un téléphone et obtenir une analyse
sourcée puis un marché au prix disponible, **sans préparer de CSV** dans le
parcours normal.

### 11.1 Ce qui fonctionne automatiquement, aujourd'hui, sans clé

| Fonction | Preuve |
|---|---|
| Calendrier, statut et résultats des 5 grands championnats | `foot fournisseurs` : openfootball **OK**, couverture vérifiée `en.1, es.1, de.1, it.1, fr.1` en 2026-27 |
| Dossier sportif scellé avant toute lecture de cote | empreinte affichée sur chaque fiche, `R03` ✓ |
| Comparaison des marchés cotés, avec règlement exact | `R19`, `R20` ✓ |
| Décision, confiance, risque principal, condition d'annulation | `R22` ✓ |
| **Contrôle T−75/T−60 exécuté**, avec nouvelles tentatives jusqu'au coup d'envoi | `foot suivre` ; `tests/test_daily_use.py` (6 tests) |
| **Journal des prévisions** en ajout seul, révision distinguée d'une reconduction | `foot journal` ; `tests/test_daily_use.py` (6 tests) |
| **Mesure du journal** : RPS, calibration, références et intervalle | `foot mesurer` ; §12–13, mesuré sur 80 rencontres réelles disjointes |
| **Accès privé + HTTPS + sauvegarde côté serveur** | `foot web --jeton --certificat --cle --journal` ; 5 tests sur serveur réel |
| Restitution de **chaque ligne saisie**, ambiguïtés nommées avec la façon de trancher | « Contrôle : chaque ligne saisie apparaît bien ci-dessus. » |

### 11.2 Ce qui attend une activation (code écrit et testé, clé à poser)

| Fonction | Clé | Coût annoncé | Ce que la sonde vérifiera |
|---|---|---|---|
| Compositions officielles automatiques (`R10`, `R21`) | `FOOTBALL_DATA_ORG_TOKEN` | gratuit 12 compétitions / 10 req·min ; ≈ 20 €/mois plan « One » | **le plan gratuit ne sert pas les compositions** : la sonde lit la réponse reçue et le dit, plutôt que de croire la documentation |
| Cotes prématch automatiques (`R19`) | `ODDS_API_KEY` | gratuit 500 req/mois ; ≈ 30 $/mois au-delà | quels bookmakers et quels marchés votre plan renvoie réellement |

Adaptateurs écrits, testés sur **réponses enregistrées** (`tests/test_live_integrations.py`,
24 tests, sans réseau ni clé). `foot config` affiche les coûts **avant** tout
engagement ; le logiciel ne souscrit à rien.

### 11.3 Ce qui reste à développer

| Manque | Conséquence | Ce qu'il faudrait |
|---|---|---|
| Absences et blessures (`R11`) | rubrique « à fournir par l'opérateur » | adaptateur API-Football — **catalogué volontairement comme non développé**, c'est le premier à écrire |
| xG, npxG, tirs (`R07`, `R08`, `R09`, `R14`) | 4 rubriques bloquées | même adaptateur, ou une source xG dédiée |
| Entraîneur et styles (`R12`) | rubrique bloquée | aucune source cataloguée |
| Météo, pelouse, arbitre (`R15`) | rubrique bloquée | football-data.co.uk sert l'arbitre mais est **injoignable depuis cet environnement** (403 du proxy) |
| Cotes de **clôture** pour l'écart de timing | l'écart au prix de clôture reste non mesuré | football-data.co.uk les sert et l'adaptateur existe, mais il est **injoignable ici** ; `foot mesurer` dit alors « non mesuré » plutôt que de se taire |

Aucune de ces rubriques ne produit d'affirmation : le rapport les marque
indisponibles et nomme, pour chacune, l'action exacte qui la lèverait —
« clé `FOOTBALL_DATA_ORG_TOKEN` à fournir », « accès réseau à ouvrir »,
« adaptateur à écrire », ou « donnée à fournir ».

### 11.4 Le parcours complet sur trois rencontres réelles

```console
$ python3 -m foot analyser --fichier trois.txt --date 2026-09-13T10:00 \
      --bookmaker Betclic --rubriques --journal --motif "démonstration"
```

```
RÉCAPITULATIF — 3 rencontre(s) demandée(s)
#    Rencontre                           Choix principal            Cote   Prob  EV       Conf  Décision
1    Manchester Unit – Manchester City   Victoire extérieur (2)     2.65   48%   +26.2%   B     recommandé
2    SSC Napoli – Bologna FC 1909        Victoire extérieur (2)     5.50   23%   +27.4%   B     recommandé
3    Levante UD – FC Barcelona           —                          —      —     —        D     aucun pari
Total : 3 demandée(s) · 3 analysée(s) · 2 recommandation(s) · 0 à préciser
Contrôle : chaque ligne saisie apparaît bien ci-dessus.

Journal : 3 prévision(s) ajoutée(s) à .foot-journal.jsonl. Rien n'y est jamais réécrit.
```

Rencontres, cotes et historiques sont réels (openfootball, 790 matchs de Premier
League et 780 de Serie A). **Ces trois rencontres démontrent le fonctionnement ;
elles ne constituent en aucun cas une validation des performances.** Aucune cote
de clôture n'est accessible ici, donc aucun rendement n'est mesurable ; c'est
précisément ce que le journal sert à préparer.

Couverture effective des 22 rubriques sur ces trois fiches : **13 sur 22 (59 %)**,
identique pour les trois, la limite étant la même pour toutes — aucune source
d'absences, de xG ni de compositions n'est active dans cet environnement.

### 11.5 Échecs et données manquantes, tels qu'affichés

```
# ligne non identifiable — et aucune suggestion fantaisiste
Ligne 2 : Zorglub - Machin
  STATUT : analyse non produite
  équipe inconnue — manque : « Zorglub » et/ou « Machin » introuvables dans
  les compétitions couvertes (de.1, en.1, es.1, fr.1, it.1)

# nom ambigu : les candidats, et comment trancher
Ligne 3 : United - City
  candidats possibles : Leeds United FC, Manchester United FC,
                        Newcastle United FC, West Ham United FC
  pour trancher : reprenez le nom complet d'un candidat, ou retirez la date
  pour laisser le calendrier la fixer.

# rubrique bloquée : le remède exact, pas un mot vague
✗ ○ R21 Compositions probables puis officielles
      adaptateur foot.collect.footballdata_org écrit ;
      compositions : clé à fournir (FOOTBALL_DATA_ORG_TOKEN pour
      football-data.org) — voir « foot config »

# suivi qui ne trouve rien : c'est une information, pas un silence
  4 tentative(s)
  01:03 CEST — aucune composition publiée
  DERNIÈRE VÉRIFICATION RÉUSSIE : aucune. Les compositions n'ont pas été
  publiées, ou la source ne les sert pas.
  arrêt : fin de la fenêtre de contrôle
```

### 11.6 Deux décisions de conception qu'il faut connaître

**Une feuille de composition est ajoutée entière, jamais fusionnée joueur par
joueur.** Mélanger les onze d'une source avec ceux d'une autre fabriquerait une
composition que personne n'a publiée. À heure de publication égale, la saisie de
l'opérateur tient ; une feuille réellement postérieure la remplace, et la
précédente reste visible comme version remplacée.

**Le recoupement est dit, jamais sous-entendu.** Chaque fiche porte désormais
une ligne « recoupement » : combien de faits décisifs reposent sur deux
fournisseurs **indépendants**, et combien sur une source unique. Sans elle,
l'absence de contradiction se lisait comme un accord — alors qu'avec un seul
fournisseur joignable, il n'y a rien à recouper. Le décompte porte sur les
fournisseurs, jamais sur les URL : un même flux republié cinq fois reste une
source, et un test le vérifie.

**Une suggestion doit ressembler à ce qui a été tapé.** Le seuil des
suggestions passe de 0,45 à 0,55 : à 0,45, « Machin » proposait Milan, Monaco et
Manchester City, ce qui, sur un téléphone, invite à choisir l'une des trois. À
0,55, une vraie faute de frappe trouve toujours son club (« Napli » → SSC
Napoli, « Bayrn Munich » → Bayern München) et un nom qui ne ressemble à rien ne
propose rien. Le seuil de *résolution*, lui, n'a pas bougé.

**Une prévision reconduite n'est pas une révision.** Le dossier est daté : son
empreinte change à chaque relecture, même quand rien d'autre ne change. Le
journal compare donc la **substance** — probabilités, marché retenu, cote,
confiance — et écrit « inchangé » quand c'est le cas. Sans cela, un contrôle de
routine à T−60 se serait lu comme un changement d'avis.

### 11.7 Vérifications

```
pytest -m "not network"      349 réussis, 1 ignoré
pytest -m "network"          8 réussis (GitHub Actions ; bloqués dans cet
                             environnement, dont l'AUDIT §2 dit pourquoi)
python3 tests/run_tests.py --sans-reseau
                             349 réussi(s), 0 échec(s), 1 ignoré(s) sur 350
ruff check .                 propre
mypy .                       propre, 93 fichiers
protocole synchronisé        protocole/protocole-22-rubriques.json == dump_rubrics()
```

54 tests ajoutés (`tests/test_live_integrations.py`, `tests/test_daily_use.py`),
tous hors réseau et sans clé.


---

## 12. Mesurer ce qui a été prévu — `foot mesurer`

La §6 demandait deux choses : **conserver** les prévisions, puis les **évaluer**.
La première était livrée au §11 ; la seconde l'est ici.

### 12.1 La règle qui gouverne le module

Le journal écrit **avant** le match ; la mesure lit **après**, et **n'écrit
rien**. Un test vérifie littéralement que le fichier est identique octet pour
octet après un rapport complet : une mesure qui pourrait corriger la prévision
qu'elle évalue ne mesurerait rien.

Trois autres refus, chacun testé :

* une rencontre non jouée, ou introuvable dans les résultats chargés, reste **en
  attente** et n'entre dans aucune moyenne ;
* seule la **dernière** prévision d'une rencontre est notée. Noter chaque
  révision compterait une rencontre plusieurs fois et récompenserait celui qui
  révise le plus souvent ;
* deux rencontres entre les mêmes équipes sans date enregistrée : **aucune**
  n'est retenue, parce qu'en choisir une noterait peut-être la mauvaise.

Le règlement d'un pari passe par la **clé de catalogue** enregistrée au journal,
pas par son libellé : « Victoire extérieur (2) » ne se règle pas, `1X2:A` si.
Remboursements et quarts de ligne sont donc traités exactement comme l'espérance
d'avant-match le supposait — un test le vérifie sur quatre règlements distincts
(gain, perte, remboursement, demi-perte).

### 12.2 Mesure sur un rejeu rétrospectif — et une affirmation corrigée

**Ce qui suit est un rejeu, pas un carnet tenu en avril.** L'historique est
arrêté au 1er avril 2026 et les prévisions sont calculées comme si l'on était à
cette date, mais elles ont été **écrites aujourd'hui**. Le journal le sait : une
ligne dont `recorded_at` est postérieur à son coup d'envoi est marquée
rétrospective et **exclue du bilan par défaut**. Il faut `--avec-rejeux` pour
l'inclure, et le rapport le dit.

**L'affirmation de non-recouvrement du §12.2 précédent était fausse.** Vérifiée
par intersection plutôt qu'asserée : sur les 110 rencontres, **30 étaient dans
l'échantillon de test de la validation chronologique du §8.5** — toutes les
anglaises, puisque cette validation porte sur `en.1`. La mesure est donc reprise
sur les **80 rencontres réellement disjointes** (Espagne et Italie),
intersection vérifiée **nulle**.

Les identifiants des trois échantillons sont publiés dans
[`protocole/echantillons-de-mesure.json`](protocole/echantillons-de-mesure.json)
(`competition|date|domicile|exterieur`), avec les intersections calculées :

| Échantillon | Effectif | ∩ validation §8.5 |
|---|---|---|
| Rejeu avril 2026, complet | 110 | **30** |
| Rejeu avril 2026, disjoint (es.1 + it.1) | 80 | **0** |
| Validation chronologique §8.5 | 486 | — |

```
Mesure de 80 rencontre(s) résolue(s), 0 en attente
  journal                n=80   RPS=0.20551  Brier=0.60361  logloss=1.00940  acc=56.2%
  référence descriptive  n=80   RPS=0.22148  ← calculée APRÈS COUP
  référence antérieure   n=80   RPS=0.22328  ← estimée sur les seules données antérieures
  skill contre la référence descriptive : +7.21% — majorant optimiste
  skill contre la référence antérieure  : +7.96%
  écart de RPS modèle − référence descriptive :
      IC 95% [-0.04789, +0.01594] sur n=80 (négatif = le modèle fait mieux)

  calibration over 80 forecast-outcome pairs (ECE = 0.0571, MCE = 0.1662)
    [0.00, 0.20)  n=33   claimed=0.140  observed=0.212  bias=-0.072
    [0.20, 0.40)  n=140  claimed=0.279  observed=0.243  bias=+0.036
    [0.40, 0.60)  n=52   claimed=0.494  observed=0.577  bias=-0.083
    [0.60, 0.80)  n=14   claimed=0.702  observed=0.571  bias=+0.130
    [0.80, 1.00)  n=1    claimed=0.834  observed=1.000  bias=-0.166

  AVANTAGE NON DÉMONTRÉ sur cet échantillon : l'intervalle de confiance de
  l'écart de RPS contient zéro ([-0.04789, +0.01594], n=80). Le skill positif
  ci-dessus est une estimation ponctuelle, pas une performance établie.
  Aucune rentabilité n'est promise.
```

**C'est le résultat, et il est négatif quant à la démonstration.** Un skill de
+7,2 % sur 80 rencontres a un intervalle de confiance qui contient zéro : le
modèle n'y démontre aucun avantage. Ni 30 rencontres, ni 80, ni un nombre de
tests ne suffisent à établir une fiabilité prédictive, et le rapport refuse
désormais de conclure quand l'intervalle traverse zéro — quel que soit
l'effectif.

Deux références sont affichées, parce qu'elles ne disent pas la même chose :

* la **descriptive** utilise les fréquences de l'échantillon évalué. Elle connaît
  le résultat des rencontres qu'elle sert à juger, donc le skill mesuré contre
  elle est un **majorant optimiste**, pas une performance ;
* l'**antérieure** est estimée sur les seules rencontres jouées **avant** le
  début de l'échantillon. Elle aurait pu être alignée le jour même, ce qui est
  la seule chose qu'un adversaire de référence doive pouvoir faire.

Aucun rendement n'est calculé : aucun prix n'était disponible sur ces rencontres
d'archive. Inventer des cotes pour produire un pourcentage aurait été exactement
le défaut que ce travail cherche à éviter.

### 12.3 Deux corrections rendues nécessaires par la mesure

**Un import manuel ne marquait pas ses résultats.** `ManualProvider` estampillait
sa clé de compétition sur le calendrier mais pas sur les résultats lus du CSV :
un fournisseur qui déclare servir `it.1` renvoyait des matchs étiquetés `None`,
et la mesure ne pouvait apparier aucune prévision à son propre résultat. Corrigé
au niveau du fournisseur, là où l'incohérence se trouvait.

**Le skill n'était pas calculable sur un échantillon dégénéré.** Si toutes les
rencontres mesurées finissent de la même façon, le taux de base de l'échantillon
est parfait par construction, son RPS vaut zéro, et le rapport levait une
exception au lieu de s'afficher. Il le dit désormais en une ligne. Un rapport qui
plante sur un petit journal est un rapport qu'on ne lit jamais.

### 12.4 Le plancher de conclusion

Deux garde-fous, et le second compte davantage :

En dessous de **30 rencontres résolues**, les chiffres sont affichés — les
masquer serait une autre forme de malhonnêteté — mais aucune conclusion n'est
énoncée :

```
  ÉCHANTILLON INSUFFISANT POUR CONCLURE (3 < 30). Les chiffres sont affichés
  parce qu'ils existent, pas parce qu'ils démontrent quoi que ce soit — un
  rendement calculé sur si peu de paris est du bruit.
```

Et quel que soit l'effectif, **un intervalle de confiance qui contient zéro
interdit d'annoncer un avantage**. C'est le cas sur les 80 rencontres du §12.2 :
l'effectif suffit au premier garde-fou, et le second l'arrête quand même.


---

## 13. Cinquième revue indépendante du commit `74d1fa3` — corrections

Neuf défauts reproduits puis corrigés, avec `tests/test_regression_review5.py`
(37 tests qui échouent tous sur `74d1fa3`).

### 13.1 Reproductions, aux valeurs exactes de la revue

| # | Reproduit sur `74d1fa3` | Correction |
|---|---|---|
| 1 | Contrôles à 19:30, 19:45, 19:50 : **trois âges nuls** pour une cote saisie une seule fois | Le suivi fixe l'heure du relevé au premier contrôle et ne la fait plus avancer. Relancer l'analyse ne rend pas un prix plus frais. |
| 2 | Compositions relues au cache de six heures : un « rien publié » lu à T−75 resservi à T−60 | `WATCH_CACHE_TTL` = 120 s, `Cache.with_ttl` : même stockage, fraîcheur adaptée au suivi, compatible avec les quotas. Le véritable adaptateur est testé avec son cache. |
| 3 | Réponse sans composition → `ValueError` (Evidence `UNAVAILABLE` avec valeur), avalée par le moteur | Valeur nulle côté adaptateur ; et l'échec d'une source est **inscrit au registre** au lieu d'être silencieux — « aucune composition publiée » et « source en panne » sont deux états. |
| 4 | Suivi arrêté sur **deux joueurs**, un par équipe, annonçant « composition officielle » | Deux feuilles officielles **complètes** (onze titulaires chacune) **et** le passage du contrôle T−60 — c'est entre T−75 et T−60 que les compositions changent. |
| 5 | `Forecast.key` sans date : 14 et 21 septembre réduits à une rencontre | La date entre dans l'identité. Les anciens journaux restent lisibles sans qu'aucune date leur soit attribuée ; quand une ligne ancienne et une datée tombent sur le même match, la datée l'emporte et l'autre est **déclarée écartée**. |
| 6 | `measure()` : `as_of` explicitement ignoré, une prévision écrite après le match évaluée | Quatre instants distingués — enregistrement réel, instant historique simulé, coup d'envoi, date limite du rapport. La dernière prévision **admissible** est choisie chronologiquement, pas par ordre d'ajout. Un rejeu rétrospectif est exclu du bilan sauf `--avec-rejeux`. |
| 7 | `offer_for_key("OU:4.5:under")` → `OU:4.5:over` | Le mot explicite l'emporte sur le signe ; chaque clé canonique se relit en elle-même. **Le pari inverse était réglé, silencieusement.** |
| 8 | `AH:H:-1.75` et `OU:4.5:over` → `profit=None`, disparus du rendement | L'évaluation règle par `offer_for_key`, le résolveur du moteur. Un marché recommandable est réglable. |
| 9 | « Manchester United » correspond, « Manchester United FC » fait disparaître le prix | Repli par `normalise()`, date et paire toujours exactes, **refus explicite** quand deux événements se replient sur la même paire. `normalise` replie aussi « F.C. » sur « fc ». |
| 10 | `closing = {}`, jamais alimenté : « aucune source joignable » sans la moindre tentative | `foot/analysis/closing.py` collecte par import explicite ou par les sources du registre. `ClosingState` distingue **non raccordé / inaccessible / servi**, et une clôture est appariée par rencontre **et marché**. |

### 13.2 Ce que la revue a révélé au-delà de ses dix points

Deux défauts trouvés en corrigeant, corrigés aussi :

* un contrôle qui ne changeait rien était annoncé « DÉCISION MODIFIÉE » —
  l'empreinte du dossier est datée, donc elle bougeait à chaque relecture. La
  comparaison porte désormais sur la substance ;
* **l'affirmation de non-recouvrement du §12.2 était fausse** : 30 des 110
  rencontres appartenaient à l'échantillon de validation. Vérifié par
  intersection, corrigé, identifiants publiés (§12.2).

### 13.3 Usage quotidien : ce qui a été ajouté

| Fonction | Preuve |
|---|---|
| Trois actions sur l'écran du téléphone — Analyser, Suivre, Bilan | parcours complet exécuté sur serveur réel, les trois boutons appelés sur une rencontre réelle *(une quatrième, Contrôles, s'y ajoute au §17.3)* |
| Suivi exécuté **côté serveur**, survivant à la fermeture de l'onglet | `foot/analysis/supervisor.py`, 5 tests |
| Journal sauvegardable et **restaurable** | fichier en ajout seul ; un test copie, restaure et relit à l'identique |
| Provenance et fraîcheur **rubrique par rubrique** | `foot analyser --provenance` |
| Coûts, quotas et couverture avant tout abonnement | [COUTS.md](COUTS.md), et `foot config` / `foot fournisseurs --couverture` |

### 13.4 Limites restantes, nommées

* **xG et absences : non développées.** C'est le premier manque, il pèse sur 5
  rubriques, et il suppose un adaptateur à écrire **et** un abonnement (§COUTS).
* **Écart au prix de clôture : non mesuré ici.** Le code existe et est testé ;
  la source gratuite qui le servirait est injoignable depuis cet environnement.
  Un import explicite (`--clotures-csv`) contourne la contrainte.
* **Un suivi ne survit pas au redémarrage du serveur.** *(Levée au §17 : les
  suivis sont désormais écrits et repris, et un coup d'envoi passé pendant
  l'arrêt est déclaré manqué plutôt que présenté comme fait.)*
* **Aucun avantage prédictif démontré.** Sur 80 rencontres disjointes,
  l'intervalle de confiance de l'écart de RPS contient zéro (§12.2).

### 13.5 Vérifications

```
pytest -m "not network"      389 réussis, 1 ignoré
python3 tests/run_tests.py --sans-reseau
                             389 réussi(s), 0 échec(s), 1 ignoré(s) sur 390
ruff check .                 propre
mypy .                       propre, 97 fichiers
protocole synchronisé        protocole/protocole-22-rubriques.json == dump_rubrics()
```


---

## 14. Sixième revue du commit `b27d03c` — corrections et adaptateur API-Football

### 14.1 Cinq écarts reproduits, corrigés

| # | Reproduit sur `b27d03c` | Correction |
|---|---|---|
| 1 | Le `Supervisor` du serveur web réutilisait le moteur ordinaire, donc son cache de six heures : compositions publiées à 19:45, **zéro joueur à 19:45 et 19:50** | `_watch_engine()` est la seule porte, empruntée par `foot suivre` **et** par le bouton Suivre. Le test exécute la même scène aux **deux** fraîcheurs dans une seule fonction : c'est la comparaison qui prouve que le réglage est la cause. |
| 2 | Suivi armé à 15:45 pour un match à 20:45 : **premier contrôle à 16:45** au lieu de 19:30, `_sleep` plafonnant à une heure sans que la boucle revérifie | `_wait_until` dort par paliers jusqu'à l'heure prévue et s'arrête si l'horloge n'avance plus. Les cotes gardent l'heure de **lancement**, pas celle du premier contrôle. |
| 3 | `_render_ledger` affichait le journal et renvoyait vers `foot mesurer` | `Supervisor.measure_async` calcule côté serveur, en arrière-plan, avec un avancement lisible ; rappuyer consulte sans relancer ; un échec est rapporté. |
| 4 | `odds()` appelée sans argument (E0 / 2024-25 pour une demande it.1 / 2026-27) ; identifiants « Serie A (Italie) » au lieu de `it.1` ; deux bookmakers écrasant la même clé ; colonnes `B365H` lues comme des clôtures | Protocole `ClosingSource` portant compétition et saison ; clés de compétition normalisées ; le **bookmaker entre dans l'identité**, et « même bookmaker » est distingué d'une « référence de marché » qui n'existe que si les livres s'accordent ; colonnes `B365CH/CD/CA`, et un fichier sans colonnes de clôture ne rend **rien**. |
| 5 | Un rapport daté de la veille du coup d'envoi réglait déjà le pari dès qu'on lui fournissait le résultat futur | La date limite filtre **les deux côtés** : une rencontre non jouée à cette date reste en attente. |

**Défaut trouvé en corrigeant :** `_dataset_evidence` marquait `UNAVAILABLE` une
saison sans match joué tout en portant une valeur, ce qui levait une
`ValueError`. Zéro résultat n'est pas une donnée absente — une saison non
commencée est un fait confirmé, et les rencontres à venir prouvent que la requête
a abouti.

**Le test qui compte.** Le n°1 n'est pas couvert par `Cache.with_ttl` pris
isolément mais par une intégration qui pilote le **véritable adaptateur** — son
cache, son parsing, ses en-têtes — contre des réponses contrôlées. L'adaptateur
reçoit pour cela une horloge et un *fetcher* injectables : un cache dont
l'expiration ne peut être exercée qu'en attendant quinze minutes est un cache que
personne ne teste.

### 14.2 Adaptateur API-Football — `foot/collect/apifootball.py`

22 tests sur réponses enregistrées, sans clé ni réseau. Quatre garanties, chacune
tenue par un test :

**Zéro n'est pas une absence.** Une équipe qui a tiré zéro fois a tiré zéro fois ;
un champ que le plan ne sert pas reste `None`. Les deux traversent le logiciel
différemment.

**Un indicateur générique ne vaut pas confirmation.** `field_coverage` établit la
couverture **champ par champ**, par championnat et par saison, avec trois états
distincts :

```
● xg                     it.1 2026-27 — servi (sur 2 ligne(s))
· xg                     en.1 2026-27 — absent de la réponse
◐ red_cards              it.1 2026-27 — présent, sans valeur
· npxg                   it.1 2026-27 — absent de la réponse
  champs non servis à ce compte : big_chances, npxg, penalties
```

La même réponse sert les tirs sans servir les xG : c'est exactement la confusion
que « statistiques disponibles » entretenait.

**Rien n'est dérivé de ce qui n'est pas servi.** L'API donne des totaux de match ;
elle ne donne pas les xG à onze contre onze. **R08, R09 et R14 restent non
développées**, et un test interdit de les rattacher à cet adaptateur.

**Un doute n'est pas une absence.** « Questionable » est enregistré *probable*,
« Missing Fixture » *confirmé* ; et aucune absence déclarée n'est pas la preuve
d'un effectif au complet — la preuve le dit plutôt que de se taire.

Deux autres refus : une réponse d'erreur renvoyée en **HTTP 200** (clé invalide,
quota épuisé) est un refus explicite et non une ligue vide ; et aucune clé ne peut
atteindre un rapport, ce qu'un test vérifie sur la sonde et sur le tableau.

### 14.3 Raccordement aux rubriques

| Rubrique | Avant | Après |
|---|---|---|
| R07 xG, tirs | aucun adaptateur écrit | adaptateur écrit ; **clé `API_FOOTBALL_KEY` à fournir** |
| R11 absences | aucune source automatique | adaptateur écrit ; **clé à fournir** |
| R10, R21 compositions | une source possible | **deux** sources possibles, nommées |
| R08, R09, R14 | aucun adaptateur | **inchangé, volontairement** |

Pour tout ce qui reste non servi, l'import opérateur demeure ouvert — un test le
vérifie rubrique par rubrique.

**Correction rendue nécessaire :** rattacher R07 à un adaptateur masquait le
message « n lignes importées, aucune exploitable ». Envoyer quelqu'un souscrire un
abonnement pour corriger une faute de frappe dans un nom d'équipe est la réponse
la moins utile possible ; le motif de refus d'un import reprend la priorité.

### 14.4 Parcours téléphone, exécuté de bout en bout

Serveur réel, jeton, journal, moteur de suivi distinct, mesure raccordée :

```
1. ANALYSER : 200 | fiche produite | prévision conservée côté serveur
2. SUIVRE   : 200 | suivi démarré  | limite annoncée : « s'arrête si le serveur s'arrête »
3. BILAN    : 200 | mesure lancée en arrière-plan
4. BILAN    : 200 | « Bilan mesuré » — 0 résolue, 1 en attente
```

La dernière ligne est le bon résultat : la rencontre se joue le 18 septembre, le
rapport est daté du 14. **Rien n'est mesurable, et rien n'est affirmé.**

### 14.5 Couverture obtenue avec le compte disponible

**Aucune.** Aucune clé n'est configurée dans cet environnement, et la commande le
dit sans appeler quoi que ce soit :

```console
$ python3 -m foot couverture
Aucune clé dans API_FOOTBALL_KEY. L'adaptateur est écrit et testé sur réponses
enregistrées ; ce que VOTRE compte reçoit ne peut être mesuré qu'avec une clé.
Voir COUTS.md avant tout abonnement — rien n'est engagé ici.
```

Le blocage est donc précis : **il manque une clé, pas du code**. Les trois
décisions de [COUTS.md](COUTS.md) restent ouvertes et rien n'est engagé.

### 14.6 Vérifications

```
pytest -m "not network"      428 réussis, 1 ignoré
python3 tests/run_tests.py --sans-reseau
                             428 réussi(s), 0 échec(s), 1 ignoré(s) sur 429
ruff check .                 propre
mypy .                       propre, 100 fichiers
protocole synchronisé        protocole/protocole-22-rubriques.json == dump_rubrics()
```


---

## 15. Septième revue du commit `a53070b` — le raccordement manquant

### 15.1 Le point central : les xG et les absences n'étaient appelés par personne

`ApiFootballProvider.absences()` et `xg_rows()` existaient ; **rien ne les
appelait**. `Engine._analyse_one` ne passait que par `_collect_sheets`. Ajouter
le fournisseur au registre apportait donc les compositions et rien d'autre : une
clé changeait moins qu'il n'y paraissait.

`_collect_context` remplace `_collect_sheets` : trois familles, trois protocoles
(`LineupSource`, `AbsenceSource`, `XgSource`), **une seule porte**, appelée avant
`build_sport_input` et avant le scellement. Tout ce qui est collecté traverse
ensuite `available_at` comme une donnée collée à la main — une source automatique
ne voit jamais plus loin dans le futur qu'un opérateur.

Les xG portent sur les **rencontres passées** des deux équipes, jamais sur le
match à venir : ses statistiques n'existent pas. Le moteur choisit les rencontres
— il tient l'historique et la coupure — et le fournisseur ne fait que chercher,
ce qui met aussi le quota sous le contrôle de l'appelant
(`RECENT_MATCHES_PER_TEAM = 5` par équipe).

### 15.2 La preuve de bout en bout

Le vrai parcours, sans le moindre CSV, avec le véritable adaptateur sur réponses
simulées :

```
1. RÉPONSE API — points d'accès réellement appelés
    13 × /fixtures            10 × /fixtures/statistics
     1 × /injuries             1 × /fixtures/lineups

2. CONTEXTE RETENU
   10 ligne(s) xG, 1 absence(s) — collectées par API-Football

3. DOSSIER SPORTIF SCELLÉ
   empreinte : 1e81c6c4cb420cdb
   familles de preuves : absence, résultats, stats, xg
     stats::Club A::2025-12-05   [confirmé] relevé 13/09 12:00

4. CONSTATS ET SCÉNARIOS
   [fait · R07] Club A : 13 buts pour 9.90 xG sur 6 match(s) (ratio 1.22)
   [fait · R11] Club A : 1 absence(s), dont 1 à un poste décisif (Gardien Titulaire)
   SCÉNARIO SPORTIF : absences décisives (Club A)
     fondé sur : absence rapportée — fait DOCUMENTÉ et sourcé
     cite      : absence::Club A::Gardien Titulaire

5. RAPPORT
   R07 partielle    opérationnel      R11 traitée       opérationnel
   R10 indisponible développé…        R21 indisponible  développé…
```

R10 et R21 restent indisponibles parce que la réponse de compositions est vide —
la feuille n'est pas publiée. C'est le bon résultat, pas un échec du
raccordement.

`tests/test_regression_review7.py` vérifie chacun de ces cinq maillons, plus
l'exclusion d'une absence publiée après l'analyse, et l'égalité des empreintes
entre terminal et navigateur. **Aucune recommandation n'est forcée** : ce qui est
prouvé, c'est quelles données ont réellement servi.

### 15.3 Deux défauts trouvés en écrivant cette preuve

**Une donnée collectée était créditée à l'opérateur.** La rubrique répondue par
une source automatique s'affichait « fournie par l'opérateur ». Premier essai de
correction : lire la colonne `source` de la ligne — faux, car cette colonne nomme
l'**amont** qu'un opérateur cite en collant (« Understat »), et un collage
serait alors compté comme automatique. La provenance est donc déduite du
**décompte** : ce que la collecte a ajouté, et rien d'autre.

**Une ligne indisponible portait encore une valeur.** `XgRow.evidence`,
`AbsenceRow.evidence` et `LineupRow.evidence` construisaient une `Evidence`
`UNAVAILABLE` avec un texte, ce que le registre refuse — une `ValueError` au
milieu d'une analyse. Même règle que partout ailleurs : un fait indisponible ne
porte aucune valeur.

**Et un troisième, dans le test lui-même :** `_recent_fixtures` étiquetait les
rencontres passées avec la compétition portée par chaque ligne d'historique, qui
peut être un libellé d'affichage que rien ne cartographie. C'est la clé résolue
par le moteur qui vaut — c'est sous elle que l'historique a été chargé.

### 15.4 Saisons : une seule résolution

`_fixture_id()` prenait `fixture.date.year`, `coverage()` l'année de début. Un
match de **février 2026 appartient à 2025-26** : le chercher en saison 2026 ne le
trouvait jamais, et la donnée n'arrivait simplement pas. `season_of(date)` et
`season_year("2025-26")` partagent désormais la même règle, exercée de part et
d'autre du changement d'année.

### 15.5 `foot couverture`, complété

Trois familles sondées **séparément** — statistiques, absences, compositions —
parce qu'un plan peut servir l'une et refuser l'autre. Chaque verdict nomme la
rencontre dont il est tiré, et le tableau écrit que **c'est un sondage, pas un
inventaire**.

Quand le tableau est vide, le vrai motif est conservé : quota épuisé, clé
refusée, saison inaccessible ou vide, compétition non cartographiée, ou clé
absente. Cinq situations, cinq messages — afficher « sans clé » alors qu'une clé
est posée envoie chercher un faux problème.

### 15.6 Les tests hors réseau le sont réellement

`test_no_key_ever_reaches_a_report` construisait le fournisseur **sans doublure**
et appelait `probe()` : il envoyait une clé factice au vrai service.

Un garde-fou refuse maintenant toute sortie réseau hors des tests marqués
`network`, dans `conftest.py` **et** dans le lanceur sans dépendance. Le contrôle
par adresse ne suffisait pas : le mandataire de cette machine écoute sur
`127.0.0.1` et relaie vers l'extérieur, si bien qu'un filtre d'adresse laissait
tout passer. Le verrou est donc posé à la frontière HTTP, avec un second verrou
socket pour ce qui n'emprunterait pas `urllib`. La boucle locale reste ouverte :
plusieurs tests lancent un vrai serveur sur `127.0.0.1`.

### 15.7 Vérifications

```
pytest -m "not network"      444 réussis, 1 ignoré
python3 tests/run_tests.py --sans-reseau
                             444 réussi(s), 0 échec(s), 1 ignoré(s) sur 445
ruff check .                 propre
mypy .                       propre, 101 fichiers
protocole synchronisé        protocole/protocole-22-rubriques.json == dump_rubrics()
```

### 15.8 Ce qu'il reste avant de dire qu'une clé suffit

Le raccordement est fait et prouvé **sur réponses simulées**. Ce qu'une clé
gratuite dira encore — et que personne ne peut affirmer d'ici — c'est quels
champs le plan sert réellement : `foot couverture` est là pour le mesurer champ
par champ, championnat par championnat, avant toute dépense.


---

## 16. Huitième revue du commit `d16e98f` — trois défauts avant la vraie clé

Reproduits avec le **véritable moteur**, le **véritable adaptateur** et une
horloge qui avance à chaque réponse. Aucun réseau : le garde-fou du §15.6
l'interdit, et l'adaptateur reçoit son horloge et son *fetcher*.

### 16.1 Une collecte fraîche s'excluait de sa propre analyse

```
AVANT                                   APRÈS
lancement         12:00:00              lancement         12:00:00
collecte terminée 12:00:16              collecte terminée 12:00:16
reçu : 1 absence, 22 joueurs            reçu : 1 absence, 22 joueurs
absences aux constats : 0               absences aux constats : 2
joueurs au plan       : 0               joueurs au plan       : 22
```

`Engine.run()` fixe `as_of` **avant** les appels réseau ; l'adaptateur date la
réponse à l'heure de réception. Quelques secondes plus tard, la donnée devenait
« postérieure à l'analyse » — l'analyse rejetait ce qu'elle venait elle-même de
chercher.

La correction sépare deux régimes :

* **analyse courante** — la coupure avance jusqu'à l'instant où la collecte s'est
  réellement terminée, et **seulement** jusque-là. Un lancement à midi ne devient
  pas une licence pour voir tout ce qui s'est passé depuis ;
* **rejeu** — la date demandée est conservée strictement. Cette date *est* la
  question posée ; la déplacer en poserait une autre.

**Rien n'est antidaté.** Chaque ligne garde l'heure de publication que sa source
lui a donnée ; c'est la coupure qui bouge, jamais la donnée. Un test le vérifie
séparément.

Les deux surfaces déclarent explicitement le régime demandé : toutes deux
calculent un instant avant d'appeler, donc aucune ne peut être devinée à
l'absence d'`as_of`. Sans `--date`, le terminal analyse *maintenant* ; avec, il
rejoue. Le champ date vide du navigateur dit la même chose.

Testé aussi au **premier lancement sans cache** et **après expiration du cache**,
avec l'horloge qui avance entre les deux.

### 16.2 La rencontre était reconnue, ses données perdaient leur équipe

```
AVANT                                   APRÈS
_fixture_id      : trouvé               _fixture_id      : trouvé
2 stats reçues   → 0 ligne xG           2 stats reçues   → 1 ligne xG
absence pour     : 'Arsenal'            absence pour     : 'Arsenal FC'
22 joueurs pour  : Arsenal, Chelsea     11 + 11 pour     : Arsenal FC, Chelsea FC
```

`_fixture_id()` repliait déjà les noms pour **trouver** le match ; tout ce qui
était lu ensuite arrivait étiqueté du nom du fournisseur, qui ne correspond à
rien en aval. Le match était trouvé et ses données perdues.

`TeamAlignment` construit la correspondance **une fois par rencontre** et
l'applique aux trois familles. Elle conserve l'orthographe du fournisseur pour la
traçabilité (`API-Football (reçu sous « Arsenal »)`), **refuse les ambiguïtés** —
deux noms qui se replient sur le même camp n'en rattachent aucun — et écarte une
ligne qui n'appartient à aucun des deux camps plutôt que de la rattacher à une
supposition.

### 16.3 « Contexte retenu » comptait des données écartées

```
AVANT   Contexte retenu : 1 ligne xG, 1 absence, 22 compositions
        (alors que le dossier avait écarté l'absence et les compositions)

APRÈS   Contexte retenu : 1 ligne(s) xG — collectées par API-Football ;
        23 ligne(s) reçue(s) mais écartée(s) (postérieure(s) à l'analyse, ou
        étrangère(s) à cette rencontre)
```

Le décompte se faisait **avant** la coupure. Il se fait désormais **après**, et
seulement sur les lignes qui concernent cette rencontre : reçu, retenu et écarté
sont trois nombres distincts. Un paragraphe de contexte se lit comme la liste de
ce sur quoi repose la recommandation — il ne peut pas contredire l'analyse qu'il
introduit. Terminal et navigateur rendent le même texte, ce qu'un test compare
caractère pour caractère.

### 16.4 Un défaut trouvé en corrigeant

Le test qui prétendait exercer « feuille absente à T−75, publiée plus tard » ne
l'exerçait pas : la doublure devinait l'heure au **nombre d'appels reçus**, donc
publiait dès le premier. Elle lit maintenant la même horloge que la boucle, et le
scénario se déroule vraiment :

```
19:30 → 0 joueur   19:45 → 0   19:50 → 0   19:55 → 22   arrêt : T−60 effectué
```

Une doublure qui devine le temps au nombre d'appels teste sa propre comptabilité,
pas le code.

### 16.5 Vérifications

```
pytest -m "not network"      455 réussis, 1 ignoré
python3 tests/run_tests.py --sans-reseau
                             455 réussi(s), 0 échec(s), 1 ignoré(s) sur 456
ruff check .                 propre
mypy .                       propre, 102 fichiers
```

Séparation sport/cotes et protections chronologiques conservées : la coupure
n'avance que pour une analyse courante, bornée par sa propre collecte, et le
dossier reste scellé avant toute lecture de cote.

---

## 17. Installation téléphone : hébergement, contrôles, secrets (neuvième revue)

Quatre demandes, plus une correction trouvée en les traitant. Rien n'est déclaré
ici qui ne soit exécuté par un test ou reproduit sur un serveur réel.

### 17.1 Le parcours par défaut du navigateur analysait le passé

`do_GET` préremplissait le champ date avec l'heure du chargement, et `do_POST`
n'activait le mode direct que si ce champ était **vide**. Ouvrir la page, taper
deux équipes et appuyer sur **Analyser** — le geste ordinaire — rejouait donc la
minute où la page avait été ouverte, et excluait tout ce qui avait été publié
depuis.

```
AVANT   <input type="datetime-local" id="date" value="2026-09-14T09:12">   live=False
APRÈS   <input type="datetime-local" id="date" value="">                   live=True
```

Le rejeu reste possible et reste un choix : il se tape. Le champ porte
maintenant la phrase qui le dit, et la libellé est passée de « Date de
l'analyse » à « Date et heure de l'analyse ».

### 17.2 Le bookmaker saisi n'atteignait pas le fournisseur

`MarketSource.market_prices` ne prenait que la rencontre ; le bookmaker n'était
connu que de l'instance construite au démarrage. Un serveur construit son moteur
**une fois** et sert plusieurs demandes : le bookmaker tapé dans le formulaire
n'arrivait nulle part, et le meilleur prix du marché sortait à sa place — un
prix que l'opérateur ne peut pas prendre.

```
AVANT   formulaire « Pinnacle »  →  prix retenu 1.90 chez Betclic
APRÈS   formulaire « Pinnacle »  →  prix retenu 1.75 chez Pinnacle
        (sans bookmaker saisi     →  1.90 chez Betclic, le meilleur ; inchangé)
```

`prefer` traverse le protocole, `best_prices` en fait l'arbitre, et un livre qui
ne cote pas la compétition est **nommé** avec ceux chez qui les prix ont été pris.

### 17.3 Contrôle des connexions — quatrième bouton, et commande

`foot controle` et le bouton **Contrôles** appellent les services, dans l'ordre
des causes :

1. **les clés d'abord**, sans rencontre et sans crédit — `/status` chez
   API-Football (formule et requêtes du jour), l'en-tête `x-requests-remaining`
   chez The Odds API, dont la liste `/sports` ne coûte rien ;
2. **la rencontre** : le parcours Analyser est exécuté pour de vrai, en direct ;
3. **chaque famille** sur cette rencontre : xG, absences, compositions, cotes ;
4. **le bookmaker** : les prix revenus sont redépartagés avec celui du
   formulaire, et la ligne dit combien de marchés il a réellement emportés ;
5. **Suivre et Bilan** : mécanisme prévu, prévision journalisable.

Cinq verdicts distincts, jamais confondus : `●` obtenu, `◐` répondu sans la
donnée, `✗` refusé (clé ou quota), `○` injoignable, `·` clé absente. Un `◐` sur
les compositions trois jours avant le match est la réponse correcte, pas une
panne — et un écran qui appellerait cela « prêt » dirait à l'opérateur ce qu'il
veut entendre : `ready` exige `●` partout.

Les quotas affichés sont **mesurés** : ils viennent du service, jamais d'une
page tarifaire. Un compteur illisible est rapporté illisible, jamais remplacé
par zéro.

### 17.4 Aucune clé, nulle part — deux fuites réelles, fermées

Le protocole exige qu'aucune clé n'apparaisse dans le dépôt, les conversations,
les erreurs affichées ou les journaux. Deux chemins la laissaient passer, tous
deux hors de portée d'un simple nettoyage à l'affichage :

| Fuite | Où | Fermeture |
|---|---|---|
| `urllib` met l'URL dans l'exception qu'il lève, et l'URL porte `apiKey=…` | tout message d'erreur d'un fournisseur qui s'authentifie par requête | `redact_url()` au moment où le message est construit, pour **tous** les adaptateurs |
| Le cache écrivait l'URL complète dans son fichier JSON | le disque du serveur, donc toute sauvegarde de ce disque | la clé de lecture reste l'URL entière (un condensé SHA-256) ; l'URL **lisible** est écrite masquée |

```
AVANT   .foot-cache/8f2…json → "url": "https://…/odds?apiKey=a1b2c3d4e5f6…&regions=eu"
APRÈS   .foot-cache/8f2…json → "url": "https://…/odds?apiKey=[masquée]&regions=eu"
```

`scrub()` reste en second rideau sur l'écran de contrôle. Un test parcourt tout
le dépôt et échoue si une valeur en forme de clé y apparaît.

### 17.5 Les suivis reprennent après un redémarrage

C'était la limite nommée au §13.4. Un hébergeur redémarre le service à chaque
déploiement : un suivi perdu, c'est le contrôle de T−75 qui n'a pas lieu.

Chaque suivi est écrit **avant** que son fil ne démarre, dans un fichier en ajout
seul (`WatchStore`). Au démarrage suivant :

| Situation | Ce qui se passe | Ce que l'écran dit |
|---|---|---|
| coup d'envoi encore devant | le suivi **repart**, avec ses contrôles restants | « repris après redémarrage » |
| coup d'envoi passé pendant l'arrêt | rien n'est relancé | **« MANQUÉ »** : le contrôle n'a pas eu lieu |
| aucun fichier configuré | rien n'est repris | la limite, et l'option qui la lève |

Vérifié sur un serveur réel, pas seulement en test : lancé, arrêté, relancé sur
le même fichier.

```
Suivis conservés dans /var/foot/suivis.jsonl : 1 repris, 0 manqué(s) pendant l'arrêt.

1 suivi(s) côté serveur :
Napoli - Bologna 20/09/2026 20:45 — 49e29015 · repris après redémarrage
```

### 17.6 Un défaut trouvé en rédigeant le guide

En vérifiant quelles rubriques rester incomplètes avec une clé qui fonctionne,
la grille affichait pour R08, R09, R12 et R14 :

```
AVANT   ✗ · R12 Entraîneur… (non développé : aucun adaptateur écrit pour : compositions)
```

C'est faux, et faux d'une manière coûteuse : les compositions **arrivent** — R10
et R21 sont renseignées par elles dans la même analyse. L'opérateur qui vient de
payer son abonnement lit que son adaptateur n'existe pas, et cherche un problème
de clé qu'il n'a pas. Ces quatre rubriques ne déclarent aucun adaptateur et aucun
import : ce qui manque est le **traitement**, et la rubrique dit déjà ce qu'il
faudrait.

```
APRÈS   ✗ · R12 Entraîneur… (non développé : aucun traitement n'est écrit pour
        cette rubrique — nécessite la composition et le dispositif annoncés)
```

La phrase s'arrête là volontairement : « aucune source ne sert cela » serait une
affirmation de trop, puisque la feuille de match, elle, arrive.

### 17.7 Hébergement : ce qui est fourni, ce qui est payant

`render.yaml` décrit le service ; il n'engage rien tant que Render n'a pas été
confirmé à l'écran.

| Exigence | Comment | Vérifié par |
|---|---|---|
| HTTPS | fourni par Render sur `*.onrender.com` ; `--https-en-amont` évite un avertissement faux | serveur réel |
| Accès privé | `FOOT_JETON`, tiré au hasard par Render, jamais dans le dépôt | test : 403 sans jeton, 200 avec |
| Clés saisies chez l'hébergeur | `sync: false` — Render demande la valeur et la garde chiffrée | test sur le fichier |
| Journal, cache et suivis persistants | disque de 1 Go monté sur `/var/foot` | test : les trois chemins sont sous le point de montage |
| Sauvegarde récupérable | trois fichiers texte, copiés depuis l'onglet Shell | [INSTALLER.md](INSTALLER.md) |
| Coût annoncé avant validation | **≈ 7,25 $/mois** détaillé ligne par ligne | [COUTS.md](COUTS.md) § 2 bis |

Et une protection ajoutée : servir sur une adresse publique **sans** jeton est
refusé au démarrage, avec la commande qui corrige. Le jeton n'est imprimé en
clair que sur `127.0.0.1` — sur un hébergeur, la console devient le journal du
fournisseur, et le jeton y resterait.

**Réserve sur le montant.** L'environnement où ce code a été écrit n'atteint pas
la page tarifaire de Render : les 7,00 $ de l'instance et les 0,25 $ du disque
sont annoncés de mémoire, et c'est **l'écran de Render au moment de valider** qui
fait foi. C'est écrit dans COUTS.md, pas seulement ici.

### 17.8 Rubriques encore incomplètes, après ces deux abonnements

| Rubrique | État affiché | Raison |
|---|---|---|
| R08, R09, R14 | `non développé` | demandent les événements horodatés d'un match ; API-Football Pro sert des totaux |
| R12 | `non développé` | la feuille arrive ; le dispositif et l'entraîneur, non, et aucun traitement ne les lit |
| R15 | `développé mais inaccessible` | adaptateur écrit ; football-data.co.uk injoignable **depuis cet environnement** — à retester depuis Render |
| R07 | `partielle` | les xG arrivent ; npxG, tirs, grosses occasions et qualité des tirs ne sont pas tous servis — `Contrôles` mesure lesquels, compte par compte |

17 rubriques sur 22 renseignées avec les deux clés, mesuré sur une analyse
complète et non déduit d'un tableau.

### 17.9 Vérifications

```
python3 tests/run_tests.py --sans-reseau
                             492 réussi(s), 0 échec(s), 1 ignoré(s) sur 493
ruff check .                 propre
mypy .                       propre
```

Serveur réel, démarré avec la commande exacte du blueprint : refus sans jeton
(403), page servie avec jeton (200), les quatre boutons, suivi écrit, serveur
arrêté et relancé, suivi repris.

Le protocole est conservé : dossier sportif scellé avant toute lecture de cote,
coupure de disponibilité inchangée, note sur 100 jamais convertie en probabilité,
aucun pari placé, aucun capital supposé. Aucune fusion vers `main` n'est faite
dans ce lot.
