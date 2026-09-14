# Ce que coûterait la suite — proposition, avant tout engagement

Ce document **n'engage rien**. Aucun abonnement n'est souscrit, aucun
déploiement payant n'est lancé. Les prix sont ceux annoncés par les
fournisseurs, à vérifier sur leurs sites avant de payer quoi que ce soit.

`python3 -m foot config` et `python3 -m foot fournisseurs --couverture`
impriment les mêmes informations depuis le logiciel, mesurées plutôt que
déclarées.

---

## 1. Ce qui manque, et ce qui le débloquerait

| Rubrique bloquée | Ce qu'il faut | Fournisseur |
|---|---|---|
| R07 xG, npxG, tirs · R08 penalties et exclusions · R09 à onze contre onze · R14 coups de pied arrêtés | statistiques avancées | API-Football, ou une source xG dédiée |
| R11 absences, retours, minutes attendues | listes d'absences | API-Football |
| R10 gardien titulaire · R21 compositions officielles | compositions | football-data.org (plan payant) ou API-Football |
| R19 comparaison des marchés, automatique | cotes prématch | The Odds API |
| R15 météo, pelouse, arbitre | arbitre | football-data.co.uk — **gratuit**, mais injoignable depuis cet environnement |

---

## 2. Trois scénarios chiffrés

Les montants sont mensuels, en ordre de grandeur, tels qu'annoncés par les
fournisseurs à ce jour.

### A. Rien du tout — 0 €

Ce qui tourne aujourd'hui : calendrier, résultats, modèle, marchés cotés à la
main, décision, suivi, journal, mesure. **13 rubriques sur 22.**
Les 9 autres restent marquées indisponibles, jamais comblées.

### B. Le minimum utile — ≈ 19 €/mois

**API-Football, plan Pro** (≈ 19 €/mois, 7 500 requêtes/jour).
Débloque absences, compositions et xG : **R07, R10, R11, R21**. **L'adaptateur
est écrit et testé** — il ne manque que la clé.

Ce qu'il ne débloque **pas**, et qu'aucun abonnement ne débloquera seul :
**R08, R09 et R14** demandent la chronologie des événements, pas des totaux de
match. Les fabriquer à partir des totaux produirait un chiffre d'apparence
rigoureuse et sans fondement ; le logiciel refuse de le faire.

Avant de payer plus qu'un mois : `foot couverture` mesure **champ par champ** ce
que votre plan renvoie réellement. Un plan peut servir les tirs sans servir les
xG, et c'est précisément la différence qui justifie ou non la dépense.

Passe la couverture de 13 à environ **17 rubriques sur 22** — sous réserve de ce
que `foot couverture` mesurera sur votre compte.

### C. Complet — ≈ 49 €/mois

B, plus **The Odds API** (gratuit jusqu'à 500 requêtes/mois ; ≈ 30 $/mois
au-delà). Débloque la comparaison automatique des marchés (**R19**) et, surtout,
rend mesurable l'**écart au prix de clôture** : c'est le seul indicateur qui dise
si le moment de la prise est bon, et il est aujourd'hui non mesuré.

Le plan gratuit de The Odds API suffit pour quelques rencontres par semaine —
500 requêtes/mois, et une requête coûte d'autant plus de crédits qu'elle demande
de marchés. **Commencez par le gratuit**, et ne payez que si le quota se révèle
trop court à l'usage.

### Hébergement

| Option | Coût | Remarque |
|---|---|---|
| Sur votre propre machine, Wi-Fi domestique | 0 € | ce qui fonctionne aujourd'hui |
| **Render** (configuration fournie, `render.yaml`) | **≈ 7,25 $/mois** | détaillé au § 2 bis ci-dessous |
| Petit serveur en ligne (VPS) | ≈ 5 €/mois | moins cher, mais tout est à installer et à renouveler à la main |
| Nom de domaine | ≈ 10 €/an | facultatif : Render fournit une adresse en `.onrender.com` |
| Certificat HTTPS | 0 € | inclus chez Render ; `certbot` ou Caddy sur un VPS |

---

## 2 bis. Hébergement sur Render — le coût exact, avant toute validation

**Ce que vous paieriez, par mois, pour le service décrit dans `render.yaml` :**

