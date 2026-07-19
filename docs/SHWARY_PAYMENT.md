# Paiement réseau avec Shwary

Le logiciel prépare l'intégration Shwary pour demander un paiement Mobile Money
depuis le bot ou une IA connectée.

## Variables à configurer

Avant de lancer Flask en production :

```powershell
$env:SHWARY_MERCHANT_ID = "votre-merchant-id"
$env:SHWARY_MERCHANT_KEY = "votre-merchant-key"
$env:SHWARY_COUNTRY_CODE = "DRC"
$env:SHWARY_CALLBACK_URL = "https://votre-domaine.com/webhooks/shwary"
```

`SHWARY_CALLBACK_URL` doit être une URL HTTPS publique accessible par Shwary.

## Endpoints ajoutés

| Méthode | Endpoint | Rôle |
| --- | --- | --- |
| POST | `/api/ai/reservations/<reference>/payments/shwary` | Lancer une demande de paiement Shwary |
| GET | `/api/ai/payments/shwary/<reference_id>` | Lire le statut local de la demande |
| POST | `/webhooks/shwary` | Recevoir le callback Shwary |
| GET | `/api/ai/reservations/<reference>/ticket.pdf` | Télécharger le billet PDF côté bot/IA |
| GET | `/tickets/<token>.pdf` | Lien PDF sécurisé pour le client |

## Parcours prévu

1. Le bot crée la réservation.
2. Le bot lance la demande Shwary.
3. Le client reçoit la demande de paiement sur son téléphone.
4. Shwary renvoie le statut sur `/webhooks/shwary`.
5. Si le statut est réussi, le logiciel marque la réservation `PAYE`.
6. Le bot peut envoyer le billet PDF au client.

Le statut `CONFIRME` reste une validation métier séparée si l'agence veut encore
contrôler les billets avant embarquement.

## Exemple : lancer le paiement

```http
POST /api/ai/reservations/BT-20260719-00012/payments/shwary
Authorization: Bearer <AI_API_TOKEN>
Content-Type: application/json

{
  "phone": "+243900000000"
}
```

Réponse :

```json
{
  "message": "Demande de paiement Shwary envoyee.",
  "payment_request": {
    "provider": "SHWARY",
    "reference_id": "BT-20260719-00012-a1b2c3d4",
    "status": "PENDING"
  }
}
```

## Exemple : récupérer le PDF

```http
GET /api/ai/reservations/BT-20260719-00012/ticket.pdf
Authorization: Bearer <AI_API_TOKEN>
```

