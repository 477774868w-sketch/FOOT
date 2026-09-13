# Audit du dépôt et correspondance avec les 22 rubriques

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

| Défaut | Détection | Correction |
|---|---|---|
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
