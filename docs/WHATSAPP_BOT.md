# Bot WhatsApp de réservation

Le logiciel contient une première base de bot WhatsApp via le webhook :

```text
/webhooks/whatsapp
```

## Ce que le bot sait déjà faire

1. Recevoir un message WhatsApp envoyé par un client.
2. Demander la ville de départ.
3. Demander la destination.
4. Afficher les trajets disponibles.
5. Demander le nom complet du passager.
6. Créer une réservation avec attribution automatique du premier siège libre.
7. Lancer une demande de paiement Shwary si les clés sont configurées.
8. Répondre avec le numéro de billet, le trajet, le siège, le prix, le statut et le lien PDF.

Le paiement reste à confirmer dans le logiciel web par l'administrateur ou l'agent autorisé.

## Ton des réponses

Le bot doit répondre naturellement, comme un agent de billetterie qui parle à un
client sur WhatsApp.

Principes :

- rester poli et rassurant ;
- utiliser des phrases courtes ;
- guider le client étape par étape ;
- éviter les réponses trop robotiques ;
- ne jamais inventer un trajet, un prix, un siège ou un statut ;
- confirmer clairement la réservation avant de demander le paiement.

Exemple :

```text
Bonjour 👋 Bienvenue à la billetterie.
Je peux vous aider à réserver un billet.

Dites-moi d'abord votre ville de départ :
- Likasi
- Lubumbashi
- Kolwezi
- Kasumbalesa
```

## Variables à configurer

Avant de lancer Flask en production, définir :

```powershell
$env:WHATSAPP_VERIFY_TOKEN = "un-code-secret-a-choisir"
$env:WHATSAPP_ACCESS_TOKEN = "token-meta-whatsapp"
$env:WHATSAPP_PHONE_NUMBER_ID = "identifiant-du-numero-whatsapp"
$env:WHATSAPP_GRAPH_VERSION = "v23.0"
```

`WHATSAPP_VERIFY_TOKEN` est le code secret que vous saisissez aussi dans Meta
quand vous configurez le webhook.

Si `WHATSAPP_ACCESS_TOKEN` ou `WHATSAPP_PHONE_NUMBER_ID` manque, le webhook
reçoit et traite les messages, mais il ne peut pas encore répondre réellement
sur WhatsApp. C'est pratique pour tester localement.

## Configuration côté Meta

Dans Meta for Developers / WhatsApp Cloud API :

1. Créer ou ouvrir l'application Meta Business.
2. Ajouter le produit WhatsApp.
3. Récupérer le `Phone Number ID`.
4. Créer un token d'accès.
5. Configurer le webhook avec l'URL publique :

```text
https://votre-domaine.com/webhooks/whatsapp
```

6. Utiliser le même `Verify token` que `WHATSAPP_VERIFY_TOKEN`.
7. S'abonner aux événements de messages.

Pour un test local, il faut exposer Flask avec une URL HTTPS publique, par
exemple avec un tunnel de développement.

## Test manuel sans Meta

La route POST accepte le format webhook WhatsApp. Exemple de corps JSON :

```json
{
  "entry": [
    {
      "changes": [
        {
          "value": {
            "messages": [
              {
                "from": "243900000000",
                "type": "text",
                "text": {
                  "body": "bonjour"
                }
              }
            ]
          }
        }
      ]
    }
  ]
}
```

Réponse attendue : le bot renvoie un message de bienvenue et la liste des villes.

## Parcours client

```text
Client : Bonjour
Bot    : Choisissez votre ville de départ
Client : Likasi
Bot    : Choisissez la destination
Client : Lubumbashi
Bot    : Affiche les trajets disponibles
Client : 1
Bot    : Demande le nom complet
Client : Jean Mukendi
Bot    : Crée la réservation et donne le numéro du billet
```
