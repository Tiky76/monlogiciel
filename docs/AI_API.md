# API pour IA

Cette API permet de connecter une IA externe à la billetterie sans utiliser la
session web classique.

Base locale :

```text
http://127.0.0.1:5000
```

## Sécurité

Définir un jeton secret avant de lancer Flask :

```powershell
$env:AI_API_TOKEN = "un-token-long-et-secret"
```

Chaque appel doit envoyer :

```http
Authorization: Bearer un-token-long-et-secret
```

Il est aussi possible d'utiliser :

```http
X-AI-Token: un-token-long-et-secret
```

Ne pas mettre ce token dans le code public ni dans GitHub.

## Rôle de cette API

L'IA peut :

- lire le contexte métier de la billetterie ;
- connaître les villes autorisées ;
- rechercher les trajets disponibles ;
- créer une réservation avec siège automatique ;
- relire une réservation ;
- lancer une demande de paiement Shwary ;
- récupérer le billet au format PDF.

L'IA ne peut pas :

- créer des comptes ;
- changer les paramètres ;
- confirmer les paiements ;
- annuler des billets ;
- modifier les tarifs.

Ces actions restent dans le logiciel web et sont réservées aux agents autorisés.

## Style de réponse attendu

Une IA connectée à cette API doit répondre naturellement, comme un agent de
réservation humain.

Consignes :

- parler en français simple ;
- rester poli, calme et professionnel ;
- poser une question à la fois ;
- reformuler les choix importants du client ;
- ne pas inventer de prix, trajet, siège ou statut ;
- utiliser les données retournées par l'API comme seule source de vérité.

Exemple :

```text
Bonjour 👋 Je peux vous aider à réserver un billet.
Vous partez de quelle ville ?
```

## Endpoints

| Méthode | Endpoint | Rôle |
| --- | --- | --- |
| GET | `/api/ai/capabilities` | Lire les capacités de l'API IA |
| GET | `/api/ai/context` | Lire le contexte métier |
| POST | `/api/ai/trips/search` | Rechercher des trajets |
| POST | `/api/ai/reservations` | Créer une réservation |
| GET | `/api/ai/reservations/<reference>` | Lire une réservation |
| GET | `/api/ai/reservations/<reference>/ticket.pdf` | Télécharger le billet PDF |
| POST | `/api/ai/reservations/<reference>/payments/shwary` | Lancer un paiement Shwary |
| GET | `/api/ai/payments/shwary/<reference_id>` | Lire le statut local Shwary |

## Exemple : lire le contexte

```http
GET /api/ai/context
Authorization: Bearer <AI_API_TOKEN>
```

Réponse :

```json
{
  "agency": {
    "name": "Billetterie",
    "currency": "FC"
  },
  "cities": ["Likasi", "Lubumbashi", "Kolwezi", "Kasumbalesa"],
  "business_rules": [
    "Le siège est attribué automatiquement par le système."
  ]
}
```

## Exemple : rechercher un trajet

```http
POST /api/ai/trips/search
Authorization: Bearer <AI_API_TOKEN>
Content-Type: application/json

{
  "origin": "Likasi",
  "destination": "Lubumbashi",
  "date": "2026-07-20"
}
```

La date est optionnelle. Si elle est absente, l'API retourne les prochains
trajets disponibles pour cette liaison.

## Exemple : créer une réservation

```http
POST /api/ai/reservations
Authorization: Bearer <AI_API_TOKEN>
Content-Type: application/json

{
  "trip_id": 3,
  "customer_name": "Jean Mukendi",
  "customer_phone": "+243900000000"
}
```

Réponse :

```json
{
  "message": "Réservation créée par l'IA.",
  "reservation": {
    "ticket_number": "BT-20260718-00012",
    "customer_name": "Jean Mukendi",
    "seat_number": 1,
    "amount": 15000,
    "status": "EN_ATTENTE"
  },
  "next_step": "Faire payer le client puis confirmer le billet dans le logiciel."
}
```

## Exemple : lire une réservation

```http
GET /api/ai/reservations/BT-20260718-00012
Authorization: Bearer <AI_API_TOKEN>
```

La référence peut être l'identifiant numérique de la réservation ou le numéro de
billet.
