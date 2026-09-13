# Démarrer — 5 minutes

Ce guide sert à **ouvrir l'application et analyser un match**. Rien d'autre.
Le détail complet est dans [GUIDE.md](GUIDE.md), le protocole dans
[protocole/](protocole/), l'état réel des fonctions dans [AUDIT.md](AUDIT.md).

---

## 1. Ouvrir l'application

Il n'y a **rien à installer** : ni bibliothèque, ni compte, ni clé. Il faut
Python 3.10 ou plus récent, déjà présent sur macOS et Linux.

```console
$ cd FOOT
$ python3 -m foot web
Interface disponible sur http://127.0.0.1:8000  (Ctrl+C pour arrêter)
```

Ouvrez **http://127.0.0.1:8000** dans votre navigateur.

> Cette adresse est locale : elle fonctionne sur l'ordinateur qui a lancé la
> commande. Aucun service en ligne n'est hébergé, et aucune autre URL n'est
> promise. Pour y accéder **depuis votre téléphone sur le même Wi-Fi**, lancez
> plutôt `python3 -m foot web --hote 0.0.0.0`, puis ouvrez
> `http://ADRESSE-DE-L-ORDINATEUR:8000` (l'adresse locale de la machine,
> du type `192.168.1.x`). N'exposez pas ce port sur Internet : l'interface n'a
> ni authentification ni chiffrement.

Pour arrêter : `Ctrl+C` dans le terminal.

---

## 2. Saisir un match

Dans le grand champ **« Rencontres à analyser »**, une ligne par match :

```
it.1 | Napoli - Bologna | 13/09/2026 20:45 | 1=1.62 N=4.00 2=5.50
```

Rien n'est obligatoire sauf les deux équipes. Ces trois écritures marchent :

| Vous tapez | Ce qui est compris |
|---|---|
| `Arsenal - Chelsea` | les deux équipes, date cherchée au calendrier |
| `Premier League: Man Utd vs Liverpool 20/09/2026 17:30` | + compétition et horaire |
| `it.1 \| Inter - Milan \| 20/09/2026 20:45 \| 1.95 3.50 4.20` | + les cotes 1 / N / 2 |

**Toutes** les lignes saisies apparaissent dans le résultat, même celles qui
n'ont pas pu être analysées — avec la raison exacte.

Puis réglez la **date d'analyse** (`as_of`) et le **fuseau** (Europe/Paris par
défaut), et appuyez sur **Analyser**.

---

## 3. Ajouter les prix des autres marchés

Un marché sans prix ne peut pas être comparé aux autres. Pour le mettre en
concurrence, donnez sa cote **dans la même ligne** :

```
it.1 | Napoli - Bologna | 13/09/2026 20:45 | 1=1.62 N=4.00 2=5.50 BTTS:oui=1.85 TOTAL:+2.5=1.90
```

| Notation | Marché |
|---|---|
| `1=` `N=` `2=` | 1–N–2 |
| `DC:1N=` `DC:12=` `DC:N2=` | double chance |
| `DNB:1=` `DNB:2=` | remboursé si nul |
| `TOTAL:+2.5=` `TOTAL:-2.5=` | plus / moins de 2,5 buts |
| `AH:H:-0.5=` `AH:A:+1=` | handicap asiatique |
| `TE:H:+1.5=` | total d'une équipe |
| `BTTS:oui=` `BTTS:non=` | les deux équipes marquent |

Les prix peuvent être groupés ou séparés par des `|`, au choix.

---

## 4. Ajouter le contexte (xG, absences, compositions)

Aucune source accessible ici ne publie ces données. Si vous les avez,
dépliez **« Contexte à fournir »** sous le formulaire et collez-les.
Une ligne d'en-tête, puis une ligne par fait :

```
date,equipe,joueur,poste,motif,source,statut
11/09/2026,SSC Napoli,Alex Meret,gardien,blessure,Gazzetta,officiel
```

- Ce que vous ne fournissez pas reste marqué **« non renseigné »** — jamais
  remplacé par une valeur inventée.
- Une ligne illisible s'affiche pour que vous la corrigiez ; elle n'est pas
  devinée.
