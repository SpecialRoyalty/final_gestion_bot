# Bot Telegram — réseau de groupes

Version de préproduction, Python 3.12 / PostgreSQL / Railway. Interface en français.
Le code est fourni avec tests locaux et configuration Docker. Les scénarios SQL de migration
ont été exécutés sur PostgreSQL 16.2 en mode autonome. Les tests complets via connexion PostgreSQL
et le déploiement avec un vrai token Telegram restent à exécuter.
Ne pas ouvrir les ventes avant la recette décrite plus bas.

## Démarrage Railway

1. Décompresser cette archive et pousser son contenu dans un dépôt privé.
2. Créer un projet Railway, ajouter PostgreSQL, puis un service depuis le dépôt.
3. Définir les variables du service :
   - `BOT_TOKEN` : token fourni par BotFather ; garder ce secret dans Railway.
   - `DATABASE_URL` : référence à la variable PostgreSQL de Railway.
   - `OWNER_IDS` : identifiants numériques des responsables du réseau, séparés par des virgules.
   - `TIMEZONE` : `Europe/Paris` par défaut ; choisir le fuseau des horaires.
   - `SUPPORT_CONTACT` : contact réel pour l'assistance aux paiements.
4. Railway utilise le Dockerfile. Garder **une seule réplique**, sans serveur web ni domaine.
   Le processus utilise le long polling ; le verrou PostgreSQL bloque une seconde instance.
5. Chaque responsable ouvre le bot et appuie sur le bouton Telegram « Démarrer ».
6. Créer les groupes dans Telegram puis ajouter le bot comme administrateur, avec les droits
   supprimer les messages, restreindre les membres, inviter les utilisateurs.
   Utiliser des **supergroupes privés**, avec liste des membres visible au bot.
7. Dans le bot : Administration → groupe → rôle, description, objectif, prix, horaires → Activer.
   Un propriétaire du réseau approuve les groupes : une personne extérieure ne peut pas
   raccorder son propre groupe à votre réseau simplement en y ajoutant le bot.
8. Configurer d'abord le portail, puis les groupes de chat. Les administrateurs des groupes
   activés sont reconnus par Telegram et peuvent gérer leur groupe. Les opérations globales
   restent réservées à OWNER_IDS.

Le bot ne crée pas de groupe Telegram lui-même : l'administrateur crée le groupe puis le connecte.
Aucune commande admin à saisir. Les commandes techniques `/start`, `/support`, `/paysupport`
sont traitées ; `/pedo` est l'exception de modération demandée. L'utilisateur navigue par boutons.
Le token ne doit jamais être commité ni envoyé dans cette conversation.

## Fonctionnalités présentes

- Un message privé de navigation édité à chaque écran, avec bouton Actualiser et barres de progression.
- Portail public configurable, boutons vers les fiches personnelles, annonces temporaires du groupe ouvert.
- Groupes classiques, 24 h/24 avec coupe-file, VIP, preuve et gratuit hebdomadaire.
- Liens de parrainage individuels vers le portail. Une personne ne compte qu'une fois.
- Validation après 5 minutes, vérification de présence réelle au portail ; mode simple ou cascade.
- Plusieurs défis simultanés : **chaque invitation validée fait progresser tous les défis**.
- En cascade, le filleul doit avoir recruté une personne restée 5 minutes. Pas de récursion infinie.
- Droits d'accès stables, liés à l'identifiant logique du groupe, et liens d'entrée de 10 minutes
  avec demande d'adhésion vérifiée contre l'identité et les droits de l'utilisateur.
- Coupe-file et VIP : **PayPal (+15 %), Revolut (+15 %) et USDT TRC20**, avec preuve et validation humaine.
  Coordonnées globales et prix de base par groupe/moyen configurables par boutons.
  Prix et destinataire figés à la création de la demande ; aucun taux EUR/USDT implicite.
  Vérification réelle du transfert par les responsables OWNER_IDS avant validation.
  Double validation bloquée et cas d'accès déjà obtenu signalé pour régularisation manuelle.
- VIP à paiement unique ou **accès de 30 jours à renouvellement manuel** ; pas de prélèvement automatique.
- Rotation quotidienne d'un seul groupe classique ; créneaux traversant minuit ; gratuit hebdomadaire indépendant.
- Locker ON/OFF : seuil de membres présents, comptage Telegram moins le bot. Les autres bots et admins
  sont compris dans ce décompte ; il ne mesure pas l'activité réelle. Seuil vérifié toutes les ~20 secondes.
