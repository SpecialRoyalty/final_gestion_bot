## Version 19 — invitations en surplus, locker et réactivité

Remplacer **toute l’application**, conserver la base et les variables existantes, puis redéployer **un seul replica**. La migration `008_fair_access.sql` est embarquée dans Python et s’applique automatiquement. Les sept migrations déjà livrées restent identiques. Cette archive n’a pas été déployée sur Railway depuis cet environnement.

### Invitations

- Chaque groupe classique ou 24 h/24 conserve son lien personnel vers le portail.
- À la validation d’une nouvelle invitation : objectif du lien en priorité s’il est encore incomplet, puis objectif actif auquel il manque le moins d’invitations. Les égalités sont départagées par identifiant de groupe pour garder un résultat stable.
- Une invitation n’est attribuée qu’à **un seul** objectif. Les accès déjà actifs, notamment payés, sont ignorés. Les groupes VIP, preuve et gratuits ne sont pas transformés en objectifs gratuits.
- Si tous les objectifs sont complets, l’invitation est conservée en réserve. La réserve est affichée dans **Inviter mes amis** et répartie lors des passages automatiques ou de la navigation lorsqu’un objectif devient disponible.
- Les invitations déjà validées avant cette version ne sont pas déplacées rétroactivement. Leurs compteurs et les accès acquis restent inchangés. Les récompenses de signalement de bug restent affectées au groupe choisi.
- L’attribution est transactionnelle, avec verrou par invitant sur PostgreSQL. Rejouer une validation ne recompte pas l’invitation. Le groupe d’origine du lien est conservé séparément.

### Inactivité et réhabilitation

L’ancien contrôle utilisait l’âge du dernier média, même si les membres ne pouvaient pas publier. Le nouveau compteur cumule seulement le temps observé où le groupe est activé, ouvert et dispose d’une liste d’administrateurs récente. Fermetures, locker, suspension du groupe et longues interruptions de suivi ne sont pas facturés aux membres. Les admins sont exemptés.

La règle devient explicite : avertissement après **72 heures d’ouverture effective** sans média, exclusion après **96 heures d’ouverture effective**. Un nouveau média, une nouvelle entrée ou un accès nouvellement accordé réinitialise le suivi. Le compteur commence à zéro lors du déploiement, faute d’historique fiable. Les anciens avertissements encore en file sont retirés. Cette durée est du temps d’ouverture cumulé, pas quatre jours de calendrier.

Avant d’exécuter une exclusion en attente, le bot revérifie le groupe, le média, l’entrée, le compteur et les admins. L’accès n’est révoqué qu’après confirmation de l’appel Telegram. Les nouvelles exclusions pour inactivité sont consignées dans l’audit. Un ancien bannissement d’admission est désormais annulé si l’accès a été accordé entre-temps.

**Administration → État du réseau → Réhabilitations locker** présente les anciens cas possibles : accès révoqué mais non expiré, membre absent, groupe avec locker, sans bannissement global. Le code précédent ne mémorisait pas le motif précis ni les périodes d’ouverture accessibles à chaque membre : **il ne permet pas de réhabiliter automatiquement et avec certitude toutes les anciennes victimes**. L’admin confirme les cas réellement concernés. Cette liste n’est donc pas une preuve d’exclusion injustifiée et peut être incomplète si la configuration a changé.

Après confirmation : accès restauré sans nouveau paiement ni invitations à refaire, anciens bannissements d’admission/inactivité encore en attente retirés, débannissement du groupe mis en file, notification personnelle avec bouton d’accès. Les sanctions réseau, permanentes ou anti-liens restent protégées. Les dates d’expiration des accès payés ne sont pas prolongées. Si le bot est absent du groupe, l’action attend son rétablissement ; le bot ne peut pas réinscrire un membre à sa place.

### Communication aux membres

Au premier démarrage de cette version, une campagne unique est préparée pour les utilisateurs connus disposant d’un panneau privé. Elle explique les invitations en surplus, la réserve et la correction du locker, dans leur langue enregistrée. L’envoi se fait en arrière-plan, au plus un message par seconde, sous réserve des autres limites Telegram. Le même destinataire n’est pas ajouté à nouveau au redémarrage.

Les blocages sont classés comme non joignables. Les réponses perdues et les erreurs serveur laissant la livraison incertaine ne sont pas répétées automatiquement. Leurs états restent visibles dans **État du réseau**. Le déploiement prépare et commence la campagne ; aucune notification réelle n’a été envoyée depuis l’environnement de développement.

### Réactivité et erreurs réseau

- Contrôle d’inactivité par requête groupée : moins de 15 appels SQL pour le test de 1 000 membres, au lieu de requêtes par membre.
- Nettoyages et bannissements de fond soumis au budget d’arrière-plan ; la modération en direct utilise toujours sa capacité prioritaire.
- Reconnexion immédiate unique d’une connexion persistante fermée pour une **lecture** Telegram. Aucun renvoi immédiat d’un message dont la livraison serait incertaine.
- `429` : respect du délai de Telegram sans consommer de tentative ni bloquer le thread en attente.
- Envois de messages/photos dont la réponse est perdue : arrêt avec diagnostic, au lieu d’une répétition pouvant créer des doublons.
- Les anciennes erreurs restent consultables. Une erreur `Bad Gateway` ou `TimeoutError` extérieure peut toujours survenir ; le code ne peut pas garantir la disponibilité de Telegram ou du réseau Railway.

### Médias au-delà de 20 Mo

L’API hébergée `api.telegram.org` limite le téléchargement à 20 Mo. Changer `MAX_MEDIA_MB` seul **ne relève pas cette limite**.

Cette version prend en charge un serveur Bot API auto-hébergé :

1. Installer le serveur officiel Telegram Bot API, avec ses propres `api_id` et `api_hash`, en mode `--local`, sur un réseau privé accessible au bot. Ce serveur n’est pas installé par cette archive.
2. Suivre la procédure Telegram de bascule (`logOut` de l’API hébergée avant le serveur local). Arrêter l’ancienne instance du bot pendant cette transition. Ne pas exposer le service HTTP et ses fichiers au public.
3. Monter le répertoire de fichiers du serveur au **même chemin absolu** dans les deux conteneurs. Configurer `TELEGRAM_LOCAL_FILES_ROOT` avec ce répertoire.
4. Définir `TELEGRAM_API_URL=http://telegram-bot-api:8081` selon l’adresse réelle. Définir `MAX_MEDIA_MB=200` (défaut local : 200 Mio ; plafond configurable : 2 000 Mio).
5. Redéployer puis utiliser **Réessayer les médias temporaires** : en mode serveur personnalisé, cette action inclut aussi les anciens échecs de taille, par lots de 20, si le groupe est toujours valide.

Le téléchargement se fait par blocs de 1 Mio vers un fichier temporaire ; toute la vidéo n’est plus conservée simultanément en mémoire Python. Les tailles déclarées et effectivement lues sont contrôlées, ainsi que le répertoire autorisé. Les durées de téléchargement et de décodage restent bornées. L’analyse média demeure dans son processus séparé. Une vidéo trop volumineuse n’arrête pas l’analyse des autres fichiers ; son analyse visuelle n’est simplement pas disponible tant que la limite n’est pas résolue.

