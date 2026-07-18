# API pour le futur bot

Cette API permet a un bot externe de consulter les trajets, creer une reservation,
enregistrer un paiement, confirmer un billet et verifier un QR Code.

Base locale de developpement:

```text
http://127.0.0.1:5000
```

## Etat global

L'API repond aux besoins principaux du futur bot:

- consulter les trajets disponibles;
- creer une reservation sans choisir le siege;
- obtenir le numero de billet, le prix et l'URL de verification;
- enregistrer un paiement;
- confirmer le billet apres paiement;
- verifier un billet depuis le token QR;
- marquer le billet comme utilise a l'embarquement.

Les operations de gestion sensibles restent reservees a l'administrateur.

## Authentification et CSRF

L'API utilise la session Flask. Pour les requetes `POST`, le bot doit envoyer le
jeton CSRF dans l'en-tete `X-CSRF-Token`.

Parcours recommande:

1. `GET /api/csrf`
2. `POST /api/login` avec le jeton recu
3. Conserver le cookie de session
4. Utiliser le nouveau `csrf_token` retourne par `/api/login` pour les autres `POST`

Exemple de connexion:

```http
POST /api/login
X-CSRF-Token: <csrf_token>
Content-Type: application/json

{
  "username": "agent",
  "password": "mot-de-passe"
}
```

Reponse:

```json
{
  "message": "Connexion reussie.",
  "csrf_token": "<nouveau_csrf_token>",
  "user": {
    "id": 1,
    "username": "agent",
    "is_admin": false
  }
}
```

## Endpoints disponibles

| Methode | Endpoint | Auth | Role |
| --- | --- | --- | --- |
| GET | `/api/health` | Non | Verifier que l'API repond |
| GET | `/api/docs` | Non | Lire la documentation JSON courte |
| GET | `/api/csrf` | Non | Obtenir un jeton CSRF |
| POST | `/api/login` | Non | Ouvrir une session |
| POST | `/api/logout` | Oui | Fermer la session |
| GET | `/api` | Oui | Lister les routes principales |
| GET | `/api/trips` | Oui | Lister les trajets |
| GET | `/api/trips/<id>` | Oui | Lire un trajet et ses reservations |
| GET | `/api/reservations` | Oui | Rechercher les reservations |
| POST | `/api/reservations` | Oui | Creer une reservation |
| GET | `/api/reservations/<id>` | Oui | Lire une reservation |
| POST | `/api/reservations/<id>/payment` | Oui | Enregistrer un paiement |
| POST | `/api/reservations/<id>/status` | Oui | Changer le statut autorise |
| GET | `/api/verify/<token>` | Oui | Verifier un QR Code |
| POST | `/api/verify/<token>/use` | Oui | Marquer le billet comme utilise |
| GET | `/api/payments` | Oui | Lister les paiements recents |
| GET | `/api/settings` | Admin | Lire parametres, villes et tarifs |

## Creer une reservation

Le bot ne choisit pas le siege. Le systeme attribue automatiquement le premier
siege disponible.

```http
POST /api/reservations
X-CSRF-Token: <csrf_token>
Content-Type: application/json

{
  "trip_id": 3,
  "customer_name": "Jean Kalala",
  "customer_phone": "+243990000000"
}
```

Reponse `201`:

```json
{
  "message": "Reservation creee.",
  "reservation": {
    "id": 12,
    "ticket_number": "BT-20260718-00012",
    "customer_name": "Jean Kalala",
    "customer_phone": "+243990000000",
    "seat_number": 1,
    "amount": 15000,
    "status": "EN_ATTENTE",
    "verification_url": "http://127.0.0.1:5000/verify/...",
    "trip": {
      "id": 3,
      "origin": "Likasi",
      "destination": "Lubumbashi",
      "departure_at": "2026-07-18T15:00"
    }
  }
}
```

## Paiement et confirmation

Modes de paiement acceptes:

```text
ESPECES, ORANGE_MONEY, AIRTEL_MONEY, MPESA, AFRIMONEY, AUTRE
```

Enregistrer le paiement:

```http
POST /api/reservations/12/payment
X-CSRF-Token: <csrf_token>
Content-Type: application/json

{
  "payment_method": "MPESA"
}
```

Confirmer le billet:

```http
POST /api/reservations/12/status
X-CSRF-Token: <csrf_token>
Content-Type: application/json

{
  "status": "CONFIRME"
}
```

Transitions autorisees:

```text
EN_ATTENTE -> PAYE, via /payment
PAYE -> CONFIRME
CONFIRME -> UTILISE
EN_ATTENTE/PAYE/CONFIRME -> ANNULE, admin uniquement et avec motif
```

## Verification QR Code

Le QR Code du billet contient une URL de verification. Pour le bot, utiliser le
token dans l'API JSON:

```http
GET /api/verify/<token>
```

Reponse:

```json
{
  "valid": true,
  "can_board": true,
  "already_used": false,
  "reservation": {
    "ticket_number": "BT-20260718-00012",
    "status": "CONFIRME"
  }
}
```

Marquer le billet comme utilise:

```http
POST /api/verify/<token>/use
X-CSRF-Token: <csrf_token>
Content-Type: application/json

{}
```

## Codes d'erreur

Les erreurs API sont renvoyees en JSON:

```json
{
  "error": "Message lisible par le bot."
}
```

Codes principaux:

- `400`: requete invalide ou CSRF manquant;
- `401`: connexion requise;
- `403`: droits administrateur requis;
- `404`: ressource introuvable;
- `409`: action impossible avec l'etat actuel du billet.

## Points restant hors API

Le bot peut deja vendre et verifier des billets. Les fonctions suivantes restent
volontairement hors API pour garder le logiciel simple:

- creation et modification des trajets;
- modification des tarifs;
- creation des comptes utilisateurs;
- annulation de paiement.

Ces operations se font dans l'interface administrateur.
