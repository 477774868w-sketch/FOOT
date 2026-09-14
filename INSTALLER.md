# Installer FOOT sur votre téléphone — le guide court

Neuf étapes, une quinzaine de minutes, **rien à taper dans un terminal**.

> **Avant de commencer.** Une seule étape est payante, la 6 : Render affiche le
> montant avant que vous ne confirmiez. Le détail est dans
> [COUTS.md § 2 bis](COUTS.md) — **≈ 7,25 $/mois** attendus. Si l'écran de
> Render annonce autre chose, arrêtez-vous : c'est lui qui a raison, pas moi.

---

## A. Les deux clés (10 min, sur ordinateur ou téléphone)

**1. The Odds API — gratuit.**
Ouvrez <https://the-odds-api.com/>, « Get API key », formulaire, e-mail.
La clé arrive par courriel. **Gardez-la de côté**, ne la collez nulle part
encore. Plan gratuit : 500 requêtes par mois, largement assez pour commencer.

**2. API-Football — payant, votre décision.**
Ouvrez <https://www.api-football.com/>, créez un compte, choisissez le plan
**Pro** (≈ 19 €/mois, 7 500 requêtes par jour). La clé s'affiche dans votre
tableau de bord. **Gardez-la de côté aussi.**

> Prenez les clés **chez les éditeurs**, jamais par un intermédiaire, et ne les
> écrivez dans aucun message, aucun fichier, aucun dépôt. La suite ne vous
> demandera de les coller qu'à **un seul endroit** : le formulaire de Render.

---

## B. Le service (5 min)

**3. Cliquez sur le bouton.**

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/477774868w-sketch/FOOT/tree/claude/code-masterpiece-o2mbbl)

Ou, si le bouton ne s'affiche pas, ouvrez cette adresse :
`https://render.com/deploy?repo=https://github.com/477774868w-sketch/FOOT/tree/claude/code-masterpiece-o2mbbl`

**4. Créez un compte Render** si vous n'en avez pas (« Sign up », e-mail ou
GitHub). Render lit `render.yaml` dans la branche et vous propose le service
déjà configuré : rien à régler vous-même.

**5. Remplissez les deux champs — les seuls.**

| Champ affiché par Render | Ce que vous collez |
|---|---|
| `API_FOOTBALL_KEY` | la clé API-Football de l'étape 2 |
| `ODDS_API_KEY` | la clé The Odds API de l'étape 1 |

Laissez `FOOT_JETON` tel quel : Render le tire au hasard pour vous.
Laissez `PYTHON_VERSION` tel quel.

Ces deux valeurs restent chez Render, chiffrées. Elles ne sont écrites dans
aucun fichier du dépôt, ne s'affichent sur aucune page, et n'apparaissent dans
aucun message d'erreur ni dans les journaux du service — c'est vérifié par des
tests.

**6. Vérifiez le montant, puis « Apply » / « Create ».**
C'est la seule étape qui engage de l'argent. Render récapitule : instance
**Starter** et **disque de 1 Go**. Attendu : **≈ 7,25 $/mois**.
Le premier déploiement prend deux à trois minutes.

---

## C. Ouvrir la page (2 min)

**7. Relevez votre jeton.**
Dans Render : votre service → onglet **Environment** → ligne `FOOT_JETON` →
« reveal » / l'icône œil. Copiez la valeur.

**8. Ouvrez l'adresse, jeton compris.**
L'adresse du service est en haut de sa page Render, du type
`https://foot-xxxx.onrender.com`. Ouvrez-la **sur le téléphone** en ajoutant le
jeton :

```
https://foot-xxxx.onrender.com/?jeton=COLLEZ_ICI_LE_JETON
```

Sans le jeton, la page refuse l'accès — c'est voulu. Une fois ouverte, le
téléphone retient le jeton : les fois suivantes, l'adresse courte suffit.

**9. Ajoutez-la à l'écran d'accueil.**
Safari → Partager → « Sur l'écran d'accueil ». Elle s'ouvre alors comme une
application, sans barre d'adresse.

---

## D. Le premier usage — vérifiez avant de faire confiance

Sur la page, tapez une rencontre **à venir** dans le grand champ, puis appuyez
sur **Contrôles**.

```
Napoli - Bologna 20/09/2026 20:45
```

Vous obtenez une ligne par service **réellement appelé** :

```
● Clé API-Football          obtenu — formule « Pro » · 12/7500 requêtes utilisées
● Clé The Odds API          obtenu — 27/500 requêtes utilisées (473 restantes)
● Parcours Analyser         obtenu — 1/1 rencontre(s) analysée(s)
● xG (API-Football)         obtenu — 2 ligne(s)
● Absences                  obtenu — 3 ligne(s)
● Compositions              obtenu — 22 ligne(s)
● Cotes (The Odds API)      obtenu — 14 prix
● Bookmaker du formulaire   obtenu — 3/3 marché(s) pris chez Pinnacle
```

Les symboles : `●` obtenu · `◐` le service a répondu mais n'avait pas la donnée
· `✗` refusé (clé ou quota) · `○` injoignable · `·` clé absente.