Références officielles : https://core.telegram.org/bots/features#local-bot-api-server et https://core.telegram.org/bots/api#getfile.

### Validation

261 tests exécutés : 255 réussis, 6 intégrations PostgreSQL réseau ignorées. Huit scénarios SQL vérifiés séparément sur PostgreSQL 16.2 local, dont les nouvelles horloges, la réserve et la sélection des réhabilitations. Schéma et imports de l’archive vérifiés. Tests Telegram simulés : aucun chiffre de ces tests ne mesure la latence réelle entre les utilisateurs, Railway et Telegram.

Les anciennes notes ci-dessous décrivent les versions précédentes ; les règles ci-dessus les remplacent en cas de différence.

---

## Version 18 — démarrage résilient et pauses Telegram

Le démarrage appelait `getMe` avant la gestion des erreurs de la boucle principale : une interruption réseau faisait sortir le programme et Railway redémarrait le conteneur. Cette version effectue `getMe` et `deleteWebhook` dans une initialisation avec reprise progressive (2, 4, 8, 16 puis 30 secondes entre tentatives). Elle attend aussi tout `retry_after` plus long renvoyé par Telegram. Elle ne crée les workers qu’après succès, conserve l’offset et ne supprime pas les mises à jour en attente.

En cas d’interruption durable, le processus reste en attente et écrit **Démarrage en attente** avec la méthode, la catégorie de l’erreur et le délai suivant. Le bot ne répond pas encore tant que Telegram reste inaccessible. Le type de cause réseau (par exemple délai expiré ou connexion réinitialisée) est conservé sans afficher l’URL ni le token. Une authentification refusée produit un message explicite demandant de vérifier `BOT_TOKEN`, plutôt qu’une reprise réseau sans fin. L’arrêt SIGTERM interrompt l’attente et ferme la base.

La campagne de remplacement du portail suspend désormais toute sa diffusion pendant un `429`, au lieu de reporter seulement un destinataire puis de continuer avec le suivant. Le délai est persistant ; les refus pour limitation ne consomment pas le budget des véritables erreurs. Les créations/modifications/révocations de liens en arrière-plan sont espacées d’au moins deux secondes par chat, sans retarder les simples boutons de navigation. État du réseau indique le délai restant. Un succès efface l’ancienne erreur de campagne.

Installer **toute cette version**, notamment `app/main.py` et le nouveau `app/services/startup.py`, avec la base et les variables existantes, puis redéployer un seul replica. Aucun nouveau schéma. Ne pas recréer la base ni régénérer le token sur la seule base d’une erreur réseau.

Les tests simulent les coupures au démarrage, la reprise complète de `main`, un délai Telegram de 65 secondes sans plafond erroné, l’authentification refusée, l’arrêt propre et la cadence des liens. Ils ne constituent pas un test de connectivité entre Railway et Telegram. Si cette connexion reste indisponible, la nouvelle version permet de diagnostiquer l’attente mais ne répare pas elle-même le réseau extérieur.

---

## Version 17 — remplacement par sélection du groupe

Depuis la fiche de l’ancien portail : **Remplacer le groupe Telegram → choisir le nouveau groupe → Confirmer le remplacement**. Le bot affiche les groupes qu’il a détectés, sans rôle et désactivés. Ajouter le bot administrateur au nouveau supergroupe privé, ne pas lui attribuer de rôle dans l’admin, puis utiliser **Actualiser** dans la liste. Il n’est plus nécessaire de saisir l’identifiant négatif. Les anciens boutons « remplacer » ouvrent également cette liste après déploiement.

La confirmation indique l’ancienne configuration et la nouvelle destination. Les droits du bot sont vérifiés avant toute mutation. La fiche provisoire sans rôle du nouveau groupe est retirée, et l’ancienne fiche conserve son identifiant logique, son rôle, ses textes et les accès. Pour un portail, la campagne de nouveaux liens et de notifications de la version 16 est préparée automatiquement. Le titre configuré de l’ancien portail est conservé.

Un groupe déjà configuré n’est jamais écrasé. La destination et son rôle sont recontrôlés dans la transaction ; une sélection périmée ou un double clic ne déclenche pas une deuxième campagne. Les confirmations expirent après dix minutes. Une absence de droits ou une erreur Telegram produit un message explicite avec **Réessayer**, sans remplacement partiel.

Déployer cette archive avec la base et les variables existantes, un seul replica, puis rouvrir **Administration → ancien portail → Remplacer le groupe Telegram**. Aucun nouveau schéma par rapport à la version 16. Les contrôles de la suite automatisée couvrent sélection, confirmation, droits, erreur réseau, cible déjà configurée, conservation des données et double clic. Les appels Telegram de ces tests sont simulés.

---

## Version 16 — remplacement du portail, incidents et admissions

### Déployer avant de remplacer le portail

Conserver la base et les variables, installer ce code puis redéployer avec **un seul replica Railway**. La migration embarquée `007_recovery.sql` ajoute les campagnes de remplacement, les suspensions de groupes et les détails des échecs médias. Prévoir environ **16 connexions PostgreSQL** (deux files d’admission et un worker de notifications dédié). Les migrations précédentes ne sont pas modifiées.

### Remplacer et prévenir

Dans **Administration → portail → Remplacer le groupe Telegram**, indiquer le nouveau supergroupe privé où le bot dispose de ses droits. Après vérification, les objectifs d’invitation sont sauvegardés avant de retirer les anciens liens. Le worker dédié prépare de nouveaux liens permanents pour les objectifs précédemment utilisés, puis envoie une notification dans la langue choisie, avec un bouton **Inviter mes amis**. Les invitations déjà validées et les accès sont conservés. Les invitations non encore validées doivent être constatées dans le nouveau portail ; leur ancienne présence n’est pas assimilée à une présence dans le nouveau.

Tous les utilisateurs connus sont comptabilisés. Ceux qui n’ont jamais ouvert leur espace privé, ont bloqué le bot ou sont définitivement bannis sont non joignables ; le bot ne peut pas leur garantir un message privé. Les notifications et leur progression survivent au redémarrage. Une réponse perdue pendant l’envoi donne un état « résultat non confirmé » : pas de renvoi automatique pouvant créer un doublon. **État du réseau → Notifications du portail : relancer** permet à l’admin de relancer les échecs, ou d’accepter explicitement le risque de doublon des livraisons non confirmées.

Les anciennes adresses Telegram ne sont pas transférables et les publications partagées par les utilisateurs ne peuvent pas être réécrites par le bot. La notification leur demande de remplacer leurs liens publiés. Installer cette version avant le remplacement : aucune campagne rétroactive n’est inventée pour un remplacement déjà effectué par une ancienne version.

### Portail ou groupe inaccessible

Une réponse explicite indiquant que le bot est expulsé/absent, ou un événement Telegram confirmant son retrait ou sa perte du rôle administrateur, suspend le groupe sans supprimer sa configuration. Les tâches périodiques cessent de le sélectionner et les jobs de ce chat restent en attente, sans appels Telegram répétés. Une seule alerte est créée ; sa déduplication est atomique entre workers. **Une erreur réseau, un 502 ou un délai expiré ne désactive jamais un groupe.**

