# Guide d'utilisation — analyse de rencontres

> **Vous débutez ?** Lisez plutôt [DEMARRAGE.md](DEMARRAGE.md) : cinq minutes,
> ouvrir l'application et analyser un match. Ce guide-ci est la référence
> complète.

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

**L'âge d'un prix compte.** Une cote que vous tapez est datée de l'heure
d'analyse — c'est le moment où vous l'avez lue. Une cote **importée** garde
l'heure déclarée par sa source :

| Situation | Ce qui se passe |
|---|---|
| relevée il y a moins de 12 h | comparée normalement |
| relevée il y a plus de 12 h | écartée : « cote périmée » |
| **heure de relevé inconnue** | écartée : « ancienneté inconnue » — confirmez l'heure avant de jouer ce prix |
| **relevée après l'heure d'analyse** | exclue de cette analyse : elle n'existait pas encore |

Dans tous ces cas, les autres marchés correctement cotés restent comparés, et la
fiche nomme chaque exclusion avec son motif.

### Fournir le contexte qu'aucune source active ne sert

Ce que vos fournisseurs servent dépend de vos clés, et `foot fournisseurs
--couverture` le mesure au lieu de le supposer. Sans clé, aucune source ne sert
xG, absences ni compositions : fournissez-les, et les rubriques correspondantes
passent de « non renseignée » à « traitée ». Une donnée collée reste marquée
« fournie par l'opérateur » de bout en bout, et une donnée collectée
automatiquement traverse exactement le même filtre de disponibilité :

```console
$ python3 -m foot analyser --fichier matchs.txt \
      --xg-csv xg.csv --absences-csv absences.csv --compositions-csv compos.csv
```

| Fichier | Colonnes |
|---|---|
| `--xg-csv` | `date,home,away,home_xg,away_xg[,source,statut,publication]` |
| `--absences-csv` | `date,equipe,joueur[,poste,motif,source,statut,remplacant,jusqu_au,retour]` |
| `--compositions-csv` | `date,equipe,joueur[,poste,titulaire,source,statut,publication,adversaire]` |

**Trois dates, jamais confondues.** `date` porte le fait — jour du match pour
une ligne xG, jour de l'annonce pour une absence, jour de la rencontre pour une
feuille. `publication` porte l'instant où l'information est devenue publique :
**avec une heure elle fait foi**, sans heure l'antériorité n'est pas démontrable
dans la journée et la ligne n'est réputée connue que le lendemain à 00:00. Pour
qu'une composition officielle relevée à T−60 compte le soir même, donnez l'heure :
`14/09/2026 19:30`.

**Une feuille appartient à une rencontre**, pas à une équipe en général : sa
`date` doit être celle du match. Chaque équipe garde son propre statut — une
feuille officielle à domicile ne rend pas officielle la feuille probable de
l'adversaire — et la complétude est dite : moins de onze titulaires reste une
composition **partielle**, et le contrôle n'est pas clos.

**Une absence a une durée.** `jusqu_au` la borne, `retour` l'annule à partir
d'une date confirmée. Sans l'un ni l'autre, elle vaut 21 jours — chiffre déclaré
comme hypothèse, pas mesuré.

**L'état d'un joueur se résout chronologiquement.** Vous pouvez empiler les
déclarations dans l'ordre où elles arrivent — blessure, retour, rechute : la
dernière connue à l'heure d'analyse décide. Une ligne dont le motif est
`retour` (ou qui porte une date en colonne `retour`) rend le joueur disponible ;
une blessure postérieure le retire de nouveau.

**Donnez l'heure de publication pour les nouvelles du jour.** Sans elle, une
déclaration datée d'aujourd'hui n'est réputée connue que demain — y compris un
retour. La fiche signale alors, en toutes lettres, les déclarations qu'elle a dû
écarter et ce qu'il faut faire pour qu'elles comptent ; elle n'affirme jamais en
silence une absence que votre propre fichier a levée.

**Une ligne illisible est rejetée avec son motif**, jamais devinée : valeur non
finie, xG négatif, date illisible, doublon. Le motif remonte jusqu'à l'interface
pour que vous puissiez corriger.