- Si le seuil est atteint pendant le créneau, ouverture pour le temps restant ; aucune fermeture
  anticipée si le nombre baisse ensuite. Le locker s'applique aussi aux groupes permanents à leur ouverture.
- Textes/photos/vidéos autorisés à l'ouverture ; restrictions à la fermeture ; les admins restent libres.
- Nettoyage des messages observés par le bot à la fermeture, par lots de 100 avec file de reprises.
- Règles périodiques remplacées toutes les 30 minutes d'ouverture ; publicités texte/photo tournantes
  par groupe, avec intervalles 2/4/6/12 h et activation/désactivation. On peut y rédiger des promotions VIP.
- Inactivité média : avertissement à 72 h, exclusion à 96 h ; compteur personnel du groupe remis à zéro.
  Un nouveau média accepté relance le délai. Cela concerne les groupes classiques et 24 h/24,
  y compris les accès coupe-file, mais pas les VIP. Le temps continue de courir pendant les fermetures.
- Mots interdits : restrictions 1 jour, puis 4 jours, puis bannissement réseau ; traitement silencieux.
- Quatre signalements par personnes distinctes sur la même réponse ou le même album → ban réseau.
  Les admins sont protégés. L'« absence d'un administrateur » ne peut pas être déduite de sa présence en ligne :
  dans cette version le seuil de quatre signalements s'applique sans cette condition.
- Débannissement par nouvelles invitations ; bannissement définitif `/pedo` sans retour, portail compris.
- Anti-repost : identifiant Telegram, SHA-256, pHash photo, trois images vidéo FFmpeg (10/50/90 %).
  Les vidéos exigent deux correspondances de frames. Index SHA et bandes pHash pour réduire les candidats.
  Blacklist réseau ; aucun média brut n'est stocké durablement. Fichiers temporaires effacés après analyse.
- Traitement média dans un thread et une connexion DB séparés pour ne pas bloquer les boutons/paiements.
- Remplacement d'un groupe depuis sa fiche, conservation des droits et notification des bénéficiaires.
- Contrôles des droits du bot toutes les deux heures et alertes aux propriétaires.
- File persistante de suppressions/restrictions/notifications, reprise et backoff sur les erreurs Telegram.

## Limites à connaître avant utilisation réelle

- Les paiements externes sont validés manuellement : aucune API PayPal/Revolut/blockchain ne confirme
  le transfert. Une capture ne prouve pas à elle seule sa réception. Vérifier le compte destinataire.
  Le bot ne réalise aucun remboursement externe automatique.
- Les nouvelles factures Stars sont désactivées. Les événements de paiement Stars déjà en vol
  d'une ancienne version restent traités pour ne pas perdre une transaction antérieure.
- Les notifications nécessitent que l'utilisateur ait démarré le bot et ne l'ait pas bloqué.
- Le message public et le message privé sont distincts. Les messages d'un utilisateur (ex. saisie admin)
  restent dans sa conversation ; « un seul message » désigne le message de navigation du bot.
- Une invitation n'est pas une preuve d'identité humaine. La validation de présence ne suffit pas contre
  des fermes de comptes. Prévoir des contrôles complémentaires si ce risque est constaté.
- Le bot ne peut ni inspecter tout l'historique ni énumérer rétroactivement tous les membres avec cette API.
  Préférer des groupes neufs. Les messages/adhésions antérieurs ou manqués ne sont pas entièrement récupérables.
- Telegram limite la suppression de certains messages et de messages anciens (généralement 48 h).
  La suppression n'est pas atomique ; elle se déroule par lots. Les anomalies doivent être surveillées.
- Pour le stockage de contenu malveillant : seules les empreintes des médias déjà observés peuvent être
  propagées. `/pedo` est une action humaine de modération, pas un détecteur automatique de contenu illégal.
- L'API standard limite le téléchargement des fichiers pour analyse avancée ; ici 20 Mio maximum.
  Les fichiers plus grands restent couverts par `file_unique_id`, et une alerte est produite.
- Les correspondances perceptuelles sont heuristiques : tester le seuil sur vos médias ; il existe des
  faux positifs et des faux négatifs, surtout pour les images uniformes et vidéos recadrées/montées.
  Les nouveaux médias peuvent rester visibles quelques instants pendant le traitement de la file.
- Les fichiers temporaires ne sont pas conservés après analyse ; ne pas monter de stockage persistant
  sur le répertoire temporaire. Le hash et les identifiants Telegram sont persistés.
- Le remplacement de groupe conserve l'identité logique. **Retirer/révoquer les anciens liens et fermer
  l'ancien groupe** si vous en avez encore le contrôle. Ne pas réutiliser un groupe déjà actif comme remplaçant.
