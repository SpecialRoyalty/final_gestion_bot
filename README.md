# Bot réseau Telegram — version initiale pour recette

Projet Python 3.12 / PostgreSQL, préparé pour Railway avec Docker.
Interface française. « Groupe chat gratuit » est le libellé utilisé.

**État : code exécutable et tests locaux fournis. Ce n’est pas un déploiement déjà réalisé ni une certification de fonctionnement en production.** Aucun jeton Telegram, compte Railway ou serveur PostgreSQL réel n’a été utilisé lors de la préparation. Commencer avec des groupes de test et les ventes désactivées.

## Installation Railway

1. Décompresser l’archive. Mettre **le contenu du dossier `telegram-network` à la racine d’un dépôt GitHub privé** : `Dockerfile`, `railway.json`, `bot/`, etc.
2. Créer un bot via le compte officiel **@BotFather** dans Telegram. Garder le jeton secret. Les commandes de création chez BotFather sont distinctes du menu de ce bot.
3. Obtenir son propre identifiant numérique Telegram. Il sera utilisé dans `OWNER_IDS` pour éviter qu’un administrateur d’un groupe prenne le contrôle de tout le réseau.
4. Dans Railway, créer un projet, ajouter PostgreSQL puis un service depuis le dépôt GitHub.
5. Dans les variables du service bot, renseigner :

| Variable | Valeur |
|---|---|
| `BOT_TOKEN` | Jeton remis par BotFather |
| `DATABASE_URL` | Référence à la variable `DATABASE_URL` du service PostgreSQL Railway |
| `OWNER_IDS` | Un ou plusieurs identifiants numériques, séparés par des virgules |
| `TZ` | `Europe/Paris`, ou un autre fuseau IANA |

6. Railway utilise le Dockerfile et démarre `python -m bot.main`. Garder **une seule réplique**. Aucun domaine public, port HTTP ou webhook n’est nécessaire : le bot utilise le long polling.
7. Vérifier le démarrage dans les journaux. Le schéma se crée automatiquement. Les processus de médias et de tâches programmées ont leurs propres connexions PostgreSQL et leurs propres verrous.
8. Ouvrir le bot en privé et toucher le bouton Telegram **Démarrer**. Aucune saisie de `/admin` n’est nécessaire. Ce premier consentement Telegram ne peut pas être supprimé par le code.
9. Créer des **supergroupes privés sans sujets**, ajouter le bot comme administrateur avec les droits de supprimer, inviter, bannir/restreindre les membres. Le bot reçoit les événements de membres explicitement.
10. Dans le menu privé : **Administration → groupe détecté → rôle**. Les rôles sont attribués par les propriétaires du réseau. Les admins du groupe sont vérifiés directement auprès de Telegram pour la gestion locale.
11. Configurer description, objectifs, tarifs en Stars, horaires et Locker. Publier le portail depuis sa fiche. Activer les ventes seulement après avoir renseigné support et conditions, puis effectué la recette ci-dessous.

Ne jamais publier le jeton dans le dépôt, dans une capture ou dans une conversation. Il n’est pas nécessaire de me transmettre ce secret.

## Fonctions livrées

- Message privé de navigation unique, persisté en base et modifié à chaque clic. Si l’utilisateur le supprime, un nouveau menu peut être créé. Notifications exceptionnelles séparées.
- Portail, chats en rotation, 24/24 avec défi ou coupe-file, VIP, groupe chat gratuit hebdomadaire et visite découverte.
- Liens de parrainage personnels **vers le portail uniquement**. Liens d’accès distincts, expirant après 10 minutes, avec demande d’adhésion et vérification de l’identité du demandeur.
- Défis simultanés : les invitations valides après l’activation d’un défi contribuent à tous les défis actifs. Une invitation ne peut être créditée qu’à un seul parrain.
- Validation après 5 minutes de présence continue. Option interne ajoutant un recrutement direct lui-même présent au moins 5 minutes ; pas de récursion infinie de validations.
- Panier multi-VIP et multi-accès ; prix, propriété, disponibilité, devise, montant et identité vérifiés côté serveur. Paiement traité une seule fois et accès accordés dans une transaction.
- Relance unique après 48 heures pour une commande abandonnée. Annulation depuis le panier. Pas de relance pendant un paiement en cours.
- VIP à durée illimitée ou **30 jours à renouvellement manuel** ; expiration persistée et retrait avec nouvelles tentatives. Renouvellement proposé après expiration.
- Rotation quotidienne sans répétition consécutive lorsqu’il y a plusieurs chats. Créneaux et fuseau configurables. Verrouillage hors horaires, annonces et nettoyage de session.
- Locker ON/OFF et seuil. En rotation, un groupe bloqué conserve son créneau ; ouverture si le seuil est atteint dans le créneau, sans prolongation. Après ouverture, un départ ne referme pas la session.
- 24/24 : Locker de lancement ; après lancement, il reste ouvert. Groupe gratuit : ouverture hebdomadaire, indépendante de la rotation.
- Annonce temporaire du chat ouvert dans le portail et les autres chats en rotation, retirée après fermeture.
- Règles communes ou spécifiques, remplacement toutes les 30 minutes durant les ouvertures. Publicités simples et VIP avec remplacement.
- Mots interdits : 1 jour de mute, puis 4 jours, puis bannissement réseau. Pas de message public de sanction.
- Quatre signalements distincts en réponse avec `@admin` : option désactivée par défaut, bannissement réversible et alerte de contrôle aux admins. Albums regroupés par identifiant.
- Bannissements réseau, protection des groupes futurs, objectif de retour et bannissement définitif `/pedo` réservé aux admins. Un bannissement définitif inclut le portail.
- Contrôle des médias : identifiant Telegram, SHA-256, pHash photo, quatre images-clés vidéo FFmpeg. Index par bandes pour chercher les voisins perceptuels sans parcourir toutes les empreintes. Aucune archive permanente de fichiers médias.
- Remplacement d’un groupe : identifiant logique conservé, droits conservés, nouvelles invitations accessibles depuis Mes accès. Les anciens jobs ne sont pas exécutés sur le nouveau groupe.
- Contrôle des permissions toutes les deux heures, alertes privées, file persistante de tâches avec temporisation des nouvelles tentatives.
- Tests et workflow GitHub Actions avec PostgreSQL 16.