`statut` vaut `officiel` ou `probable` : une composition probable n'est jamais
présentée comme officielle. Ces données **n'entrent pas dans l'estimation** —
aucune calibration d'une vraisemblance pondérée par les xG n'a été validée ici.
Elles servent à construire des **scénarios sportifs documentés**, et chacun cite
sa source.

### Ce que fait chaque type de scénario

Trois rôles, jamais confondus — le code et cette page disent désormais la même
chose, ce qui n'était pas le cas avant la revue :

| Type | Rôle | Pourquoi |
|---|---|---|
| **événement sportif documenté** | **écarte** un pari | c'est un fait sourcé : une absence rapportée, un écart buts/xG mesuré |
| **sensibilité** (±15 %) | **déclasse** sans écarter | la variation est arbitraire ; elle mesure la fragilité, donc elle ordonne le classement et pèse sur la confiance, mais elle ne peut pas décider seule |
| **incertitude d'estimation** (±1 σ) | **informe** seulement | c'est une bande bilatérale autour de l'estimation, pas un événement défavorable ; s'en servir comme plancher rejetterait tout marché sur tout échantillon réaliste, en prétendant que le rejet parle de football |

La fiche nomme, pour le pari retenu, le scénario qui produit sa pire espérance
**et** son type.

Ce que chaque fichier change, mesuré sur un parcours réel (Napoli – Bologna,
Serie A 2026-27) :

| Import | Effet vérifié |
|---|---|
| `--xg-csv` | R07 renseignée **partiellement** : la rubrique réunit buts, xG, npxG, xGA, tirs, grosses occasions et qualité des tirs — la fiche nomme ce qui est couvert et ce qui manque. L'écart buts/xG est estimé par un postérieur Gamma-Poisson : il ne produit un scénario que si son intervalle de crédibilité exclut « aucun écart », donc pas sur un match isolé |
| `--absences-csv` | R11 renseignée ; une absence à un poste suivi crée un scénario sportif documenté. Le barème agit **par poste et sur le bon canal** : un gardien absent fait monter l'attaque adverse, un buteur fait baisser la sienne ; un remplaçant nommé atténue l'effet de moitié. Ces amplitudes sont des **hypothèses déclarées**, non calibrées |
| `--compositions-csv` | R10 et R21 renseignées ; le contrôle T−75/T−60 porte sur la feuille fournie et rend un **verdict de réévaluation motivé**. Une feuille plus récente **remplace** la précédente, qui reste visible : publiez les onze joueurs probables puis les onze officiels dans le même fichier, la dernière version disponible fait foi et l'ordre des lignes est sans effet |

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

ou en interface web, en français, utilisable sur téléphone :

```console
$ python3 -m foot web
Interface disponible sur http://127.0.0.1:8000  (Ctrl+C pour arrêter)
```

Cette adresse est **locale** : elle ne vaut que sur la machine qui a lancé la
commande, et aucun service en ligne n'est hébergé. Pour l'ouvrir depuis un
téléphone du même réseau :

```console
$ python3 -m foot web --hote 0.0.0.0
```

puis `http://ADRESSE-LOCALE-DE-L-ORDINATEUR:8000`. Les deux modes ont été
vérifiés. N'exposez pas ce port sur Internet : l'interface n'a ni
authentification ni chiffrement.

Le navigateur offre **tout ce que la ligne de commande offre** — rencontres,
date, fuseau, bookmaker, budget, combiné, et les trois zones de contexte (xG,
absences, compositions) à coller directement. Les deux surfaces passent par la
même fonction `analyse_form()`, donc par le même `engine.run()` : à entrées
identiques elles produisent la **même empreinte de dossier et la même
décision**, ce qu'un test vérifie (`test_d6b`).

