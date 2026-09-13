# Guide d'utilisation — analyse de rencontres

Python 3.10 ou plus récent. **Aucune dépendance à installer.**

```console
$ git clone -b claude/code-masterpiece-o2mbbl <dépôt> && cd FOOT
$ python3 -m foot fournisseurs      # que peut-on vraiment atteindre ?
```

---

## 1. La procédure, en trois gestes

### Saisir les rencontres

Une par ligne. Tous ces formats fonctionnent :

```
Arsenal - Chelsea
Premier League: Man Utd vs Liverpool 20/09/2026 17:30
it.1 | Inter - Milan | 20/09/2026 20:45 | 1.95 3.50 4.20
OM – PSG @ 2.60 3.40 2.65
```

Les trois nombres sont les cotes **1 / N / 2**. Elles sont facultatives : sans
elles, le système fournit l'angle sportif et la **cote à partir de laquelle** le
pari deviendrait intéressant, sans prétendre qu'il y a de la valeur.

### Lancer l'analyse

```console
$ python3 -m foot analyser --fichier mes_matchs.txt \
      --date 2026-09-13T12:00 --fuseau Europe/Paris --bookmaker Pinnacle
```

ou en interface web, en français :

```console
$ python3 -m foot web
Interface disponible sur http://127.0.0.1:8000  (Ctrl+C pour arrêter)
```

### Lire les choix

Le récapitulatif donne une ligne par rencontre demandée — **toutes**, y compris
celles qui n'ont pas pu être identifiées. Puis une fiche par rencontre :
lecture sportive, effectif et contexte, pari principal, pourquoi ce marché plutôt
qu'un autre, risque et contre-analyse, compositions, traçabilité, scellement.

---

## 2. Exemple réel, exécuté

```console
$ python3 -m foot analyser \
    "it.1 | Napoli - Bologna | 13/09/2026 20:45 | 1.62 4.00 5.50" \
    "Premier League: Man Utd - Man City 14/09/2026 17:30 @ 2.55 3.55 2.65" \
    "Dijon - Sochaux" \
    --date 2026-09-13T12:00 --bookmaker Demo --budget 100 --combine 2
```

```
#    Rencontre                       Choix principal          Cote   Prob  EV       Conf  Décision
────────────────────────────────────────────────────────────────────────────────────────────────
1    SSC Napoli – Bologna FC 1909    Victoire extérieur (2)   5.50   23%   +27.4%   B     recommandé
2    Manchester Unit – Manchester…   Victoire extérieur (2)   2.65   48%   +26.2%   B     recommandé
3    Dijon – Sochaux                 —                        —      —     —        —     équipe inconnue
────────────────────────────────────────────────────────────────────────────────────────────────
Total : 3 demandée(s) · 2 analysée(s) · 2 recommandation(s) · 1 à préciser · 0 hors prématch
Contrôle : chaque ligne saisie apparaît bien ci-dessus.
```

Données : openfootball, Serie A 2026/27 et Premier League 2026/27, 780 et 790
matchs réels, relevés le 2026-09-13. Les cotes ci-dessus sont **fournies à la
main** pour la démonstration : aucun fournisseur de cotes n'est joignable depuis
cet environnement (voir `AUDIT.md` §2).

Extrait de la fiche :

```
3 · DÉCISION
  PARI PRINCIPAL     Victoire extérieur (2)
  cote               5.50
  probabilité        23.2%
  cote équitable     4.32
  espérance          +27.42% par unité misée
  CONFIANCE          B — dossier solide, marge réelle mais sensible à une hypothèse
                     (fondement du dossier, pas probabilité de gain)

4 · POURQUOI CE MARCHÉ
  Critères criteres-v1 (déclarés le …, avant évaluation) : EV ≥ +3.0%,
  EV pire scénario ≥ -1.0%, classement par croissance logarithmique à 2% de mise.
  marchés écartés :
    ✗ Match nul (N) — EV -1.27% sous le seuil
    ✗ Victoire domicile (1) — EV -15.52% sous le seuil
```

---

## 3. Les commandes

| Commande | Rôle |
|---|---|
| `foot analyser` | le parcours principal : rencontres → décisions |
| `foot web` | la même chose dans le navigateur, en français |
| `foot fournisseurs` | sonde chaque source et affiche sa **couverture réelle** |
| `foot rubriques` | la grille des 22 rubriques et ce que chacune exige |
| `foot valider` | validation chronologique sur données réelles |

Options utiles de `analyser` :

