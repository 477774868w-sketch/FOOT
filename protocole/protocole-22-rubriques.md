# Protocole « chirurgical » — 22 rubriques

> **Provenance de ce document.** Le protocole canonique de l'opérateur n'a
> été fourni à aucune des trois itérations : il n'était ni joint aux messages,
> ni présent dans le dépôt (vérifié par recherche à chaque reprise). Le texte
> ci-dessous est une **reconstruction fidèle du cahier des charges écrit par
> l'opérateur**, rubrique par rubrique, et non une invention. Il est stocké ici
> en toutes lettres et dupliqué en JSON lisible par machine.
>
> **Pour le remplacer** : éditez `protocole-22-rubriques.json`, ou passez
> `--protocole chemin.json` à la ligne de commande. Le moteur lit ce fichier ;
> aucune ligne de code n'est à modifier.

## Légende

| État d'implémentation | Sens |
|---|---|
| ● opérationnel | le code existe, s'exécute ici, et sa sortie atteint l'analyse |
| ◐ opérationnel sur données fournies | aucune source automatique, mais l'opérateur peut importer la donnée |
| ○ développé mais inaccessible | l'adaptateur est écrit et testé ; le réseau ou une clé le bloque ici |
| · non développé | aucun adaptateur n'existe ; rien ne s'exécuterait même avec un accès complet |

---

## Phase : identification

### R01 · Identification de la rencontre et statut

Compétition, équipes, catégorie, domicile réel, terrain neutre, horaire, règles de règlement, statut (à venir / commencé / reporté).

- **Donnée requise** : rencontres à venir
- **Traitement** : résolution des noms saisis contre le calendrier chargé ; la date doit correspondre exactement, aucune rencontre n'est glissée
- **Effet réel** : une rencontre non vérifiée au calendrier bloque toute recommandation prématch
- **Entre dans le modèle** : non

### R02 · Traçabilité des sources et statut de confirmation

Valeur, unité, source, fournisseur d'origine, date du fait, date de publication, date de récupération, statut.

- **Donnée requise** : aucune source externe
- **Traitement** : chaque fait entre au registre avec son URL, sa date de fait et sa date de relevé ; l'indépendance se compte sur le fournisseur amont
- **Effet réel** : les contradictions restent visibles et abaissent la confiance
- **Entre dans le modèle** : non

### R03 · Séparation sport / cotes et scellement du dossier

as_of fixé, dossier sportif scellé et empreinté avant toute lecture du marché.

- **Donnée requise** : aucune source externe
- **Traitement** : liste blanche à l'entrée de la phase sportive, empreinte SHA-256 au scellement, journal d'exposition aux cotes
- **Effet réel** : une exposition antérieure au scellement est signalée dans la fiche
- **Entre dans le modèle** : non

---

## Phase : dossier sportif

### R04 · Forme récente (5 à 10 matchs) dans un historique long

- **Donnée requise** : résultats historiques
- **Traitement** : tous les résultats disponibles entrent dans la vraisemblance, pondérés par une demi-vie
- **Effet réel** : fixe attaque[équipe] et défense[équipe], donc les buts attendus
- **Adaptateur** : `foot.collect.openfootball`
- **Entre dans le modèle** : oui

### R05 · Qualité des adversaires rencontrés, au niveau de l'époque

Force des adversaires estimée à la date du match, pas aujourd'hui.

- **Donnée requise** : résultats historiques
- **Traitement** : estimation conjointe de toutes les équipes : la force adverse est un paramètre, pas une correction ajoutée après coup
- **Effet réel** : un résultat contre une équipe forte pèse davantage, structurellement
- **Adaptateur** : `foot.collect.openfootball`
- **Entre dans le modèle** : oui

### R06 · Différentiel domicile / extérieur et taille d'échantillon

- **Donnée requise** : résultats historiques
- **Traitement** : paramètre d'avantage du terrain estimé sur toute la compétition
- **Effet réel** : décale le taux de buts domicile ; mis à zéro sur terrain neutre
- **Adaptateur** : `foot.collect.openfootball`
- **Entre dans le modèle** : oui

### R07 · Buts, xG, npxG, xGA, tirs, grosses occasions, qualité des tirs

- **Donnée requise** : statistiques avancées (xG, tirs)
- **Traitement** : comparaison buts marqués / xG sur les dix derniers matchs
- **Effet réel** : écart buts−xG signalé et converti en scénario de sur-performance ; n'entre pas dans l'estimation, faute de calibration validée
- **Import opérateur** : `--xg-csv`
- **Entre dans le modèle** : oui

### R08 · Penalties, exclusions et périodes déformantes

- **Donnée requise** : statistiques avancées (xG, tirs)
- **Traitement** : nécessite les événements horodatés du match
- **Effet réel** : aucun : la donnée n'est pas disponible et n'est pas reconstituée
- **Entre dans le modèle** : non

### R09 · Performance à onze contre onze et selon l'état du score

Exige des événements détaillés ; ne se déduit pas des totaux.

- **Donnée requise** : statistiques avancées (xG, tirs)
- **Traitement** : segmentation par état du score, impossible sans flux d'événements
- **Effet réel** : aucun : explicitement déclaré indisponible plutôt qu'approché
- **Entre dans le modèle** : non