Les lignes de contexte refusées sont **affichées pour correction**, et la page
indique ce qui a réellement été retenu.

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
| `foot web` | la même chose dans le navigateur, en français ; `--jeton` pour un accès privé, `--certificat`/`--cle` pour HTTPS, `--suivis` pour que les suivis survivent à un redémarrage |
| `foot controle` | appelle réellement chaque service configuré sur une rencontre et dit, famille par famille, ce qui revient — avec vos quotas restants. Sans clé, aucune requête n'est faite : la ligne dit « clé absente » |
| `foot suivre` | exécute le contrôle T−75/T−60 et réessaie jusqu'au coup d'envoi |
| `foot journal` | relit les prévisions écrites avant match, jamais réécrites |
| `foot mesurer` | apparie le journal aux résultats et le note — **sans rien y réécrire** |
| `foot config` / `foot fournisseurs --couverture` | ce que chaque clé débloque et coûte, mesuré ([COUTS.md](COUTS.md)) |
| `foot couverture` | ce que votre compte API-Football reçoit **champ par champ** |
| `foot config` | quelles clés sont posées, ce qu'elles débloquent, ce qu'elles coûtent |
| `foot fournisseurs` | sonde chaque source ; `--couverture` ajoute coût et accès réel |
| `foot rubriques` | la grille des 22 rubriques et ce que chacune exige |
| `foot valider` | validation chronologique sur données réelles |

Options utiles de `web`, pour un hébergement :

| Option | Effet |
|---|---|
| `--jeton [VALEUR]` | exige un jeton ; seul, il en tire un au hasard. **Obligatoire dès que l'hôte n'est pas `127.0.0.1`** : servir publiquement sans jeton est refusé au démarrage |
| `--suivis [fichier]` | écrit les suivis ; au démarrage suivant, ceux dont le coup d'envoi est devant repartent, les autres sont marqués « manqué » |
| `--https-en-amont` | le TLS est assuré par un proxy devant le service (cas des hébergeurs gérés) : sans cette option, un avertissement « jeton en clair » s'affiche à tort |
| `--url-publique ADRESSE` | l'adresse à annoncer au démarrage, quand elle n'est pas celle du socket |

Un service prêt à l'emploi (HTTPS, disque persistant, clés saisies chez
l'hébergeur) est décrit dans `render.yaml` ; la marche à suivre, en neuf étapes,
est dans [INSTALLER.md](INSTALLER.md).

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
| `--journal [fichier]` | consigne les prévisions en ajout seul, pour les mesurer plus tard |
| `--motif "…"` | la raison de cette exécution, portée au journal |
| `--provenance` | par rubrique : la donnée, sa source et sa fraîcheur |

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
- **Le contrôle T−75/T−60 n'est actif que si vous le lancez.** `foot suivre`
  l'exécute réellement ; une analyse ponctuelle ne le fait pas, et le rapport
  écrit alors ce qui exécuterait le contrôle plutôt que de le promettre. Sans
  source de compositions joignable, la boucle tournerait sans rien lire, et la
  fiche le dit.

---

## 6. Validation chronologique

```console
$ python3 -m foot valider --competition en.1 --plis 4
```

Résultat mesuré sur 790 matchs réels de Premier League (2024-25 à 2026-27),
**486 rencontres évaluées hors échantillon** en 4 plis **disjoints** :

```
  réglé (régularisation)     RPS=0.21000   skill +8.35%
  configuration de production RPS=0.21024  skill +8.24%   ← celle qui recommande
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
$ pytest -m "not network"        # 295 réussis, 1 ignoré, 8 déselectionnés
$ python3 tests/run_tests.py --sans-reseau   # les mêmes, sans rien installer
$ ruff check . && mypy .         # propres sur 82 fichiers
```

Le lanceur sans dépendance compte **séparément** réussites, échecs et ignorés :

```
295 réussi(s), 0 échec(s), 1 ignoré(s) sur 296 en 32.45s
```

Un test qui ne peut pas s'exécuter lève `unittest.SkipTest` et apparaît comme
ignoré — jamais comme réussi.

Les tests **réseau** sont séparés des tests déterministes, dans les deux
lanceurs :

```console
$ pytest -m network              # 8 réussis — dépend des hôtes atteignables
```

**Intégration continue.** `.github/workflows/verification.yml` exécute sur
chaque poussée et chaque *pull request* :

- un job **bloquant** — le lanceur sans dépendance d'abord (si la suite exigeait
  pytest, la promesse « zéro dépendance » ne serait pas tenue), puis
  `pytest -m "not network" -rs`, `ruff`, `mypy`, et un contrôle que le protocole
  stocké dans `protocole/` correspond bien au code ;
- un job **réseau informatif** (`continue-on-error`) : un hôte injoignable est
  un fait d'environnement, pas un défaut du code, et ne doit pas bloquer une
  fusion.
