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

### Coter les autres marchés

Un marché sans prix ne peut pas être comparé. Pour qu'il entre en concurrence,
donnez sa cote, dans la même ligne :

```
it.1 | Napoli - Bologna | 13/09/2026 20:45 | 1=1.62 | N=4.00 | 2=5.50
      | BTTS:oui=1.80 | TOTAL:+2.5=2.05 | DNB:1=1.22 | DC:1N=1.18 | AH:H:-0.5=1.72
```

| Notation | Marché |
|---|---|
| `1=` `N=` `2=` | 1–N–2 |
| `DC:1N=` `DC:12=` `DC:N2=` | double chance |
| `DNB:1=` `DNB:2=` | remboursé si nul |
| `TOTAL:+2.5=` `TOTAL:-2.5=` | plus / moins de 2,5 buts |
| `AH:H:-0.5=` `AH:A:+1=` | handicap asiatique (lignes quart comprises) |
| `TE:H:+1.5=` | total d'équipe |
| `BTTS:oui=` `BTTS:non=` | les deux marquent |

Les prix se placent indifféremment chacun dans son champ `|` ou groupés dans le
même champ, séparés par des espaces — les deux écritures se lisent :

```
it.1 | Napoli - Bologna | 13/09/2026 20:45 | 1=1.62 N=4.00 2=5.50 BTTS:oui=1.85
```

Une ligne absente du catalogue standard (`AH:H:-0.75`, `TOTAL:+0.5`) est
**construite à la demande** plutôt qu'ignorée. La fiche indique ensuite combien
de marchés cotés ont réellement été mis en concurrence, et combien ne l'ont pas
été faute de prix.

Les corners, cartons et buteurs ne se déduisent pas de la loi des scores finaux.
Une cote donnée pour l'un d'eux (`CORNERS:+9.5=1.90`) n'est **ni estimée ni
ignorée** : la fiche la nomme et dit qu'un modèle dédié serait requis.

### Fournir le contexte que personne ne publie ici

Aucune source accessible ne sert les xG, les absences ou les compositions. Vous
pouvez les fournir, et les rubriques correspondantes passent alors de
« non renseignée » à « traitée » :

```console
$ python3 -m foot analyser --fichier matchs.txt \
      --xg-csv xg.csv --absences-csv absences.csv --compositions-csv compos.csv
```

| Fichier | Colonnes |
|---|---|
| `--xg-csv` | `date,home,away,home_xg,away_xg[,source,statut]` |
| `--absences-csv` | `date,equipe,joueur[,poste,motif,source,statut]` |
| `--compositions-csv` | `date,equipe,joueur[,poste,titulaire,source,statut]` |

`statut` vaut `officiel` ou `probable` : une composition probable n'est jamais
présentée comme officielle. Ces données **n'entrent pas dans l'estimation** —
aucune calibration d'une vraisemblance pondérée par les xG n'a été validée ici.
Elles servent à construire des **scénarios sportifs documentés**, les seuls
habilités à faire rejeter un pari, et chacun cite sa source.

Ce que chaque fichier change, mesuré sur un parcours réel (Napoli – Bologna,
Serie A 2026-27) :

| Import | Effet vérifié |
|---|---|
| `--xg-csv` | R07 renseignée : buts marqués contre xG fournis, et un scénario « retour au niveau xG » par équipe, dont l'ampleur est **mesurée** sur l'écart |
| `--absences-csv` | R11 renseignée ; une absence à un poste suivi crée un scénario sportif documenté (ampleur déclarée **hypothèse**, jamais présentée comme mesurée) |
| `--compositions-csv` | R10 et R21 renseignées ; le contrôle T−75/T−60 porte sur la feuille fournie et rend un **verdict de réévaluation motivé** |