Réajouter le bot avec ses droits puis cliquer **Activer** vérifie les permissions et reprend le groupe. Un remplacement reprend l’état activé antérieur à la suspension. Les tâches visant un ancien portail inaccessible ne sont pas envoyées au nouveau chat, car leurs identifiants de messages appartiennent à l’ancien.

### Médias et anciens panneaux

L’ancien message « analyses arrêtées » ne permettait pas de distinguer le réseau, le décodage et la taille du fichier. Les nouvelles erreurs conservent une cause, le groupe, le message et les données nécessaires à une reprise. Un fichier trop gros pour le téléchargement via l’API Telegram standard (limite documentée de 20 MB) est indiqué comme non analysable visuellement ; il ne provoque plus des alarmes répétées. La détection des liens et des identifiants de médias déjà connus demeure indépendante, mais aucune analyse visuelle complète n’est prétendue pour un fichier non téléchargé.

Les erreurs réseau/décodages trop longs ont des reprises bornées. **État du réseau → Réessayer les médias temporaires** remet jusqu’à 20 dossiers admissibles en file ; un ancien message n’est jamais réanalysé comme s’il appartenait au groupe Telegram de remplacement. Les anciens dossiers dont l’ancienne version n’a pas conservé le détail ne peuvent pas être reconstitués automatiquement. Les compteurs historiques restent visibles, regroupés par cause, plutôt que trois fois la même erreur de panneau.

Telegram refuse notamment la suppression de messages âgés de plus de 48 heures. Pour les nouveaux panneaux dont l’âge est connu, le bot évite cet appel impossible et retire les anciens boutons. Le texte de l’ancien message peut rester présent : cette limite Telegram n’est pas masquée comme une suppression réussie.

### Réactivité

Les admissions utilisent deux files, réparties par utilisateur. Une reprise d’un arrivant ne bloque plus tous les arrivants de son groupe. Les notifications du remplacement utilisent leur propre worker et la capacité d’arrière-plan, séparée des boutons, admissions et suppressions. Une saturation locale passagère reporte brièvement la requête au lieu d’imposer systématiquement une seconde ; les délais `retry_after` imposés par Telegram sont toujours respectés. Le décodage vidéo limite aussi les threads des filtres.

Les derniers délais avant traitement et de traitement restent mesurés dans État du réseau. Les tests de concurrence bloquent une admission et une campagne et vérifient que d’autres admissions/boutons progressent. Ils simulent Telegram : ils ne promettent ni une latence de production ni la disparition de toutes les interruptions réseau.

### Validation

213 tests exécutés : 207 réussis, 6 intégrations PostgreSQL réseau ignorées dans cet environnement. Schéma, requêtes de migration et campagnes vérifiés séparément avec PostgreSQL 16.2 en mode local. Imports et démarrage du schéma vérifiés sans identifiants Telegram. Les règles manquantes restent à saisir par l’admin ; aucun texte de règles n’est inventé.

Références Telegram : https://core.telegram.org/bots/api#getfile et https://core.telegram.org/bots/api#deletemessage.

---

## Version 15 — liens de partage sans expiration

Les invitations personnelles de partage vers le portail, ainsi que les liens publics des groupes gratuits, sont créées **sans date d’expiration Telegram**. Une visite ultérieure du menu réutilise la même adresse, même après plusieurs jours, sans appel Telegram supplémentaire. Chaque invitation conserve son auteur et son groupe cible ; le décompte ne change pas.

Au déploiement, le bot prolonge progressivement les anciens liens de partage **encore valables**, un par cycle en arrière-plan, sans changer leur adresse. Consulter son invitation prolonge immédiatement le lien encore actif. La base n’enregistre le caractère permanent qu’après confirmation Telegram. Une erreur entraîne une reprise différée et reste visible dans les diagnostics. Les liens déjà expirés ne sont pas réactivés par cette version : rouvrir **Inviter mes amis** fournit un nouveau lien permanent à partager.

**Régénérer mon lien** reste disponible : cette action révoque l’ancien lien, même permanent, sans retirer les invitations déjà validées. Remplacer le groupe Telegram met en file la révocation de ses anciens liens. Un lien peut aussi être révoqué manuellement dans Telegram ; « sans expiration » ne signifie pas irrévocable.

Les liens personnels d’entrée dans les chats restent valables dix minutes et sont liés au bénéficiaire. Le contrôle des accès payants, des bannissements et des durées de visite demeure actif. Un lien public permanent d’un groupe gratuit conserve la validation d’entrée par le bot.

Déploiement : remplacer le code puis redéployer avec la même base et **un seul replica**, comme pour la version 14. Aucun changement de schéma requis. La convention interne `links.expires=-1` signifie permanent ; `0` reste révoqué ; une valeur positive conserve sa date limite. Ne pas redéployer une ancienne version qui ignore cette convention.

Vérifications : stabilité après un an simulé, suivi des invitations, prolongation sans changement d’adresse, erreurs sans faux succès, liens expirés/révoqués non réactivés, conversion progressive, accès gratuit contrôlé et régénération. Les appels Telegram sont simulés ; la conversion réelle s’effectuera après déploiement.

Documentation Telegram : https://core.telegram.org/bots/api#createchatinvitelink et https://core.telegram.org/bots/api#editchatinvitelink.

---

## Version 14 — priorité au temps de réaction

Cette section décrit la version actuelle ; les sections précédentes conservent l’historique.

### Déployer

Remplacer le code, conserver la base et les variables existantes, puis redéployer avec **un seul replica Railway**. La migration embarquée `006_priority_actors.sql` ajoute les index de file, les identifiants de conversation et les diagnostics d’actions arrêtées, sans effacer les données. Les événements en attente sont réaffectés aux nouvelles files au démarrage. Prévoir **environ 14 connexions PostgreSQL** : 13 pour le processus principal et ses threads, une pour le processus média. Le transport et les caches sont propres à chaque processus. Aucun `expected.json` externe n’est nécessaire.

### Ce qui réduit le délai

- L’accueil utilise la liste d’administrateurs mémorisée : aucun appel `getChatAdministrators` avant d’afficher un accueil membre. Un rafraîchissement de liste est effectué en arrière-plan, un groupe par cycle. Un nouvel administrateur peut devoir actualiser après ce rafraîchissement ; les promotions/démotions reçues mettent immédiatement le cache à jour. Les changements sensibles contrôlent toujours les droits actuels.
- Un arrivant ordinaire ne vide plus le cache administrateur. L’ingestion d’un média ordinaire ne consulte pas les administrateurs via Telegram.
- Un événement reçu réveille immédiatement son worker. Les requêtes SQL d’attente à vide sont espacées ; une reprise arrivée à échéance est détectée sous 250 ms hors charge et temps de requête.
- Une reprise retardée bloque uniquement les événements suivants du même utilisateur dans la même file. Les autres utilisateurs du worker continuent.
- Suppressions des arrivées/départs, admissions et signalements `/pedo` / réponses `@admin` ont leurs files distinctes. Les opérations de modération urgentes peuvent dépasser les messages ordinaires du même groupe ; les règles de ban restent persistantes et s’appliquent aux messages traités ensuite.
- L’analyse des médias tourne dans **un processus séparé**, avec un seul média traité à la fois. Un fichier déjà analysé est réutilisé sans téléchargement/décodage supplémentaire. Cela réduit la contention Python ; une machine saturée en CPU ou mémoire peut néanmoins ralentir tous les processus.
- Connexions HTTPS réutilisées par thread. Les tâches secondaires ont une capacité limitée pour préserver les interactions. Une réponse `429` déclenche un délai avant les prochains appels au chat concerné ; ce délai n’endort pas le worker et ne consomme pas son budget de tentatives. Les appels de polling restent indépendants.
- Un lien d’accès personnel encore valable est réutilisé sans appel Telegram de débannissement systématique.