- Les publicités VIP utilisent l'outil publicitaire commun. Une interface dédiée pour lier plusieurs
  campagnes à un VIP, un mode « une seule fois » et une image de couverture du portail restent à ajouter.
- Les compteurs ne s'animent pas en continu ; ils sont recalculés lors de la navigation/actualisation.
- Les calendriers sont persistés à l'avance : changer l'horaire d'un groupe modifie sa fenêtre planifiée.
- Les traductions, le filtrage par alphabet et l'exclusion selon une langue supposée ne sont pas implémentés.
- Le mécanisme de reprise n'offre pas une garantie « exactement une fois » sur les envois Telegram :
  une interruption entre l'envoi et son enregistrement peut créer une notification en double.
- Migrations SQL versionnées exécutées dans une transaction avant les index. Le démarrage
  contrôle les colonnes attendues et refuse les transformations ambiguës. Sauvegarder PostgreSQL
  avant le déploiement d’une évolution de schéma.

## Vérification locale

Les tests de logique utilisent SQLite en mémoire et un faux client Telegram. Aucune transaction réelle.

```bash
python -m unittest discover -s tests -v
```

Pour exécuter le bot localement : installer `requirements.txt` et FFmpeg, puis exporter les variables
ou charger un `.env` avec votre shell. Le bot ne charge pas automatiquement le fichier `.env`.

```bash
python -m app.main
```

SQLite (`DATABASE_URL=sqlite:///development.db`) est uniquement destiné au développement.
Railway doit utiliser PostgreSQL pour la persistance et le verrou de processus.

## Recette obligatoire avant ouverture au public

Sur un réseau de test, avec deux administrateurs et plusieurs comptes de test :

1. Vérifier les boutons et le retour sans nouveaux messages de navigation.
2. Rejoindre/quitter le portail avant et après cinq minutes ; vérifier les modes simple/cascade.
3. Tester le même lien d'accès avec son propriétaire et un autre compte ; vérifier le refus du second.
4. Créer des demandes de test pour les trois moyens, transmettre des preuves fictives puis vérifier
   validation/refus, double validation, preuve réutilisée et accès acquis pendant la vérification.
5. Tester les échéances, renouvellements et suppression effective après expiration. Vérifier
   le parcours de régularisation manuelle pour un transfert reçu mais un accès devenu indisponible.
6. Tester le locker sous/au-dessus du seuil, minuit, heure d'été/hiver, fermeture et groupe gratuit.
7. Tester texte/photo/vidéo, album, photo recompressée, vidéo, blacklist et média >20 Mio.
8. Retirer les permissions du bot ; constater les alertes et les reprises après restauration.
9. Redémarrer le service avant une fermeture et pendant une tâche différée, puis remplacer un groupe.
10. Vérifier sauvegarde et restauration de PostgreSQL, débit sous charge et surveillance des files en échec.

Documentation de référence consultée :
- https://core.telegram.org/bots/api
- https://core.telegram.org/bots/payments-stars

## Configuration des paiements manuels (version 2)

1. Administration → Moyens de paiement : saisir le destinataire PayPal, les coordonnées Revolut
   et l’adresse USDT TRC20. Un tiret désactive un moyen pour les nouveaux achats.
2. Administration → Groupe → Tarifs : saisir séparément les bases PayPal et Revolut en EUR,
   puis le tarif USDT en USDT. Exemple : base 10 EUR → total 11,50 EUR avec supplément.
   Les prix acceptent deux décimales et sont stockés en centimes, sans calcul flottant.
3. L’utilisateur choisit le moyen, consulte le total et le destinataire, paie, puis envoie sa preuve.
4. Les responsables OWNER_IDS reçoivent la capture d’écran et un bouton Examiner. Les administrateurs
   de groupes ordinaires peuvent gérer les tarifs de leur groupe, mais ne valident pas les paiements.
5. Le responsable vérifie le transfert reçu puis clique Paiement reçu ou Refuser.
6. Un VIP de 30 jours démarre à la validation ; la double validation ne prolonge pas sa durée.

Une seule demande en cours par utilisateur/groupe. Les demandes en vérification n’expirent pas
silencieusement. Une demande sans preuve peut être annulée par le membre uniquement s’il n’a pas payé.
La même preuve identifiée par Telegram est refusée si elle a déjà été soumise ; une image modifiée
peut contourner cette détection, d’où la vérification du transfert réel par l’administrateur.
Les preuves restent hébergées chez Telegram ; seuls leurs identifiants sont enregistrés dans la base.

