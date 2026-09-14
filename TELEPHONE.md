# Depuis le téléphone — une page

Au quotidien : vous ouvrez une adresse, vous tapez vos matchs, vous appuyez sur
un bouton. **Aucune commande à taper.**

---

## L'écran

Trois champs, quatre boutons.

| Champ | À quoi il sert |
|---|---|
| **Rencontres à analyser** | une par ligne |
| **Date et heure de l'analyse** | l'instant depuis lequel on juge |
| **Bookmaker** | celui chez qui vous jouez réellement |

| Bouton | Ce qu'il fait |
|---|---|
| **Analyser** | une fiche par rencontre, plus un tableau de synthèse |
| **Suivre** | lance le contrôle T−75/T−60 **sur le serveur** — il continue si vous fermez l'onglet |
| **Bilan** | montre les prévisions déjà enregistrées, et comment les sauvegarder |
| **Contrôles** | appelle réellement vos fournisseurs sur la rencontre saisie et dit, famille par famille, ce qui revient — plus vos quotas restants |

Une ligne suffit :

```
Napoli - Bologna
```

Avec les cotes de votre bookmaker, dans la même ligne :

```
it.1 | Napoli - Bologna | 20/09/2026 20:45 | 1=1.62 N=4.00 2=5.50
```

Pour **Suivre**, la ligne doit porter l'heure du coup d'envoi : c'est elle qui
place les contrôles T−75 et T−60.

**Toutes** les lignes saisies reviennent dans le résultat, y compris celles qui
n'ont pas pu être analysées, avec la raison et — si le nom est ambigu — la liste
des candidats et comment trancher.

Mises, combiné et contexte à coller sont repliés sous les boutons : accessibles,
mais hors du passage.

---

## Ce que vous lisez

Par rencontre :

- **le marché retenu**, sa cote, **le bookmaker** et **l'heure du relevé** ;
- **la cote minimale** en dessous de laquelle ce pari ne tient plus ;
- la **confiance A/B/C/D** — qualité du dossier, *jamais* une probabilité de
  gagner ;
- le **risque principal** et la **condition d'annulation** ;
- ce qui n'a pas pu être renseigné, et l'action exacte qui le lèverait.

Le logiciel analyse et recommande. **Il ne place aucun pari**, et ne suppose
aucun capital : sans budget saisi, aucune mise n'est chiffrée. Aucune
rentabilité n'est promise — voir [AUDIT.md](AUDIT.md) §12.

---

## Suivre une rencontre

Appuyez sur **Suivre**. Le serveur relève les compositions à T−75 puis T−60,
réessaie toutes les 5 minutes jusqu'au coup d'envoi si rien n'est publié, et
s'arrête quand **deux feuilles officielles complètes** sont en main. Chaque
tentative est inscrite, y compris celles qui n'ont rien trouvé.

Rechargez la page et appuyez de nouveau sur **Suivre** pour voir où en sont les
suivis : la dernière vérification réussie y figure, ou « aucune » si les
compositions ne sont pas publiées.