## Paiements : correction importante

Telegram impose **Telegram Stars (`XTR`) pour les biens et services numériques vendus dans un bot**. Les accès aux groupes sont donc configurés en Stars. Le projet ne propose pas de paiement PayPal/Revolut/USDT par capture, ni de majoration de 15 %. Le montant en euros d’achat des Stars dépend de Telegram et n’est pas calculé par ce bot.

Le bouton de paiement ouvre un lien de facture, sans ajouter un deuxième menu. Telegram peut ajouter ses propres messages de transaction. Les paiements Stars sont confirmés par Telegram, sans approbation d’une capture par un admin. Les formules de 30 jours ne sont pas des prélèvements automatiques.

Les ventes sont toujours initialisées à OFF ; le réglage est ensuite en base. Pour rembourser, utiliser Administration → Rembourser une commande, avec son identifiant issu des données de commande/audit. Une transaction reçue tardivement après annulation déclenche un remboursement à réessayer si nécessaire.

## Choix et limites à connaître avant ouverture au public

1. **Ce livrable est une première version de recette.** Les appels Telegram sont simulés dans les tests métier ; les essais avec de vrais groupes et paiements restent indispensables. Aucun test de charge à grande échelle n’a été effectué.
2. Le compteur Locker utilise le nombre de membres fourni par Telegram, moins le bot lui-même, puis les entrées/sorties et une réconciliation périodique. **Il inclut les autres bots et les administrateurs** : ce n’est ni une mesure d’activité réelle ni un décompte parfait des seuls membres autorisés. Telegram ne fournit pas l’énumération complète des membres. Les membres présents avant l’installation doivent recevoir un droit via Administration → Accorder un accès s’ils doivent être conservés.
3. Les groupes doivent être privés. Un groupe public permet d’arriver autrement que par une demande d’adhésion ; le retrait réactif ne peut jamais empêcher toute visibilité préalable. Les admins humains peuvent aussi ajouter des personnes ou modifier les permissions hors du bot.
4. Le bot ne peut pas détecter instantanément tout changement de profil d’une personne silencieuse. **Aucun filtrage fondé sur l’écriture, la langue présumée ou l’origine n’est inclus.**
5. Telegram limite généralement la suppression aux messages de moins de 48 heures. Un arrêt prolongé peut donc rendre le nettoyage complet impossible. Les messages sont supprimés par tâches successives ; ce n’est pas une opération atomique. Les erreurs remontent aux admins.
6. Le bot ne peut pas envoyer de message privé à un utilisateur qui n’a jamais démarré le bot ou l’a bloqué. Les accès restent disponibles dans le menu dès son retour. Le bot ne peut pas créer lui-même un nouveau groupe Telegram.
7. L’inactivité est **désactivée par défaut**. Si activée, elle compte les jours calendaires depuis le dernier média, y compris les jours fermés. Ne pas l’activer pour une rotation longue sans accepter ce comportement. Retrait à J4 ; un accès par invitations est remis à zéro, un achat reste conservé et permet de revenir. C’est le compromis choisi pour respecter le paiement unique.
8. Le groupe preuve réserve sa visite lors de l’approbation de la demande, pour 10 minutes. Un échec d’approbation peut consommer cette visite et nécessiter une intervention en base. Les dix minutes ne sont pas une garantie à la seconde en cas de panne Telegram.
9. Le Locker est appliqué aux chats en rotation, gratuits, 24/24 et VIP. Sur un VIP ou un 24/24, il s’agit d’un seuil de lancement : après son premier lancement le groupe reste ouvert. Le portail et la visite preuve n’ont pas de Locker. Un VIP bloqué affiche le seuil sur sa fiche avant l’ajout au panier.
10. Les publicités sont configurées via un petit tableau JSON saisi dans le bot (exemples proposés à l’écran), avec textes et identifiants de photos. Les écrans d’édition restent dans le même menu ; les valeurs sont envoyées comme messages privés. Les publicités tournent pendant les ouvertures, ou dans le 24/24. La gestion guidée pas à pas des campagnes et l’envoi hors ouverture ne sont pas inclus.
11. Les rappels indiquent les Stars ; les relances actuelles reprennent les accès du panier, pas seulement les VIP. Les notifications renvoient au menu via un bouton Accueil.
12. Les médias de plus de 20 Mo ou non analysables sont supprimés avec alerte admin dans cette version, car l’API standard limite le téléchargement. Prévoir un serveur Bot API local et des workers dimensionnés pour lever cette limite. La suppression est asynchrone : le média peut être brièvement visible.
13. La reconnaissance perceptuelle est approximative. Faux positifs et transformations non détectées restent possibles. La similarité vidéo exige trois images-clés proches ; les recadrages, montages et vidéos très courtes ne sont pas garantis. Le contrôle n’identifie pas automatiquement la nature illégale d’un contenu : `/pedo` est une décision humaine de bannissement/référencement.
14. Pas de renouvellement VIP anticipé, de remise, de paiement fractionné ni de période de grâce dans cette version. Pas de filtre de noms. L’absence d’un administrateur n’est pas déduite de son statut en ligne.
15. Une seule installation/réseau par base. Une seule réplique Railway. La haute disponibilité, la migration entre réseaux, le traitement d’albums antérieurs à l’installation et les sauvegardes gérées sont hors périmètre de cette version.
16. Surveiller la croissance des tables et les files de tâches ; activer les sauvegardes PostgreSQL sur Railway. Les empreintes et les identifiants sont persistants. Une politique de conservation/purge adaptée au service doit être définie avant une exploitation durable.