### Erreurs et suivi

Une suppression « message introuvable » est terminée. Une suppression définitivement refusée est arrêtée et visible dans **État du réseau**, au lieu d’être relancée indéfiniment. Une annonce dont la suppression est définitivement refusée reste suivie et bloque son remplacement pour éviter les doublons ; un administrateur doit examiner le message et les droits. Les analyses impossibles ou ayant échoué cinq fois sont arrêtées et comptées. Les alertes réseau identiques sont regroupées ; les nouvelles erreurs de code restent signalées.

**Administration → État du réseau** distingue maintenant l’attente avant traitement et la durée de traitement des dernières interactions, en millisecondes. La durée inclut les appels Telegram du traitement, mais pas le délai de transport entre le téléphone et la réception par le polling. Utiliser ces mesures après déploiement pour comparer aux lenteurs observées. Aucun test local ne garantit une latence réseau réelle ni l’absence future de pannes Telegram.

### Autres ajustements de la demande jointe

- Groupe **Preuve → Durée et condition de visite** : 5, 10, 15, 30, 60, 120, 240 ou 1440 minutes ; valeur initiale 10 minutes. La durée commence à l’admission. La condition ON exige au moins une invitation réelle validée dans le réseau, pas un simple clic de partage ni une récompense de bug. Le contrôle est effectué aussi à l’entrée, même avec un lien obtenu auparavant. Une visite par personne ; les visites déjà commencées gardent leur échéance.
- Les autres bots ajoutés sont exclus via la file de modération ; le bot du réseau est exempté. Telegram peut refuser l’exclusion d’un administrateur : l’action arrêtée et l’erreur restent visibles, il faut alors lui retirer ses droits.
- `/pedo` reconnaît les administrateurs anonymes du groupe et traite une réponse même si le message original n’était pas encore indexé. Les quatre signalements `@admin` doivent toujours provenir de personnes distinctes.
- La fiche membre d’un groupe gratuit/classique affiche le statut promotionnel ouvert pendant son créneau, même si le locker maintient les permissions fermées, comme l’annonce du portail. Les droits réels ne sont pas modifiés par cet affichage.

### Vérifications

La suite couvre les reprises sans blocage des autres utilisateurs, la priorité devant 1 000 messages, les appels évités sur l’accueil et les liens, les limites Telegram, les suppressions terminales, les visites, les bots ajoutés, les signalements et le démarrage du processus média. Les appels Telegram sont simulés. Les migrations sont aussi exercées sur PostgreSQL 16.2 en mode local.

---

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
  campagnes à un VIP, et un mode « une seule fois » restent à ajouter. L’image du portail est disponible depuis la version 6.
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
   et l’adresse USDT TRC20. Le bouton Désactiver ce moyen le retire des nouveaux achats.
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
Les sources SQL embarquées déjà appliquées sont vérifiées par empreinte et ne doivent pas être modifiées.
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

- 53 tests locaux réussis (logique, paiements, captures, migrations et conservation des données).
- 3 scénarios SQL réussis sur PostgreSQL 16.2 en mode autonome : installation/réexécution,
  réparation de `orders.gid` absent sur table vide, renommage avec conservation des valeurs.
- 6 tests d’intégration PostgreSQL via psycopg fournis, non exécutés dans cet environnement :
  les sockets locaux y sont interdits. La CI GitHub les exécute avec un service PostgreSQL 16.
- Aucun accès à la base Railway de l’utilisateur ni essai Telegram réel effectué.

Pour exécuter les tests d’intégration, définir `TEST_POSTGRES_URL` vers une base de test jetable,
jamais vers la production, puis lancer `python -m unittest discover -s tests -v`.
Les tests créent et retirent uniquement leurs schémas aléatoires préfixés `test_network_`.

## Version 4 — ressources de migration embarquées

Correction du démarrage `FileNotFoundError: .../migrations/expected.json`.
Le schéma attendu et les deux sources SQL sont intégrés directement dans `app/db/migration.py`.
Il n’y a plus de lecture de `expected.json`, de dossier de ressources obligatoire, ni de recherche
par glob de fichiers SQL au démarrage. Les noms et empreintes des migrations précédentes sont
conservés, afin de rester compatibles avec une base déjà initialisée.

Le Dockerfile exécute `python -m app.selfcheck` lors de la construction : ce contrôle importe
le point d’entrée réel et initialise deux fois une base SQLite en mémoire. Il ne contacte ni
Telegram ni PostgreSQL et ne nécessite aucun secret. Ce contrôle ne remplace pas la recette
sur Railway et Telegram.

Déploiement : extraire l’archive puis placer **le contenu** du dossier `telegram-network`
à la racine du dépôt/service, avec le Dockerfile, `requirements.txt` et le dossier `app`.
Remplacer la version précédente complète, puis redéployer. Conserver DATABASE_URL et les données.
Commande de démarrage : `python -m app.main`.

Tests : 53 réussis, 6 tests d’intégration réseau PostgreSQL non exécutés ici (sockets interdits).
Un test copie seulement les fichiers `.py`, se place dans un autre répertoire de travail et
exécute le contrôle : aucune dépendance aux anciens fichiers JSON/SQL.

## Version 5 — menus adaptés au rôle et publication du portail

La fiche d’administration affiche uniquement les réglages utiles au rôle sélectionné :

| Rôle | Réglages propres au rôle |
| --- | --- |
| Portail | Texte, Activer et publier, Publier / actualiser ; aucun objectif, tarif, abonnement ou horaire. |
| Chat classique | Objectif, coupe-file, locker, créneau, règles, anti-repost, publicités. |
| Chat 24 h/24 | Objectif, coupe-file, locker, règles, anti-repost, publicités ; aucun créneau. |
| VIP | Tarifs, paiement unique/mensuel, locker, règles, anti-repost, publicités ; aucun objectif d’invitations. |
| Preuve | Description ; visite de 10 minutes, une seule fois. |
| Gratuit | Locker, jour hebdomadaire, créneau, règles, anti-repost, publicités ; aucun tarif. |

Les contrôles de rôle s’appliquent aussi côté serveur aux anciens boutons et saisies en attente.
Les libellés techniques True/False et les numéros de jours sont remplacés par ON/OFF, unique/mensuel
et les noms de jours. Le seuil du locker est visible lorsqu’il est activé.