Les lignes xG doivent désigner des matchs présents dans l'historique chargé
(mêmes noms d'équipes, mêmes dates). Sinon la fiche le dit explicitement —
« *n* ligne(s) importées, aucune exploitable » — au lieu de redemander un
fichier déjà fourni.

Aucun automatisme ne relève les compositions dans ce déploiement, et la fiche
l'écrit : le contrôle T−75/T−60 est planifié, jamais présenté comme effectué.

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
| `--resultats-csv`, `--cotes-csv` | import manuel de résultats et de cotes |
| `--xg-csv`, `--absences-csv`, `--compositions-csv` | contexte fourni par l'opérateur |
| `--protocole fichier.json` | remplace la grille des 22 rubriques |

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

**Une rencontre non vérifiée au calendrier ne reçoit aucune recommandation.**
Deux clubs qui existent ne font pas une rencontre programmée. Si la date saisie
ne correspond à aucune rencontre, le système le dit et propose les rencontres
proches ; il ne fait jamais glisser silencieusement votre date sur une autre.

**Une information n'est utilisée qu'à partir du moment où elle était
connaissable.** La coupure porte sur la date de **disponibilité** — pour une
source sans horaire de coup d'envoi, un résultat du 12 septembre n'est réputé
connu qu'à partir du 13. Une analyse lancée à minuit ne consomme donc pas les
scores du soir même.

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

**Un modèle non convergé, ou un dossier noté D, n'engendre aucune
recommandation.** Ces deux diagnostics signifient « information insuffisante
pour engager » ; publier un pari à côté serait se contredire.

**Les seuils de prix sont résolus sur le profil de règlement exact.** Sur un
marché remboursable — 40 % de victoire, 35 % de remboursement, 25 % de défaite —
exiger 3 % d'espérance demande une cote de **1,70**, pas 1,67 : multiplier la
cote d'équilibre par 1,03 est faux dès qu'un remboursement est possible.

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
**486 rencontres évaluées hors échantillon** en 4 plis **disjoints** :

```
  réglé (régularisation)     RPS=0.21000   skill +8.35%
  référence                  RPS=0.21040   skill +8.17%
  sans correction bas scores RPS=0.21052   skill +8.12%
  sans régularisation        RPS=0.21075   skill +8.02%
  réglé (demi-vie)           RPS=0.21154   skill +7.67%
  sans décroissance          RPS=0.21180   skill +7.56%
  taux de base               RPS=0.22912
  IC 95% du RPS (bootstrap par blocs) : [0.20094, 0.22104]

  correction bas scores (rho)      gain +0.00012  IC [-0.00023, +0.00041]  NON CONFIRMÉ
  décroissance temporelle          gain +0.00140  IC [-0.00020, +0.00296]  NON CONFIRMÉ
  régularisation (ridge)           gain +0.00035  IC [-0.00009, +0.00082]  NON CONFIRMÉ
  réglage chrono. de la demi-vie   gain -0.00114  IC [-0.00225, -0.00014]  NON CONFIRMÉ
  réglage chrono. du ridge         gain +0.00040  IC [-0.00026, +0.00103]  NON CONFIRMÉ
  modèle d'équipes                 gain +0.01872  IC [+0.00830, +0.02921]  apport confirmé
```

**Lecture honnête : un seul composant confirme son apport, le modèle
d'équipes.** Chaque ablation ne fait varier **qu'une** dimension, et le verdict
repose sur l'intervalle bootstrap de la différence **appariée**, pas sur le
signe du gain. Le réglage chronologique de la demi-vie **dégrade** le score, avec
un intervalle entièrement négatif : c'est un résultat défavorable au système,
publié tel quel.

Ces chiffres diffèrent de ceux publiés au commit `8813955` (501 évaluations) :
les fenêtres de test s'y chevauchaient aux dates frontières.

---

## 7. Vérifier

```console
$ pytest                         # 241 réussis, 1 ignoré
$ python3 tests/run_tests.py     # les mêmes, sans rien installer
$ ruff check . && mypy .         # propres sur 79 fichiers
```

Le lanceur sans dépendance compte **séparément** réussites, échecs et ignorés :

```
241 réussi(s), 0 échec(s), 1 ignoré(s) sur 242 en 52.35s
```

Un test qui ne peut pas s'exécuter lève `unittest.SkipTest` et apparaît comme
ignoré — jamais comme réussi.