| Option | Effet |
|---|---|
| `--date`, `--fuseau` | l'instant d'analyse (`as_of`) ; Europe/Paris par défaut |
| `--budget N` | chiffre les mises. **Sans budget, aucune mise n'est proposée** |
| `--kelly 0.25` | fraction de Kelly (quart-Kelly par défaut) |
| `--combine N` | propose un combiné d'**au plus** N sélections |
| `--rubriques` | affiche la grille des 22 rubriques par rencontre |
| `--resultats-csv`, `--cotes-csv` | import manuel, solution de secours documentée |

---

## 4. Ce que le système garantit

**Aucune rencontre n'est perdue.** Une ligne saisie = une ligne au récapitulatif.
Si elle n'est pas identifiable, le rapport dit précisément ce qui manque :

```
L6: united – Chelsea    -> ambiguë — manque : nom d'équipe trop court pour être
    unique : précisez le nom complet parmi les candidats ci-dessous
    — candidats : Leeds United FC, Manchester United FC, Newcastle United FC
```

**Un match commencé n'est jamais analysé comme un prématch.** Il est signalé à
part et ne reçoit aucune recommandation.

**Le dossier sportif est formé sans voir les cotes, puis scellé.** L'entrée de la
phase sportive est une liste blanche à quatre champs, qui écarte les preuves de
marché ; le dossier est ensuite empreinté (SHA-256). L'empreinte est une trace,
pas la barrière — les deux sont distinctes dans le code et dans le rapport. Une
variation de cote ne réécrit jamais un dossier scellé ; une information sportive
décisive produit un **nouveau** dossier qui remplace l'ancien, avec sa raison.

**Une donnée absente n'est jamais remplacée.** Les rubriques sans source sont
marquées indisponibles avec le motif. Les données synthétiques portent un
marqueur indélébile et sont **refusées en mode réel**.

**Les règlements sont exacts.** Remboursements, demi-gains et demi-pertes sont
calculés sur la loi jointe des scores. `p × cote − 1` n'est utilisé que pour un
pari binaire sans remboursement : avec 25 % de remboursement, cette formule donne
0,035 là où l'espérance vraie est 0,285.

**Les critères de classement sont fixés avant l'évaluation** et repris dans la
fiche. Ils ne sont jamais réécrits après coup.

---

## 5. Ce que le système ne fait pas

- **Il ne place aucun pari.** Il analyse et recommande.
- **Il ne suppose aucun capital.** Sans `--budget`, aucune mise n'est chiffrée.
- **Une confiance A n'augmente pas la mise** : elle qualifie le dossier, pas
  l'avantage de prix.
- **Il ne revendique aucune rentabilité démontrée.** Sans cotes disponibles au
  moment de la décision, seule la qualité probabiliste est mesurée.
- **Il ne cote ni cartons, ni corners, ni buteurs** : ces marchés ne se déduisent
  pas de la loi des scores finaux, et il refuse de les approximer.
- **Aucun contrôle automatique de compositions n'est actif.** Le rapport écrit
  « AUCUN automatisme actif — le contrôle reste à effectuer manuellement ».

---

## 6. Validation chronologique

```console
$ python3 -m foot valider --competition en.1 --plis 4
```

Résultat mesuré sur 790 matchs réels de Premier League (2024-25 à 2026-27),
501 rencontres évaluées hors échantillon :

```
  dixon-coles fixe     n=501  RPS=0.21026  logloss=1.03227  acc=46.9%  skill +8.26%
  poisson              n=501  RPS=0.21038  logloss=1.03386  acc=46.9%  skill +8.21%
  dixon-coles réglé    n=501  RPS=0.21058  logloss=1.03202  acc=47.3%  skill +8.13%
  taux de base         n=501  RPS=0.22920  logloss=1.08499  acc=42.3%
  IC 95% (bootstrap par blocs) : [0.20176, 0.22082]

  apport de chaque composant (retiré un par un) :
    correction bas scores (Dixon-Coles)   gain -0.00020  → APPORT NON CONFIRMÉ
    réglage chronologique des paramètres  gain -0.00031  → APPORT NON CONFIRMÉ
    modèle d'équipes (vs taux de base)    gain +0.01863  → apport confirmé
```

**Lecture honnête : seul le modèle d'équipes confirme son apport.** Sur cet
échantillon, ni la correction Dixon-Coles ni le réglage chronologique ne se
confirment — ils restent donc expérimentaux, et le système le dit plutôt que de
les présenter comme des acquis. L'intervalle est calculé par bootstrap **par
blocs**, qui préserve la dépendance sérielle des résultats.

---

## 7. Vérifier

```console
$ pytest -q                      # 204 tests
$ python3 tests/run_tests.py     # les mêmes, sans rien installer
$ ruff check . && mypy foot      # propres sur 58 fichiers
```