Un groupe enregistré avec le rôle Portail reste désactivé tant que le responsable n’a pas cliqué
**Activer et publier**. L’activation vérifie les droits du bot puis publie immédiatement le message
public, même si aucun groupe de chat n’existe. La fiche indique séparément l’état activé/désactivé
et le message publié/pas encore publié. Les erreurs de publication sont affichées sur cette fiche.
Le bouton **Publier / actualiser** modifie le message public existant ; si celui-ci a été supprimé,
il en recrée un. Modifier le texte d’un portail actif actualise immédiatement le message.
Changer le rôle d’un groupe actif le désactive : il faut valider les réglages du nouveau rôle et le réactiver.

`/start` reste disponible à tout moment dans la conversation privée. Il annule une saisie en attente
et ramène à l’accueil en éditant le message de navigation. « Un seul processus de polling » désignait
une seule instance serveur recevant les événements Telegram, pas une limite d’utilisation de `/start`.
Le texte du log de démarrage est désormais explicite.

Vérification : 71 tests locaux réussis ; 6 tests réseau PostgreSQL non exécutés dans cet environnement.
Les nouveaux tests couvrent tous les rôles, l’activation sans groupe de chat, le refus de permissions,
les erreurs de publication, le rafraîchissement sans doublon, la récupération d’un message supprimé,
les anciens boutons et les appels répétés de `/start`.

## Version 6 — supervision, statistiques et présentation

### Statistiques et interprétation

Administration → **Statistiques** : fenêtres de 24 heures, 7 et 30 jours, avec pagination.
Le panneau affiche utilisateurs connus, invitations validées et en attente, visiteurs uniques de
chaque fiche, accès accordés, entrées/sorties observées, membres suivis et paiements à vérifier.
Les montants approuvés manuellement sont séparés par devise : EUR et USDT ne sont jamais additionnés.
Des pistes d’action fondées sur les chiffres sont affichées (description manquante, visiteurs sans
accès accordé sur la période, sorties supérieures aux entrées, attente de validation >24 h).
Ce sont des pistes à examiner, pas une démonstration de causalité ou une prévision commerciale.

L’historique d’événements commence lors de cette mise à jour ; les anciennes visites et adhésions
non enregistrées ne sont pas reconstituées. Le panneau distingue ces événements des totaux actuels.
Les visiteurs de fiche sont dédupliqués par utilisateur pour la période. Les entrées/sorties sont
celles reçues par le bot, pas un recensement rétroactif de Telegram. Les statistiques globales et
les diagnostics sont réservés aux responsables OWNER_IDS.

### Contrôles de fonctionnement

Administration → **État du réseau** affiche :

- connexion DB et dernière réception Telegram réussie ;
- dernier cycle terminé, tâches en erreur et dernière activité du traitement média ;
- files d’actions/médias en attente et en reprise ;
- accès expirés restant à révoquer, éventuels groupes classiques ouverts simultanément ;
- dernière vérification du planning, dernière modification d’annonce et erreurs de chaque groupe.

**Vérifier les groupes** demande un contrôle réel des droits, du nombre de membres et des
permissions d’envoi (lorsque Telegram les retourne). Les contrôles sont effectués progressivement,
un groupe par cycle, pour limiter les appels. Le bouton Actualiser recharge les résultats.
Les contrôles ne modifient pas les droits ni les accès ; ils signalent les anomalies. Un résultat
ancien conserve sa date, et une donnée jamais observée apparaît « jamais », jamais comme un succès.
Un heartbeat récent ne garantit pas que toutes les opérations Telegram ont réussi.

Les tâches de planification, parrainage, annonces, expiration, publicité et diagnostics sont isolées :
l’échec d’une tâche n’empêche plus les suivantes de s’exécuter. La file d’actions dispose d’un budget
par cycle ; la vérification des invitations utilise un curseur afin qu’un invité en erreur ne bloque
pas tous les autres. Les erreurs enregistrées masquent les URL et tokens.

### Compte à rebours

Le message affiche maintenant la date/heure prévue et le fuseau configuré, ainsi qu’un compte à
rebours précis en heures et minutes. Il est recalculé lors des cycles (~20 secondes hors latence
Telegram) et édité seulement lorsque son contenu change. Toutes les cinq minutes au maximum,
une édition de contrôle vérifie aussi qu’un message inchangé existe encore.
Le planning persistant est recalculé à partir de l’heure actuelle après redémarrage.
Les tests simulent notamment 00:44 → 09:05, les ouvertures/fermetures et les erreurs de parrainage.
La cause exacte d’une annonce figée en production nécessite les résultats du panneau ou les logs ;
les seules captures ne prouvent pas si le service était arrêté, bloqué ou mal configuré.

### Portail et fiches

- Portail → **Image du portail** : envoyer une photo Telegram. Le texte et les boutons accompagnent
  la photo. L’ajout d’une image à un message texte existant et les remplacements de photo/texte
  utilisent le même message Telegram. Limite de saisie du texte avec image : 800 unités UTF-16
  pour réserver la place à l’annonce du groupe ouvert dans la légende Telegram.
- Chat classique / 24 h/24 / VIP : description désormais obligatoire avant activation. Les groupes
  déjà actifs restent actifs ; compléter leurs descriptions depuis leurs fiches administrateur.
- **Nom affiché** permet de remplacer un titre générique comme CHAT dans l’interface du bot ;
  cela ne renomme pas le groupe Telegram lui-même.
- Fiche membre : nom, type, état ouvert/fermé, description, progression sur douze segments fins,
  pourcentage, compteur et invitations restantes. La progression se recharge à la navigation ou
  avec Actualiser. Les caractères et polices gardent le rendu natif du client Telegram.

### Règle des liens et objectifs, conservée

Depuis la version 10, chaque membre choisit son objectif et reçoit un lien personnel par groupe classique ou 24 h/24. Tous ces liens conduisent au portail. Chaque invité ne compte que pour le premier objectif auquel son invitation a été attribuée ; aucun double crédit après départ et retour. Le bouton Régénérer mon lien révoque l’ancien lien du même objectif sans toucher aux crédits acquis.

### Migration et validation

La migration additive `003_metrics.sql` est embarquée dans le module Python comme les précédentes.
Elle ajoute une table d’événements et ses index. Les anciennes migrations gardent leurs empreintes.
Aucune remise à zéro des utilisateurs, invitations, droits ou paiements.

Résultats : **90 tests locaux réussis**, 6 tests réseau PostgreSQL non exécutés dans cet environnement.
Les trois scénarios SQL PostgreSQL 16.2 ont été rejoués avec la nouvelle table statistique.
Aucun accès à la base Railway ni test réel Telegram réalisé ici ; vérifier le panneau après déploiement.

## Version 7 — configuration complète et accès répété à l’accueil

### Pourquoi le coupe-file peut être absent

Un moyen de paiement est proposé uniquement si ses **coordonnées globales** et son **tarif dans
le groupe concerné** sont renseignés. Les coordonnées seules ou le tarif seul ne suffisent pas.
Le membre ne voit pas d’achat si son accès est déjà acquis ou si le groupe n’est pas payant.

- Administration → Moyens de paiement : enregistrer le destinataire/adresse.
- Administration → Groupe → Tarifs : enregistrer le prix de base pour le même moyen.

### Pourcentage de configuration