**Après un redémarrage du serveur.** Si le serveur a été démarré avec
`--suivis` (c'est le cas de la configuration Render fournie), les suivis sont
écrits sur le disque et **repris au démarrage suivant** : chacun repart avec ses
contrôles restants, et porte la mention « repris après redémarrage ». Un suivi
dont le coup d'envoi est passé pendant l'arrêt est marqué **« MANQUÉ »** — il
n'a pas eu lieu, et l'écran ne prétend pas le contraire.

**Ce qui n'est pas promis :** sans `--suivis`, un suivi s'arrête si le serveur
s'arrête, et la page le dit. Sans source de compositions active, la boucle
tourne sans rien lire — la fiche l'écrit aussi.

---

## Vérifier que tout répond

Appuyez sur **Contrôles**, avec une rencontre à venir dans le champ. Chaque
service est **réellement appelé** — rien n'est déduit d'une capacité annoncée —
et vous obtenez une ligne par famille :

```
● Clé API-Football          obtenu — formule « Pro » · 12/7500 requêtes utilisées
● Clé The Odds API          obtenu — 27/500 requêtes utilisées (473 restantes)
● xG (API-Football)         obtenu — 2 ligne(s)
◐ Compositions              répondu, rien à servir — pas encore publiées
● Bookmaker du formulaire   obtenu — 3/3 marché(s) pris chez Pinnacle
```

`●` obtenu · `◐` le service a répondu sans la donnée · `✗` refusé (clé ou
quota) · `○` injoignable · `·` clé absente. **Un `◐` n'est pas une panne** :
des compositions publiées une heure avant le coup d'envoi n'existent pas trois
jours plus tôt. **Un `✗` ou un `·` en est une.**

Aucune clé n'apparaît sur cet écran, même dans un message d'erreur : les
messages des fournisseurs sont nettoyés avant affichage, et un test le vérifie.

---

## Le bilan

Appuyez sur **Bilan**. Vous y voyez les prévisions déjà écrites, **avant** les
matchs. Rien n'y est jamais réécrit : une décision révisée s'ajoute en citant
celle qu'elle remplace.

**Sauvegarde :** le journal est un fichier texte, une ligne par prévision.
Le sauvegarder, c'est le copier ; le restaurer, c'est le remettre en place. Le
chemin exact est affiché sur l'écran Bilan.

La mesure elle-même (calibration, écart aux cotes de clôture) se fait une fois
les résultats connus, et demande des données que la page ne va pas chercher sur
un appui de bouton. Voir [GUIDE.md](GUIDE.md) § 3.

---

## Mise en place — une seule fois

Quelqu'un doit démarrer le serveur une fois. Ensuite, plus rien à taper.

```console
$ python3 -m foot web --hote 0.0.0.0 --jeton --journal --suivis
Interface disponible sur http://0.0.0.0:8000/?jeton=VOTRE_JETON — …
Analyses conservées dans .foot-journal.jsonl (ajout seul).
Suivis conservés dans .foot-suivis.jsonl : 0 repris, 0 manqué(s) pendant l'arrêt.
```

Sur `127.0.0.1`, le jeton est imprimé en clair — la console est la vôtre. Dès
que l'hôte est public, il ne l'est plus : il reste là où vous l'avez posé.
Servir sur une adresse publique **sans** jeton est refusé au démarrage.

Pour installer tout cela en ligne, sans terminal du tout, suivez
[INSTALLER.md](INSTALLER.md).

Recopiez **l'adresse complète, jeton compris**, en remplaçant `0.0.0.0` par
l'adresse locale de l'ordinateur (du type `192.168.1.x`). Une fois ouverte, le
téléphone retient le jeton ; il n'est plus jamais affiché dans la page. Ajoutez
la page à l'écran d'accueil : elle s'ouvre alors comme une application.

### Hébergement en ligne — état réel

| Étape | Comment | État |
|---|---|---|
| Adresse privée | `--jeton` | **fourni** |
| HTTPS | `--certificat cert.pem --cle cle.pem` | **fourni**, certificat à obtenir |
| Clés d'API côté serveur | `.foot-cles`, jamais dans le dépôt | **fourni** |
| Journal sauvegardable et restaurable | `--journal`, fichier en ajout seul | **fourni** |
| Certificat | Let's Encrypt (`certbot`), ou un proxy inverse (Caddy, nginx) | à faire |
| Nom de domaine, pare-feu, redémarrage automatique | votre hébergeur | à faire |
| Suivi qui survit au **redémarrage du serveur** | `--suivis fichier.jsonl` | **fourni** |
| Hébergement clés en main (HTTPS, disque, jeton) | `render.yaml` + [INSTALLER.md](INSTALLER.md) | **fourni**, payant (voir [COUTS.md](COUTS.md) § 2 bis) |

Les clés d'API ne quittent jamais le serveur : le moteur les lit dans son propre
environnement, et aucun gabarit de page ne les rend. Un test le vérifie.

Sur un réseau local, HTTP suffit. Dès que le service sort du Wi-Fi domestique,
il faut **les deux** — `--jeton` et HTTPS : un jeton transmis en clair est un
jeton donné, et la commande vous le dit au démarrage.
