# Depuis le téléphone — une page

Objectif : saisir des matchs, appuyer sur **Analyser**, lire une fiche par
rencontre. Rien à préparer, aucun CSV.

---

## 1. Démarrer le serveur (sur l'ordinateur, une fois)

```console
$ cd FOOT
$ python3 -m foot web --hote 0.0.0.0 --jeton --journal
Interface disponible sur http://0.0.0.0:8000/?jeton=hY3k…
Analyses conservées dans .foot-journal.jsonl (ajout seul).
```

`--jeton` seul tire une adresse privée au hasard. Recopiez **l'adresse
complète, jeton compris**, en remplaçant `0.0.0.0` par l'adresse locale de
l'ordinateur (du type `192.168.1.x`). Une fois ouverte, le téléphone retient le
jeton ; il n'est plus jamais affiché dans la page.

`--journal` conserve chaque analyse **côté serveur** : le téléphone ne détient
rien, et fermer l'onglet ne perd rien.

> Sur un réseau local, HTTP suffit. Dès que le service sort du Wi-Fi domestique,
> voir *Hébergement en ligne* plus bas — un jeton transmis en clair est un jeton
> donné, et la commande vous le dit.

---

## 2. L'écran

Quatre choses, dans l'ordre :

1. **Rencontres à analyser** — une par ligne ;
2. **Date et heure de l'analyse** — l'instant depuis lequel on juge ;
3. **Bookmaker** — celui chez qui vous jouez réellement ;
4. **Analyser**.

Mises, combiné et contexte à coller sont repliés dessous : ils restent
accessibles, ils ne barrent plus le passage.

Une ligne suffit :

```
Napoli - Bologna
```

Avec les cotes de votre bookmaker, dans la même ligne :

```
it.1 | Napoli - Bologna | 13/09/2026 20:45 | 1=1.62 N=4.00 2=5.50
```

**Toutes** les lignes saisies reviennent dans le résultat, y compris celles qui
n'ont pas pu être analysées, avec la raison et — si le nom est ambigu — la liste
des candidats et comment trancher.

---

## 3. Ce que vous lisez

En haut, une ligne par match. Dessous, une fiche par match :

- **le marché retenu**, sa cote, **le bookmaker** et **l'heure du relevé** ;
- **la cote minimale** en dessous de laquelle ce pari ne tient plus ;
- la **confiance A/B/C/D** — qualité du dossier, *jamais* une probabilité de
  gagner ;
- le **risque principal** et la **condition d'annulation** ;
- ce qui n'a pas pu être renseigné, et l'action exacte qui le lèverait.

Le logiciel analyse et recommande. **Il ne place aucun pari**, et ne suppose
aucun capital : sans budget saisi, aucune mise n'est chiffrée.

---

## 4. Une heure avant le coup d'envoi

```console
$ python3 -m foot suivre "it.1 | Napoli - Bologna | 13/09/2026 20:45" \
    --coup-denvoi 2026-09-13T20:45 --journal
```

La commande dort entre les contrôles, relève les compositions à T−75 puis T−60,
**réessaie toutes les 5 minutes** jusqu'au coup d'envoi si rien n'est publié,
et s'arrête dès qu'une feuille officielle est en main. Chaque tentative est
inscrite, y compris celles qui n'ont rien trouvé, et le rapport se termine par
la **dernière vérification réussie**.

Sans source de compositions active, la boucle tournerait sans rien lire : la
fiche le dit, et vous collez la feuille à la main dans « Contexte à coller ».
`python3 -m foot config` dit où vous en êtes.

---

## 5. Relire ce qui a été prévu

```console
$ python3 -m foot journal
```

Une ligne par prévision, écrite **avant** le match. Rien n'y est jamais
réécrit : une décision révisée s'ajoute en citant celle qu'elle remplace.

---

## Hébergement en ligne — ce qu'il reste à faire

Le nécessaire est en place ; l'activation est la vôtre, et rien ne se fait dans
votre dos.

| Étape | Commande / geste | État |
|---|---|---|
| Adresse privée | `--jeton` | fourni |
| HTTPS | `--certificat cert.pem --cle cle.pem` | fourni, certificat à obtenir |
| Certificat | Let's Encrypt (`certbot`), ou un reverse proxy (Caddy, nginx) | à faire par vous |
| Clés d'API côté serveur | `.foot-cles` sur la machine, jamais dans le dépôt | fourni |
| Sauvegarde des analyses | `--journal` | fourni |
| Nom de domaine, pare-feu | votre hébergeur | à faire par vous |

Les clés d'API ne quittent jamais le serveur : le moteur les lit dans son propre
environnement, et aucun gabarit de page ne les rend. Un test le vérifie.