## Recette indispensable

- Créer deux chats privés de test, un portail, un gratuit, un 24/24 et un VIP ; utiliser deux comptes de test.
- Tester le premier Démarrer, vingt changements de page et Actualiser : un seul menu privé.
- Tester parrainage, sortie avant cinq minutes, retour, validation et objectif atteint.
- Tenter de réutiliser le lien d’un autre compte : demande refusée.
- Configurer un Locker au-dessus de la population : aucune ouverture. Atteindre le seuil dans le créneau : ouverture.
- Tester un créneau passant minuit, la fermeture, les règles et la suppression des annonces du portail.
- Redémarrer le service entre une action et son exécution : menus, droits, files et horaires doivent survivre.
- Tester Stars dans l’environnement de test Telegram avant de vendre : paiement réussi, annulation, double clic, facture transmise à un autre compte, accès expiré, remboursement.
- Retirer au bot le droit de bannir : alerte et retrait réessayé. Rétablir le droit et contrôler l’exécution.
- Remplacer un groupe : droits conservés, nouveaux liens personnels, aucun ancien message supprimé dans le nouveau groupe.
- Tester les médias avec des images ordinaires : copie exacte, réencodage, vidéo, fichier trop gros, albums. Ne pas utiliser de contenu illégal pour les tests.

## Exécuter les tests

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

FFmpeg et FFprobe doivent être installés pour le test vidéo. Ils sont installés dans le Dockerfile. Les deux tests PostgreSQL utilisent `DATABASE_TEST_URL` et un schéma temporaire supprimé à la fin ; utiliser une base de test dédiée. Sans cette variable, ils sont explicitement ignorés.

## Structure

- `bot/main.py` : ingestion durable, paiements, processus de fond.
- `bot/ui.py` : menu unique et navigation administrateur/membre.
- `bot/service.py` : droits, paniers, liens, identité logique et remplacement.
- `bot/events.py` : membres et modération.
- `bot/scheduler.py` : rotation, Locker, publications, expiration et reprises.
- `bot/media.py` : empreintes et recherche perceptuelle.
- `bot/schema.sql` : schéma PostgreSQL initial, création idempotente.
- `tests/` : tests métier, média et intégration PostgreSQL.

## Documentation officielle consultée

- https://core.telegram.org/bots/api
- https://core.telegram.org/bots/payments-stars
- https://docs.railway.com/builds/dockerfiles

Consultée le 15 novembre 2026. Les contraintes et la documentation peuvent évoluer.