| Ligne | Montant annoncé | Pourquoi cette ligne |
|---|---|---|
| Instance web, plan **Starter** | **7,00 $/mois** | le plan gratuit n'accepte **pas** de disque persistant et s'endort après ~15 min sans visite : le journal disparaîtrait à chaque déploiement et le contrôle de T−75 n'aurait pas lieu |
| **Disque persistant**, 1 Go | **0,25 $/mois** (0,25 $/Go/mois) | journal des prévisions, fichier des suivis, cache |
| Trafic sortant | **0 $** en pratique | une enveloppe mensuelle est incluse ; quelques pages de texte par jour en consomment une fraction négligeable |
| Certificat HTTPS, adresse `*.onrender.com` | **0 $** | inclus |
| **Total** | **≈ 7,25 $/mois** | |

À quoi s'ajoute, séparément et chez leurs éditeurs :

| | Montant | Décision |
|---|---|---|
| API-Football, plan **Pro** | ≈ 19 €/mois | la vôtre, directement chez API-Football |
| The Odds API, plan **gratuit** | 0 € | 500 requêtes/mois — commencez par là |

**Deux réserves, à lire avant de valider :**

1. **Ces montants sont ceux que je connais, pas ceux que j'ai lus aujourd'hui.**
   L'environnement où ce code a été écrit n'a pas accès à la page tarifaire de
   Render. **Le montant qui fait foi est celui que Render affiche à l'écran
   avant que vous ne confirmiez la création du service.** S'il diffère de ce
   tableau, c'est Render qui a raison — arrêtez-vous et dites-le moi.
2. **Le disque se redimensionne à la hausse, jamais à la baisse.** 1 Go est
   très large pour un journal en texte ; ne prenez pas plus « par sécurité ».

**Ce que ce coût n'achète pas :** rien sur le plan sportif. L'hébergement rend
l'outil joignable depuis le téléphone, sans ordinateur allumé à la maison. Il
n'ajoute aucune donnée, ne débloque aucune rubrique, et ne change rien à ce que
dit le § 3 ci-dessous.

**Ce qui coûte 0 € et fait presque tout :** votre propre machine, en Wi-Fi
domestique, avec `python3 -m foot web --jeton`. Si vous n'analysez que chez
vous, l'hébergement est une dépense de confort.

---

## 3. Ce que l'argent **n'achète pas**

- **Aucune rentabilité.** La mesure du §12 de [AUDIT.md](AUDIT.md) donne un
  intervalle de confiance qui contient zéro sur 80 rencontres : aucun avantage
  n'est démontré. Plus de données peuvent améliorer le dossier ; elles ne
  transforment pas un avantage non démontré en avantage.
- **Aucune garantie de couverture.** Un plan payant expose souvent moins que son
  site ne décrit. C'est pourquoi `foot fournisseurs --couverture` **sonde** et
  rapporte ce que votre compte obtient réellement : payez d'abord le mois le
  moins cher, sondez, puis décidez.
- **Aucun suivi durable sans fichier de suivis.** Un suivi lancé sans l'option
  `--suivis` ne survit pas au redémarrage du serveur. La configuration Render
  fournie l'active et pose le fichier sur le disque persistant : les suivis
  repartent après un déploiement, sauf ceux dont le coup d'envoi est passé
  entre-temps, marqués « manqué » plutôt que présentés comme faits.

---

## 4. Ce que je vous demande

Rien n'est engagé tant que vous ne le dites pas. Trois décisions, séparées :

1. **Ouvrir un compte gratuit The Odds API ?** (0 €, 500 requêtes/mois) —
   l'adaptateur est écrit et testé ; il ne manque que la clé.
2. **Ouvrir un compte gratuit football-data.org ?** (0 €) — l'adaptateur est
   écrit et testé, mais le plan gratuit **ne sert pas les compositions** : la
   sonde vous le confirmera sur votre propre clé avant toute dépense.
3. **Souscrire API-Football Pro (≈ 19 €/mois) ?** — la seule dépense qui change
   vraiment la couverture. L'adaptateur est désormais écrit et testé ; la
   première chose à faire avec la clé est `foot couverture`, qui dira champ par
   champ ce que le plan sert avant que vous ne renouveliez.

4. **Créer le service Render (≈ 7,25 $/mois) ?** — la configuration est écrite
   et vérifiée ; rien n'est créé tant que vous ne confirmez pas dans Render, et
   le montant affiché à ce moment-là est celui qui compte.

Dites-moi lesquelles vous voulez, et je m'arrête là où vous vous arrêtez.