- Sans heure de publication, une information datée d'aujourd'hui n'est
  considérée connue **que demain**. Pour qu'une composition ou un retour de
  blessure du jour compte, ajoutez la colonne `publication` avec l'heure :
  `13/09/2026 19:30`. À défaut, la fiche vous dit quelles lignes elle a dû
  écarter — elle ne les ignore jamais en silence.
- Vous pouvez empiler les versions : les onze probables, puis les onze
  officiels. La dernière connue à l'heure d'analyse fait foi, la précédente
  reste tracée, et l'ordre des lignes n'a aucune importance.

---

## 5. Lire la réponse

En haut, **une ligne par match** : le pari principal, la cote, la probabilité
du modèle, l'espérance, la confiance et la décision.

Puis **une fiche par match**, dans cet ordre :

1. **Lecture sportive** — buts attendus, 1X2 du modèle, score central.
2. **Effectif et contexte** — et la liste de ce qui n'a pas pu être renseigné,
   avec pour chaque manque l'action qui le lèverait.
3. **Décision** — le pari, son règlement exact, sa cote équitable, sa confiance.
4. **Marchés cotés comparés** — combien ont un prix, lesquels, et pourquoi
   celui-ci plutôt qu'un autre.
5. **Risque et contre-analyse** — ce qui rendrait la lecture fausse.
6. **Compositions** — l'heure du contrôle T−75 / T−60.
7. **De la source à la prévision** — quelle donnée a servi à quel chiffre.

Deux choses à ne pas confondre :

- la **confiance A/B/C/D** décrit la qualité du dossier, **pas** une probabilité
  de gagner ;
- le logiciel **analyse et recommande ; il ne place aucun pari**, et ne suppose
  aucun capital. Sans budget saisi, aucune mise n'est chiffrée.

---

## 6. Refaire le contrôle des compositions

Le contrôle T−75 / T−60 **n'est pas automatique** : aucun automatisme ne relève
les compositions ici, et la fiche l'écrit. Pour le faire :

1. environ une heure avant le coup d'envoi, relevez les compositions
   officielles auprès du club ou de la ligue ;
2. rouvrez l'application, recollez la même saisie, **mettez la date d'analyse à
   l'heure courante** ;
3. ajoutez les compositions dans « Contexte à fournir », avec la colonne
   `publication` et l'heure réelle ;
4. relancez : la fiche indique alors le verdict — **maintenu**, **dégradé**,
   **annulé** ou **amélioré** — et pourquoi.

---

## 7. En ligne de commande

Tout ce qui précède existe à l'identique en terminal, et donne les **mêmes
décisions** :

```console
$ python3 -m foot analyser \
    "it.1 | Napoli - Bologna | 13/09/2026 20:45 | 1=1.62 N=4.00 2=5.50" \
    --date 2026-09-13T12:00 --bookmaker Demo --budget 100 \
    --xg-csv xg.csv --absences-csv absences.csv --compositions-csv compos.csv
```

Autres commandes utiles :

```console
$ python3 -m foot fournisseurs     # que peut-on vraiment atteindre d'ici ?
$ python3 -m foot valider --competition en.1 --plis 4   # performance mesurée
$ python3 tests/run_tests.py       # la suite complète, sans rien installer
```

---

## Si quelque chose ne marche pas

| Message | Ce qu'il faut faire |
|---|---|
| `équipe inconnue` | vérifiez l'orthographe, ou précisez la compétition (`it.1 \| …`) |
| `non vérifiée` | la rencontre n'est pas au calendrier chargé : corrigez la date, ou fournissez un calendrier avec `--calendrier-csv` |
| `hors prématch` | le coup d'envoi est passé — l'analyse d'avant-match ne s'applique plus |
| `aucune cote fournie` | l'angle sportif est donné, avec la cote à partir de laquelle le pari deviendrait intéressant |
| `n ligne(s) importées, aucune exploitable` | les noms d'équipes ou les dates du fichier ne correspondent pas à l'historique chargé |
| `ancienneté inconnue` | la cote importée n'a pas d'heure de relevé : confirmez-la avant de jouer ce prix |
| `postérieure à l'heure d'analyse` | l'information a été publiée après l'heure que vous avez saisie — elle n'existait pas encore ; avancez la date d'analyse si elle est connue maintenant |