Administration → **Configuration**, également accessible depuis État du réseau, affiche le nombre
exact de critères remplis et leur pourcentage. Le calcul inclut les groupes enregistrés (y compris
ceux désactivés), le portail actif et au moins un groupe actif à rejoindre. Les contrôles sont adaptés
au rôle : description, image de portail, objectif, horaires, jour, seuil si locker activé, puis coordonnées
et tarifs de chacun des trois moyens de paiement demandés pour les groupes payants.
Une configuration partielle des moyens de paiement peut donc donner un score inférieur à 100 %
alors que le moyen entièrement configuré fonctionne. Le panneau l’explique et affiche les moyens
actuellement utilisables. Les boutons « compléter » ouvrent les réglages manquants.

**Ce pourcentage mesure la configuration, pas la disponibilité ni l’absence de bugs.**
Les vérifications techniques conservent leurs états et leurs dates séparément.
Les éléments manquants apparaissent immédiatement à l’ouverture du panneau, sans attendre
un contrôle Telegram. Les coordonnées et montants eux-mêmes ne sont pas exposés dans le bilan.

### /start utilisable plusieurs fois

Le menu natif Telegram propose « Ouvrir ou retrouver mon espace » (/start) dans les conversations
privées. `/start` et `/start@nom_du_bot` reviennent à l’accueil et annulent une saisie en attente.
La navigation conserve le même message du bot : il n’y a pas une nouvelle bulle à chaque appel,
et l’heure de création Telegram de l’ancien message peut rester visible.
L’accueil affiche désormais son heure d’actualisation dans son texte.

Le panneau technique distingue le dernier /start reçu, le dernier /start affiché et une éventuelle
erreur d’affichage. Une erreur Telegram pendant la recherche des droits admin d’un autre groupe
ne bloque plus l’accueil utilisateur. Les mises à jour reçues sont traitées avant la maintenance
planifiée de chaque cycle. Les durées réseau restent variables : cela n’est pas une garantie de
réponse instantanée ni la confirmation de la cause exacte d’un incident non observé sur Railway.

Validation : **102 tests locaux réussis**, 6 tests réseau non exécutés ici, contrôle de l’archive
et initialisation embarquée réussis. Pas de nouvelle migration SQL pour cette version.

## Version 8 — accueil administrateur, texte coupe-file et panneau accessible

### Administration directe

`/start` sans lien ciblé ouvre directement le panneau réseau pour OWNER_IDS.
Un administrateur Telegram d’un groupe connecté arrive sur un panneau limité aux groupes qu’il
administre. Les simples membres gardent leur espace personnel. Les droits sont vérifiés à chaque
opération ; ce changement d’accueil ne donne aucun nouveau droit.
Les panneaux administrateur comprennent **Inviter mes amis** et **Vue membre**.
Un lien ciblé vers une fiche ou une invitation conserve sa destination, même pour un administrateur.

### Description commune du coupe-file

Administration → **Texte coupe-file** : saisir le texte commun aux groupes classiques et 24 h/24,
maximum 1800 caractères. Il apparaît à l’ouverture du choix de moyen de paiement. Un texte explicatif
par défaut est disponible. Les descriptions propres aux groupes et VIP restent séparées.

### Règle d’invitation par boutons

Administration → Règle invitations affiche deux boutons, avec la règle active cochée :
- **Simple** : l’invité reste au moins cinq minutes dans le portail.
- **Cascade** : l’invité reste au moins cinq minutes et invite lui-même une personne restée cinq minutes.

Les explications sont réservées aux responsables du réseau ; elles ne sont pas exposées aux membres.
Les invitations déjà validées ne sont pas retirées lors d’un changement de règle.

### Message récent après /start et preuve

Cette version remplace volontairement le comportement des versions précédentes : `/start` envoie
un nouveau panneau au bas de la conversation, enregistre son identifiant puis retire l’ancien.
Les clics suivants continuent d’éditer ce panneau ; la navigation ne produit pas une pile de menus.
Si l’envoi échoue, le pointeur vers l’ancien panneau est conservé. Si Telegram refuse la suppression
de l’ancien message, le nouveau reste actif et une tâche tente de retirer les boutons de l’ancien.
L’ancienne bulle peut donc rester dans l’historique lorsque Telegram interdit sa suppression.

Après réception et sauvegarde d’une preuve, le panneau de suivi est lui aussi placé sous la capture.
La capture de l’utilisateur n’est pas supprimée. Les responsables reçoivent la preuve séparément
pour validation ; un échec de déplacement du panneau ne supprime ni la demande ni sa preuve.

### Diagnostics et rotation

« Description manquante » désigne réellement un champ vide dans la configuration du groupe.
Compléter la description, puis relancer les contrôles si l’ancien rapport est encore affiché.
La phrase générique « Historique des membres incomplet si le bot était absent » n’est plus
présentée dans le rapport de chaque groupe : une note informative globale précise que seuls les
événements reçus par le bot alimentent l’historique. Elle ne prouve pas une panne ou une absence.

La rotation n’est pas aléatoire : le groupe le moins récemment ouvert est privilégié, en excluant
le groupe planifié la veille lorsqu’un autre groupe existe. Les journées déjà planifiées restent
persistantes et les groupes gratuits/24 h/24 suivent leurs propres règles.

Le bannissement selon l’alphabet arabe ou cyrillique du nom n’est pas implémenté.

Validation : **112 tests locaux réussis**, 6 tests réseau PostgreSQL non exécutés ici.
Pas de nouvelle migration SQL. Les tests couvrent aussi l’échec d’envoi du nouveau panneau,
le refus de suppression de l’ancien et la conservation des preuves.


## Version 9 — annonces par paliers, accueil et langues

Cette section remplace les indications antérieures sur le décompte précis en minutes.

- Les annonces de statut des groupes sont supprimées puis republiées au changement de palier, jamais éditées. Une annonce identique reste en place, y compris après redémarrage. Si la suppression échoue, aucune nouvelle annonce n’est envoyée. Si l’envoi échoue après suppression, le cycle suivant réessaie.
- Ouverture : heures, puis 30, 10, 5, 4, 3, 2 et 1 minute. Fermeture : 30, 15, 5, 4, 3, 2 et 1 minute. Gratuit hebdomadaire : jours, heures, puis 30, 15, 10, 5, 4, 3, 2 et 1 minute. Les heures lointaines sont approximatives ; la date et l’heure prévues restent affichées. Le cycle vérifie les paliers toutes les 20 secondes environ, selon la réponse Telegram ; aucun rattrapage des annonces passées n’est publié.
- `/start` publie un panneau récent et retire le précédent. Les boutons actualisent le panneau courant ; le passage image/texte recrée le panneau et retire l’ancien. Une suppression privée impossible désactive les boutons de l’ancien panneau et apparaît dans les diagnostics.
- Administration → **Accueil et traductions** : texte d’accueil par langue (500 caractères UTF-16), image commune facultative, description de chaque groupe par langue et texte coupe-file commun par langue. Le bouton Texte par défaut rétablit le contenu par défaut. **Retirer l’image** rétablit l’accueil texte.
- **Langue** : Français, English, Italiano, Español. Le choix individuel est conservé en base, y compris après redémarrage. Les écrans membres, invitations, étapes de paiement, erreurs de preuve et notifications de paiement utilisent cette langue. Les titres et descriptions personnalisés ne sont pas traduits automatiquement ; une description sans version locale reprend celle du groupe. L’accueil et le coupe-file utilisent leur texte traduit par défaut si aucun texte local n’a été saisi. L’administration et les annonces publiques restent en français. Les pages externes suivent leurs propres réglages linguistiques.
- Les droits d’accès et liens restent identiques quand la langue change. Aucune nouvelle migration de schéma : les réglages sont stockés dans la table existante `settings`.
- Aucun bannissement fondé sur l’alphabet du nom ou du pseudonyme n’est ajouté.