### R10 · Gardien titulaire, remplaçant et indicateurs

- **Donnée requise** : compositions
- **Traitement** : relevé du gardien annoncé et de son statut (probable / officiel)
- **Effet réel** : un changement de gardien déclenche une réévaluation du dossier
- **Import opérateur** : `--compositions-csv`
- **Entre dans le modèle** : non

### R11 · Absences, retours, minutes attendues et interactions

- **Donnée requise** : absences et blessures
- **Traitement** : liste d'absences avec poste et statut de confirmation
- **Effet réel** : produit un scénario sportif documenté, cité en source, qui peut faire rejeter un pari ; n'applique aucun coefficient arbitraire
- **Import opérateur** : `--absences-csv`
- **Entre dans le modèle** : non

### R12 · Entraîneur, changement de système, confrontation de styles

- **Donnée requise** : compositions
- **Traitement** : nécessite la composition et le dispositif annoncés
- **Effet réel** : aucun : non développé
- **Entre dans le modèle** : non

### R13 · Repos, déplacements, rotation, prolongations récentes

Jours de repos calculables depuis le calendrier des résultats.

- **Donnée requise** : résultats historiques
- **Traitement** : jours écoulés depuis le dernier match, par équipe
- **Effet réel** : affiché seulement ; aucun coefficient de fatigue n'est appliqué faute d'estimation validée
- **Adaptateur** : `foot.collect.openfootball`
- **Entre dans le modèle** : oui

### R14 · Coups de pied arrêtés, transitions, banc, fins de match

- **Donnée requise** : statistiques avancées (xG, tirs)
- **Traitement** : nécessite les événements détaillés
- **Effet réel** : aucun : non développé
- **Entre dans le modèle** : non

### R15 · Météo, pelouse et arbitre

- **Donnée requise** : arbitre, météo
- **Traitement** : l'arbitre figure dans l'archive football-data.co.uk ; la météo exigerait un fournisseur distinct
- **Effet réel** : aucun ici : l'archive est inaccessible depuis cet environnement
- **Adaptateur** : `foot.collect.footballdata`
- **Entre dans le modèle** : non

---

## Phase : modèle

### R16 · Liaison donnée → variable → effet sur la prévision

Toute donnée affichée n'est pas une donnée utilisée ; la distinction est explicite.

- **Donnée requise** : aucune source externe
- **Traitement** : chaque constat porte son type, sa variable et son effet, ou la mention « affiché seulement »
- **Effet réel** : la fiche sépare les informations utilisées des informations montrées
- **Entre dans le modèle** : non

### R17 · Modèle de référence, enrichissements et régularisation

Poisson, Dixon-Coles et Elo comme références ; petits échantillons traités par régularisation.

- **Donnée requise** : résultats historiques
- **Traitement** : Dixon-Coles pondéré, régularisation inversement proportionnelle au nombre de matchs effectifs, Elo en contrôle croisé
- **Effet réel** : fournit la loi jointe des scores dont dérivent tous les marchés
- **Adaptateur** : `foot.collect.openfootball`
- **Entre dans le modèle** : oui

### R18 · Scénarios, contre-analyse et conditions d'invalidation

- **Donnée requise** : aucune source externe
- **Traitement** : scénarios typés : sensibilité, incertitude d'estimation, événement sportif documenté
- **Effet réel** : seules la sensibilité et les événements documentés peuvent rejeter un pari ; l'incertitude d'estimation est rapportée, pas filtrante
- **Entre dans le modèle** : non

---

## Phase : marché

### R19 · Comparaison des marchés disponibles

- **Donnée requise** : cotes
- **Traitement** : chaque cote fournie est réglée sur la même loi jointe, remboursements et demi-règlements compris
- **Effet réel** : classe les marchés par croissance logarithmique ; la fiche nomme ceux qui ont réellement été comparés
- **Adaptateur** : `foot.collect.footballdata`
- **Import opérateur** : `cotes saisies (1=, N=, 2=, BTTS:, TOTAL:, DC:, DNB:, AH:, TE:)`
- **Entre dans le modèle** : non

### R20 · Calculs de marché : loi jointe et règlements asiatiques

- **Donnée requise** : aucune source externe
- **Traitement** : unions, intersections et lignes quart calculées case par case sur la grille des scores
- **Effet réel** : seuils de prix résolus sur le profil de règlement exact
- **Entre dans le modèle** : non

### R21 · Compositions probables puis officielles, et réévaluation

- **Donnée requise** : compositions
- **Traitement** : contrôles planifiés à T−75 et T−60, statut probable puis officiel
- **Effet réel** : un changement décisif produit un nouveau dossier scellé qui remplace l'ancien, avec sa raison
- **Import opérateur** : `--compositions-csv`
- **Entre dans le modèle** : non

---

## Phase : restitution

### R22 · Décision, confiance, risque et restitution

- **Donnée requise** : aucune source externe
- **Traitement** : confiance A/B/C/D fondée sur la couverture, la convergence et les contradictions — jamais sur la probabilité de gain
- **Effet réel** : une confiance D ou un modèle non convergé interdisent la recommandation
- **Entre dans le modèle** : non

---
