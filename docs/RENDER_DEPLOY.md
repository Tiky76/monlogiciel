# Déploiement sur Render

Ce projet est prêt pour Render avec le fichier `render.yaml`.

## Pourquoi un disque persistant ?

La billetterie utilise SQLite. Sur Render, la base doit être stockée sur un
disque persistant, sinon les données peuvent être perdues après un redéploiement.

Le fichier `render.yaml` configure :

```text
TICKETS_DB_PATH=/var/data/tickets.db
```

et un disque :

```text
mountPath: /var/data
```

## Déployer

1. Ouvrir Render.
2. Choisir `New +`.
3. Choisir `Blueprint`.
4. Connecter le dépôt GitHub `Tiky76/monlogiciel`.
5. Render lit automatiquement `render.yaml`.
6. Remplir les variables marquées comme secrètes.
7. Lancer le déploiement.

## Variables importantes

À remplir dans Render :

```text
ADMIN_PASSWORD
AI_API_TOKEN
WHATSAPP_VERIFY_TOKEN
WHATSAPP_ACCESS_TOKEN
WHATSAPP_PHONE_NUMBER_ID
SHWARY_MERCHANT_ID
SHWARY_MERCHANT_KEY
SHWARY_CALLBACK_URL
```

`ADMIN_USERNAME` vaut par défaut :

```text
administrateur
```

Le premier administrateur est créé automatiquement au démarrage si aucun
administrateur actif n'existe encore.

## Après le déploiement

Render donnera une URL du type :

```text
https://monlogiciel.onrender.com
```

À utiliser ensuite :

```text
Webhook WhatsApp : https://monlogiciel.onrender.com/webhooks/whatsapp
Callback Shwary  : https://monlogiciel.onrender.com/webhooks/shwary
PDF billet       : https://monlogiciel.onrender.com/tickets/<token>.pdf
```

Pour l'APK scanner, il faudra reconstruire l'APK avec :

```text
https://monlogiciel.onrender.com/scanner
```

Remplacez cette URL par le vrai domaine Render obtenu.