Tests : paliers et absence de répétition, ordre suppression/envoi, reprise après échec, autorisation des réglages, accueil illustré et transitions texte/photo, persistance des langues, navigation et paiements dans chaque langue. Les tests Telegram utilisent un simulateur ; ils ne certifient pas un déploiement réel.


## Version 10 — objectifs distincts, signalements et annonces obligatoires

### Mise à jour et conservation des accès

Remplacer les fichiers de l’application puis redéployer avec la même `DATABASE_URL`.
La migration embarquée `004_member_workflows.sql` ajoute cinq tables et des index ; elle ne supprime aucune table existante et les migrations précédentes restent inchangées.
Au premier démarrage, les accès existants restent conservés. Les objectifs déjà atteints sous l’ancien compteur commun, sans accès encore enregistré, deviennent des accès acquis. Les anciens compteurs partiels et invitations sans cible ne sont pas attribués arbitrairement à un groupe. Les compteurs des nouveaux objectifs démarrent à zéro. Les anciens liens de parrainage sans cible sont désactivés et leur révocation Telegram est mise en file ; les membres prennent leurs nouveaux liens depuis **Inviter mes amis**. Cette transition est enregistrée une seule fois.

### Invitations par objectif

**Inviter mes amis → choisir un groupe** affiche son lien personnel, le bouton de partage et **Régénérer mon lien** avec confirmation. Depuis une fiche de groupe, le bouton mène directement à cet objectif. Chaque lien mène au même portail ; une table conserve le groupe cible. Le premier rattachement d’un invité est conservé et empêche les doubles crédits après un départ/retour ou un changement de lien. Les règles internes simple/cascade restent identiques, sans être exposées aux membres. Les droits d’accès restent associés à l’identifiant logique du groupe lors d’un remplacement Telegram.

### Signalements et +5 crédits

**Signaler un bug · +5 → objectif → description**. L’admin reçoit une notification et retrouve les dossiers dans **Administration → Signalements**. Il choisit **Bug réel · attribuer +5** ou **Refuser**. Une décision est définitive pour ce dossier et transactionnelle : double clic ou nouvelle tentative ne donne pas une deuxième récompense. Un bug validé rapporte cinq crédits au seul objectif choisi. Les crédits de bugs sont séparés des statistiques d’invitations réelles ; ils sont inclus dans le compteur de débannissement. Le membre consulte ses vingt derniers dossiers dans **Mes signalements**. Les descriptions identiques du même membre sont refusées ; plafond de trois signalements sur 24 heures et texte de 20 à 2000 caractères. Les doublons signalés par différentes personnes restent à apprécier par l’admin. La sélection de récompense concerne les groupes classiques et 24 h/24 disposant d’un objectif d’invitations.

### Annonce obligatoire lors d’une activation

La première activation d’un nouveau groupe non-portail enregistre une annonce. À la prochaine interaction, le membre voit son nom, sa description et ses conditions d’accès, avec les moyens de paiement configurés et leurs montants majorés si applicables. Il doit cliquer **J’ai compris** avant de naviguer ou payer. Plusieurs annonces sont présentées successivement ; les confirmations sont persistantes et personnelles. Les groupes désactivés sont ignorés. Les groupes déjà actifs lors de la migration ne sont pas annoncés comme nouveaux. Une désactivation/réactivation du même groupe ne recommence pas la campagne. Le propriétaire conserve son accès à l’administration.

### Réglages par boutons

**Mots interdits** montre une liste paginée avec **Ajouter**, puis sélection et confirmation pour **Supprimer**. Les termes non sélectionnés sont conservés. Les paiements offrent **Désactiver ce moyen** et **Retirer ce tarif** ; les textes localisés offrent **Texte par défaut**. Le tiret n’est plus une commande de suppression. L’accueil, les annonces, les invitations et les signalements des membres disposent des traductions françaises, anglaises, italiennes et espagnoles. Les textes personnalisés restent configurables par langue.

### Coupures Telegram

Les erreurs réseau sont présentées comme des connexions interrompues, jamais comme une preuve de disparition du groupe. Les opérations idempotentes (lecture, édition, permissions, suppression, révocation) disposent de trois tentatives avec attente progressive. Les envois et créations de liens ne sont pas répétés aveuglément dans la couche réseau : Telegram peut avoir accepté la requête avant la perte de sa réponse. Les tâches planifiées et leur file conservent leur reprise habituelle. Une réponse perdue après un envoi reste un résultat ambigu ; l’API Telegram ne fournit pas de clé permettant de garantir un envoi exactement une fois.

### Validation

Les tests couvrent l’attribution par groupe, la validation cascade, les retours sans double crédit, la régénération et son échec, les décisions de bugs idempotentes, les autorisations, les annonces bloquantes, leur persistance, les suppressions ciblées et les coupures réseau. Les requêtes de schéma et de transition sont aussi vérifiées sur PostgreSQL 16.2 en mode local sans serveur réseau. Le bot Telegram en production n’est pas accessible dans cet environnement ; les appels Telegram des tests sont simulés.


## Version 12 — traitement rapide, anti-liens et portail animé

### Déploiement

Redéployer avec les mêmes `BOT_TOKEN`, `OWNER_IDS` et `DATABASE_URL`, en conservant **un seul replica Railway**. La migration embarquée `005_fast_dispatch.sql` ajoute la boîte de réception persistante et des index, sans supprimer les tables ou accès existants. Aucune modification des variables n’est nécessaire. Chaque worker possède sa propre connexion à la base ; prévoir environ onze connexions PostgreSQL pour le processus complet (polling, quatre workers privés, deux de modération, arrivées/départs, tâches planifiées, file d’actions et médias). Le verrou de polling reste détenu par le processus principal.

### Réactivité et reprise

- Le polling stocke les mises à jour avant d’avancer son offset, dans une seule transaction. Les identifiants empêchent leur réinsertion en double. Les événements terminés sont conservés sept jours ; les échecs restent visibles pour diagnostic.
- Les boutons de chaque utilisateur conservent leur ordre, dans quatre files privées distinctes. Deux files de modération maintiennent l’ordre par groupe. Les messages d’arrivée/départ ont leur propre file et sont supprimés directement, sans attendre le planificateur.
- Les accusés `answerCallbackQuery` partent dans un pool séparé, avec un délai réseau de deux secondes. Un accusé expiré ou interrompu ne bloque plus l’action du bouton et ne génère plus une notification répétitive aux propriétaires. Les erreurs sont comptabilisées dans État du réseau.
- Les actions interactives utilisent un délai réseau de quatre secondes par appel et une seule tentative dans la couche transport. Les opérations automatiques utilisent six secondes. Les reprises d’événements sont bornées et les jobs échoués restent en file. Une panne réseau n’est pas supprimable par le code et une réponse perdue après un envoi peut laisser une livraison ambiguë.
- Les listes d’administrateurs sont mises en cache trente secondes. Les mutations de configuration recontrôlent le statut individuel en direct. Les événements de changement de statut invalident le cache. Les vérifications de droits périodiques sont réparties, une par cycle, plutôt que toutes au même moment.
- **État du réseau** indique les événements en attente/échec et les derniers délais par file. Ces chiffres permettent de mesurer les performances réelles sur Railway. Le test de concurrence simule un planificateur bloqué et vérifie que navigation et suppression des arrivées progressent malgré cela ; il ne mesure pas le réseau Telegram réel.

