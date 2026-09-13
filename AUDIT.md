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
| 21 | Compositions probables puis officielles | 🟡 | `analysis/lineups.py` : planification T−75/T−60, révision du dossier | `::test_lineup_plan_is_outstanding_until_something_records_a_check` — **aucun automatisme actif**, et le rapport l'écrit |
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
| · | non développé | R08, R09, R12, R14 — aucun adaptateur n'existe pour les événements détaillés |

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