**Un `◐` n'est pas une panne** : des compositions ne sont publiées qu'une heure
avant le coup d'envoi, et un `◐` sur « Compositions » trois jours plus tôt est
la réponse correcte. **Un `✗` ou un `·`, si, est une panne** : clé mal collée,
quota épuisé, ou plan qui ne sert pas ce que vous croyiez.

Ensuite, l'usage quotidien :

| Vous voulez | Champ à remplir | Bouton |
|---|---|---|
| une fiche par rencontre | les rencontres, une par ligne | **Analyser** |
| suivre les compositions | la rencontre **avec l'heure du coup d'envoi** | **Suivre** |
| revoir vos prévisions | rien | **Bilan** |
| revérifier vos services | une rencontre à venir | **Contrôles** |

Laissez le champ **date vide** : l'analyse se fait *maintenant*. Une date
remplie **rejoue** cet instant-là et ignore tout ce qui a été publié après.

---

## Ce qui survit à un redémarrage, et ce qui ne survit pas

Render redémarre votre service à chaque déploiement, et parfois de lui-même.
Tout ce qui compte est écrit sur le disque persistant, monté sur `/var/foot` :

| | Après un redémarrage |
|---|---|
| **Journal des prévisions** (`/var/foot/journal.jsonl`) | **conservé** — ajout seul, jamais réécrit |
| **Cache** (`/var/foot/cache`) | **conservé** — vos crédits ne sont pas redépensés |
| **Suivis lancés** (`/var/foot/suivis.jsonl`) | **repris** : ceux dont le coup d'envoi est encore devant repartent tout seuls, avec leurs contrôles restants |
| Un suivi dont le coup d'envoi est **passé pendant l'arrêt** | **marqué « MANQUÉ »** à l'écran Suivre — il n'a pas eu lieu, et rien ne prétendra le contraire |
| Les clés d'API | **conservées** par Render, jamais par le service |

Au démarrage, le service écrit dans son journal Render :

```
Suivis conservés dans /var/foot/suivis.jsonl : 1 repris, 0 manqué(s) pendant l'arrêt.
```

Après un redémarrage, l'écran **Suivre** porte la mention
« repris après redémarrage » sur chaque suivi relancé.

**Sauvegarde.** Les trois fichiers sont du texte. Pour en garder une copie :
Render → votre service → onglet **Shell** →

```
cat /var/foot/journal.jsonl
```

puis copiez ce qui s'affiche dans un fichier chez vous. Restaurer, c'est coller
le contenu à la même place. Le journal étant en ajout seul, une copie n'est
jamais périmée : elle est simplement plus courte que l'actuelle.

---

## Si quelque chose ne va pas

| Symptôme | Cause la plus probable | Geste |
|---|---|---|
| « Accès privé » | jeton absent ou faux | rouvrez l'adresse avec `?jeton=…` (étape 7) |
| `·` sur une clé | champ laissé vide à l'étape 5 | Render → Environment → corrigez → « Save » (le service redémarre) |
| `✗ refusé (clé ou quota)` | clé erronée, ou quota du mois épuisé | la ligne « QUOTAS » du même écran dit lequel des deux |
| `○ injoignable` | panne réseau côté service | réessayez dans quelques minutes |
| Page longue à s'ouvrir | première visite après un redémarrage | normal, une seule fois |
| Le montant Render ne correspond pas | le tarif a changé depuis ce document | **arrêtez-vous**, dites-le moi |

---

## Ce que le logiciel ne fait pas

Il **analyse et recommande**. Il ne place aucun pari, ne se connecte à aucun
compte de jeu, et ne suppose aucun capital : sans budget saisi, aucune mise
n'est chiffrée. La note sur 100 et la confiance A/B/C/D mesurent la **qualité du
dossier**, jamais une probabilité de gagner. Aucune rentabilité n'est promise —
la mesure sur 80 rencontres, en [AUDIT.md](AUDIT.md) § 12, donne un intervalle
qui contient zéro.

**Les rubriques encore incomplètes**, telles que la grille les affiche sur
chaque fiche — les mots ci-dessous sont ceux que vous lirez à l'écran :

| Rubrique | État affiché | Pourquoi, et ce que ça changerait d'y revenir |
|---|---|---|
| **R08** penalties, exclusions | `✗ non développé` | demande les **événements horodatés** du match ; API-Football Pro sert des totaux, pas la chronologie. Les fabriquer à partir des totaux donnerait un chiffre d'allure sérieuse et sans fondement |
| **R09** jeu à onze contre onze | `✗ non développé` | même raison : segmenter par état du score exige le flux d'événements |
| **R14** coups de pied arrêtés | `✗ non développé` | même raison |
| **R15** météo, pelouse, arbitre | `✗ développé mais inaccessible` | l'adaptateur existe ; la source (football-data.co.uk) est gratuite mais injoignable depuis l'environnement où ce code a été écrit. À retester depuis Render : ce sera peut-être joignable de là |
| **R07** xG, npxG, tirs | `~ partielle` | les xG arrivent ; **npxG, tirs, grosses occasions, qualité des tirs** ne sont pas tous servis. `Contrôles` mesure lesquels le sont sur **votre** compte, champ par champ |

Aucune n'est comblée par une estimation : une donnée absente reste marquée
absente. Le reste de la grille — 17 rubriques sur 22 — est renseigné dès que les
deux clés fonctionnent.
