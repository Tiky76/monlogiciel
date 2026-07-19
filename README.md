# Gestion de billets de transport

Application Flask simple, reposant sur SQLite, pour créer des trajets, réserver des sièges et enregistrer les paiements.

## Lancer l'application

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
flask --app app init-db
flask --app app create-admin administrateur
$env:SECRET_KEY = "une-cle-secrete-longue-et-aleatoire"
flask --app app run --debug
```

Ouvrez ensuite `http://127.0.0.1:5000`.

## Administration et sécurité

Les comptes sont créés exclusivement par un administrateur. La commande `create-admin` crée le premier compte directement dans SQLite et demande le mot de passe sans l'afficher. Les mots de passe sont stockés sous forme hachée.

En exploitation, définissez toujours `SECRET_KEY` dans l'environnement avant de lancer Flask. L'application protège les formulaires contre les requêtes CSRF, verrouille temporairement les connexions après cinq échecs et réserve les paramètres, rapports et comptes aux administrateurs.

## Sauvegarde et restauration

Un administrateur peut télécharger une sauvegarde cohérente depuis **Paramètres → Télécharger une sauvegarde**. Pour restaurer une sauvegarde :

1. arrêtez l'application ;
2. conservez une copie du fichier `tickets.db` actuel ;
3. remplacez `tickets.db` par le fichier de sauvegarde ;
4. relancez l'application et contrôlez la connexion et les dernières réservations.

Ne remplacez jamais la base pendant que Flask est en cours d'exécution.

## API pour bot

L'application expose une API JSON sous `/api`. La documentation complete pour le futur bot se trouve dans `docs/API_BOT.md`.

Routes utiles :

- `GET /api/health` : verifier que l'API repond ;
- `GET /api/docs` : lire une documentation JSON courte ;
- `GET /api/trips` : lister les trajets ;
- `POST /api/reservations` : creer une reservation avec siege automatique ;
- `POST /api/reservations/<id>/payment` : enregistrer un paiement ;
- `POST /api/reservations/<id>/status` : confirmer ou utiliser un billet ;
- `GET /api/verify/<token>` : verifier un QR Code ;
- `POST /api/verify/<token>/use` : valider l'embarquement.

## Bot WhatsApp

Une première base de bot WhatsApp est disponible avec le webhook `/webhooks/whatsapp`.
Elle permet de guider un client jusqu'à la création d'une réservation avec siège
automatique. Les instructions de connexion à WhatsApp Cloud API sont dans
`docs/WHATSAPP_BOT.md`.

## API pour IA

Une API dédiée aux assistants IA est disponible sous `/api/ai`. Elle utilise un
jeton `AI_API_TOKEN` et permet de lire le contexte métier, rechercher des trajets
et créer des réservations avec siège automatique. La documentation est dans
`docs/AI_API.md`.

## Paiement Shwary et billet PDF

Le projet prépare une intégration Shwary pour demander un paiement réseau/mobile
money depuis le bot, recevoir le callback de paiement et générer un billet PDF.
Les instructions sont dans `docs/SHWARY_PAYMENT.md`.

## Déploiement Render

Le projet contient `render.yaml` pour déployer le logiciel sur Render avec
Gunicorn et une base SQLite placée sur disque persistant. Le guide se trouve
dans `docs/RENDER_DEPLOY.md`.

## Scanner QR mobile

Le logiciel web principal ne montre pas le scanner QR dans son menu. Le controle des billets doit rester dans une application de scannage separee.

Un projet Android de base est disponible dans `android-scanner/` pour produire une APK de controle. Les instructions sont dans `docs/APK_SCANNER.md`.

Un workflow GitHub Actions est egalement disponible pour generer l'APK depuis GitHub: `.github/workflows/build-android-apk.yml`.