### Anti-liens

L’anti-liens est actif pour les membres dans tous les groupes connectés et activés, portail compris. Il examine textes, légendes, entités URL, liens cachés et boutons contenant un lien. Les liens ordinaires `http(s)`, `tg://`, `t.me`, `www` et domaines sont reconnus ; les simples mentions et adresses email ne sont pas assimilées à des liens. Certaines écritures volontairement déguisées peuvent échapper à une détection textuelle ; le bot n’analyse pas les QR codes ni le texte à l’intérieur des images.

Le message est supprimé immédiatement. Le membre est marqué banni et exclu du réseau, portail compris, avec propagation par la file prioritaire. Ce statut empêche aussi l’accès aux groupes futurs. Le mécanisme existant de demande de débannissement reste disponible dans le bot ; ce bannissement n’est pas un bannissement permanent sans recours. Les publications du bot et des administrateurs sont exemptées. Un message publié au nom d’un canal externe entraîne une exclusion du canal émetteur, sans inventer l’identité d’une personne derrière celui-ci.

### Publicités du portail

La fiche du groupe portail affiche maintenant **Publicités**. Le parcours guidé est conservé : fréquence 2/4/6/12 heures → texte ou photo légendée → aperçu → confirmation. Plusieurs campagnes peuvent être enregistrées et tourner en continu dans le portail, même si aucun chat n’est ouvert. Il n’y a qu’une publicité de campagne visible : l’ancienne est supprimée et la suppression confirmée avant l’envoi de la suivante. Une erreur de suppression empêche la nouvelle diffusion afin d’éviter un doublon. La mise en pause d’une campagne retire aussi son message au prochain cycle.

### Annonces temporaires d’accès

En plus de l’accueil et de la campagne publicitaire, le portail affiche une annonce temporaire par groupe classique, gratuit ou 24 h/24 ouvert : **🟢 Groupe X est ouvert**. Elle est retirée quand le groupe ferme ou n’est plus disponible.

Pendant le créneau prévu, si le locker bloque l’ouverture, l’annonce reste présente avec **🟠 Groupe X attend N membres pour ouvrir**, et invite à rejoindre/partager. Elle ne prétend pas que les messages sont autorisés tant que le groupe reste verrouillé.

Pour les groupes classiques et 24 h/24, le bouton ouvre la fiche privée du bot : un membre ayant déjà rempli l’objectif ou payé voit son lien personnel, les autres voient l’objectif et le partage vers le portail. Un bouton public identique pour tous ne contient jamais le lien privé d’un membre. Pour le gratuit, le bouton mène directement à l’invitation du groupe ; une demande d’admission est automatiquement vérifiée pour empêcher un membre banni de contourner le contrôle.

Le cycle de publication reste d’environ vingt secondes, selon les réponses de Telegram. L’annonce de statut supprimée manuellement est vérifiée environ chaque minute et restaurée ; la cadence des décomptes reste celle des paliers. Les drapeaux, le vocabulaire « invitations », les jours sélectionnables et les consignes simplifiées restent présents.

Référence technique : [Telegram Bot API](https://core.telegram.org/bots/api), notamment `answerCallbackQuery`, `MessageEntity`, `getChatAdministrators` et `createChatInviteLink`.


## Version 13 — annonces vertes, règles anticipées et erreurs classées

Cette section remplace les indications précédentes concernant les annonces orange du locker et la répétition des règles.

- Dans le portail, pendant le créneau prévu, l’annonce est **🟢 Groupe X est ouvert ! Il fermera dans …**, même si le locker empêche encore l’ouverture réelle. Le locker continue de contrôler les permissions du groupe. Le décompte avance par tranches de trente minutes (2 h, 1 h 30, 1 h, 30 min). À chaque changement, la précédente annonce est supprimée avant publication de la nouvelle. À la fin du créneau, elle est retirée. Les groupes 24/24 affichent leur disponibilité continue, sans inventer d’heure de fermeture.
- Les règles des groupes classiques et gratuits sont affichées au premier cycle situé entre cinq minutes avant le début du créneau et sa fin. Elles restent présentes jusqu’à la fin, sans empilement. Si le bot redémarre pendant cette période, il reprend le message existant ou le recrée s’il manque. Si le locker n’a jamais laissé ouvrir le groupe, les règles sont tout de même retirées à la fin prévue. Un groupe sans créneau conserve ses règles en continu tant qu’il est activé. La désactivation retire les règles au prochain cycle.
- **Configuration / sanity check** comporte désormais le critère **Règles du groupe**, avec accès au bouton de configuration. Le diagnostic technique signale aussi leur absence. Aucun texte de règles n’est inventé.
- Le motif `opening:%` est envoyé comme paramètre SQL. L’adaptateur PostgreSQL échappe également les pourcentages littéraux des requêtes paramétrées et n’envoie pas de tableau de paramètres vide aux requêtes simples. Les migrations déjà appliquées ne sont pas modifiées.
- Une coupure réseau isolée est enregistrée dans les diagnostics sans notification immédiate répétitive. Trois échecs rapprochés du même traitement déclenchent une alerte d’incident persistant, avec la déduplication habituelle. Une réussite réinitialise le compteur du polling, des publications du portail et des actions concernées. Le polling applique une reprise progressive de 1 à 30 secondes.
- Une erreur nouvelle non temporaire reste signalée immédiatement. Le panneau État du réseau affiche les cinq incidents récents encore enregistrés ; les logs gardent les détails techniques. Il ne s’agit pas de masquer les problèmes.
- Si Telegram répond que le destinataire a bloqué le bot ou désactivé son compte, les envois privés vers lui sont arrêtés, les tâches concernées sont retirées de la file et aucune alerte de panne réseau n’est envoyée. Un nouveau message privé de cet utilisateur réactive la possibilité de lui répondre. Cela ne modifie pas ses achats ni ses droits d’accès.
- Si le nettoyage d’un ancien panneau échoue, une véritable suppression est désormais remise en file, en plus du retrait de ses boutons. Une édition d’un message déjà supprimé est considérée terminée.
- L’anti-liens et le bannissement réseau de la version 12 restent actifs. Les tests des URL, légendes, liens cachés, administrateurs exemptés et groupes futurs font partie des vérifications de cette version.

Validation : tests applicatifs avec Telegram simulé, tests de la syntaxe des paramètres avec psycopg, et vérification SQL locale PostgreSQL 16.2. Les coupures de Telegram, les indisponibilités du réseau et les pertes de réponse après envoi restent possibles ; aucune promesse de zéro erreur en production n’est faite. Une livraison dont la réponse a été perdue peut rester ambiguë.
