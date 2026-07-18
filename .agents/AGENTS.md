# Guide de contribution — Billetterie Flask

## Aperçu du projet

Cette application de billetterie est une application Flask monolithique et légère :

- `app.py` contient la configuration, les routes HTTP, la validation et les requêtes SQLite ;
- `schema.sql` décrit le schéma de la base de données ;
- `tickets.db` est la base SQLite locale de développement ;
- `templates/` contient les pages HTML utilisant Jinja ;
- `static/style.css` contient les styles de l'interface.

Il n'y a ni ORM, ni système d'authentification, ni API séparée. Conserver cette simplicité sauf demande explicite.

## Installation et exécution

Utiliser Python et les dépendances indiquées dans `requirements.txt` :

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
flask --app app init-db
flask --app app run --debug
```

L'application est disponible sur `http://127.0.0.1:5000`.

## Conventions Python et Flask

- Garder les routes et leur logique dans `app.py` tant que le projet reste de cette taille.
- Employer des requêtes SQL paramétrées (`?` et un tuple de paramètres) ; ne jamais concaténer des entrées utilisateur dans une requête.
- Obtenir la connexion avec `get_db()` et laisser `close_db()` la fermer à la fin du contexte Flask.
- Après une écriture en base, appeler `db.commit()` ; en cas d'erreur d'intégrité, utiliser `db.rollback()` avant de poursuivre.
- Valider côté serveur toutes les données reçues par formulaire, même si le HTML possède déjà des attributs `required`, `min` ou `type`.
- Pour une ressource absente, utiliser `abort(404)` ; pour une donnée invalide, utiliser `abort(400)` ou un message `flash()` selon le cas.
- Préserver les docstrings et commentaires en français lors de toute modification importante.

## Base de données

- Toute modification de structure doit d'abord être ajoutée à `schema.sql`.
- Le script emploie `CREATE ... IF NOT EXISTS` : il initialise une base neuve mais ne migre pas automatiquement une base existante.
- Ne pas supprimer ni recréer `tickets.db` sans demande explicite : elle peut contenir des données de travail.
- Respecter les relations existantes : une réservation référence un trajet et un paiement référence une réservation.
- Préserver l'index unique `one_active_seat_per_trip`, qui empêche la double réservation d'un siège actif.

## Templates et styles

- Tous les templates de page étendent `templates/base.html`.
- Réutiliser `templates/reservation_table.html` pour les tableaux de réservations plutôt que de dupliquer son HTML.
- Générer les liens et actions avec `url_for()` ; ne pas coder les chemins en dur si une route Flask existe.
- Conserver l'échappement automatique de Jinja : ne pas utiliser `|safe` sur des valeurs utilisateur non assainies.
- Ajouter les styles dans `static/style.css` et réutiliser les variables CSS de `:root` lorsque cela convient.
- Vérifier l'affichage mobile : le point de rupture actuel est `760px`.

## Vérifications avant livraison

Exécuter au minimum :

```powershell
python -m py_compile app.py
```

Puis vérifier les pages concernées avec le client de test Flask ou dans le navigateur. Pour une modification générale, contrôler au minimum :

- `/`
- `/trips`
- `/reservations`
- `/reservations/new`
- `/reservations/<id>` avec un identifiant existant

Ne pas inclure de clés de production dans le dépôt : la valeur de `SECRET_KEY` actuelle est uniquement adaptée au développement.