Mise à jour depuis la version 1 : sauvegarder la base, remplacer le code, redémarrer. Les tables
manuelles sont ajoutées sans supprimer les données existantes. Les anciens prix Stars ne sont
pas convertis en euros : configurer les nouveaux tarifs et coordonnées avant de proposer les achats.
Les commandes en état « conflict » restent visibles pour traitement/remboursement hors du bot.

## Version 3 — structure et correction de démarrage

Le point d’entrée principal est désormais `python -m app.main`. Le Dockerfile l’utilise.
Le petit lanceur `bot/main.py` est conservé uniquement pour les services Railway dont la commande
personnalisée utilise encore `python -m bot.main` ; il appelle le nouveau point d’entrée.

| Répertoire | Responsabilité |
| --- | --- |
| `app/db/` | Connexion, contrôle du schéma, migrations SQL versionnées et diagnostic en lecture seule. |
| `app/handlers/` | Routage des mises à jour, saisies, callbacks et écrans Telegram. |
| `app/keyboards/` | Construction des boutons et claviers. |
| `app/services/` | Accès, paiements, modération, planification, traitement média et client Telegram. |
| `app/utils/` | Calculs de progression, créneaux et fonctions communes. |
| `app/application.py` | Assemblage des handlers et services. |
| `app/main.py` | Démarrage du service et boucle de réception. |

Preuve de paiement : **capture d’écran uniquement** (photo Telegram ou image PNG/JPEG).
Les PDF sont refusés avant tout changement d’état ou notification aux responsables.

### Erreur « column gid does not exist »

L’ancienne initialisation créait les tables avec IF NOT EXISTS puis les index, sans vérifier
si une table déjà présente avait les colonnes attendues. En particulier, `one_pending` utilise
`orders(uid, gid)`. Le traceback seul ne prouve pas quel ancien schéma est installé.

Le correctif vérifie le schéma avant la création des index et applique uniquement ces réparations :

- Colonne `gid` seule manquante sur une table vide : ajout de la colonne attendue.
- Ancienne colonne texte `group_id` : renommage en `gid` seulement si toutes les valeurs existantes
  correspondent aux identifiants logiques de `groups.id`, ou si la table est vide.
- Autre schéma ou valeur impossible à relier : arrêt explicite avec les noms des tables et colonnes.
  Aucune ligne supprimée, aucun identifiant de groupe inventé, aucun DROP TABLE automatique.

Les réparations et migrations se font dans la même transaction : un échec annule l’ensemble.
Les fichiers SQL déjà appliqués sont vérifiés par empreinte et ne doivent pas être modifiés.
Le thread média ouvre une connexion séparée sans relancer les migrations.

Si le schéma de ta base est différent de ces cas, exécuter dans l’environnement Railway :

```bash
python -m app.db.doctor
```

Ce diagnostic est en lecture seule. Il affiche les noms et types des colonnes, les colonnes manquantes
et le nombre de lignes des tables incompatibles. Il n’affiche ni URL de connexion, ni token,
ni contenu des lignes. Fournir cette sortie permet de préparer une migration adaptée au schéma réel.
Ne pas supprimer PostgreSQL pour contourner cette erreur.

### Mise à jour Railway

1. Sauvegarder PostgreSQL.
2. Remplacer le code par cette version, en conservant les variables existantes.
3. Utiliser le Dockerfile fourni ; si une commande de démarrage personnalisée est définie,
   la passer à `python -m app.main`.
4. Redéployer puis vérifier les logs de migration et de démarrage.
5. Si le diagnostic signale un schéma incompatible, exécuter la commande de diagnostic ci-dessus.

### Résultats des vérifications

- 51 tests locaux réussis (logique, paiements, captures, migrations et conservation des données).
- 3 scénarios SQL réussis sur PostgreSQL 16.2 en mode autonome : installation/réexécution,
  réparation de `orders.gid` absent sur table vide, renommage avec conservation des valeurs.
- 6 tests d’intégration PostgreSQL via psycopg fournis, non exécutés dans cet environnement :
  les sockets locaux y sont interdits. La CI GitHub les exécute avec un service PostgreSQL 16.
- Aucun accès à la base Railway de l’utilisateur ni essai Telegram réel effectué.

Pour exécuter les tests d’intégration, définir `TEST_POSTGRES_URL` vers une base de test jetable,
jamais vers la production, puis lancer `python -m unittest discover -s tests -v`.
Les tests créent et retirent uniquement leurs schémas aléatoires préfixés `test_network_`.
