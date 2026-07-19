-- Trajets proposés à la vente : chaque réservation référence l'un d'eux.
CREATE TABLE IF NOT EXISTS trips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    departure_at TEXT NOT NULL,
    seat_count INTEGER NOT NULL CHECK (seat_count > 0),
    price REAL NOT NULL CHECK (price >= 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Billets réservés par les clients. Les contraintes limitent les valeurs incohérentes.
CREATE TABLE IF NOT EXISTS reservations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_id INTEGER NOT NULL REFERENCES trips(id),
    customer_name TEXT NOT NULL,
    customer_phone TEXT NOT NULL,
    seat_number INTEGER NOT NULL,
    ticket_number TEXT UNIQUE,
    amount REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'EN_ATTENTE' CHECK (status IN ('EN_ATTENTE', 'PAYE', 'CONFIRME', 'ANNULE', 'UTILISE')),
    created_by INTEGER REFERENCES users(id),
    cancelled_at TEXT,
    cancel_reason TEXT,
    cancelled_by INTEGER REFERENCES users(id),
    verification_token TEXT UNIQUE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Interdit de vendre deux fois un même siège, sauf si la première réservation est annulée.
CREATE UNIQUE INDEX IF NOT EXISTS one_active_seat_per_trip
ON reservations(trip_id, seat_number)
WHERE status != 'ANNULE';

-- Historique des paiements rattachés à une réservation.
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reservation_id INTEGER NOT NULL REFERENCES reservations(id),
    method TEXT NOT NULL,
    amount REAL NOT NULL CHECK (amount >= 0),
    received_by INTEGER REFERENCES users(id),
    voided_at TEXT,
    void_reason TEXT,
    voided_by INTEGER REFERENCES users(id),
    paid_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
-- Comptes utilisés pour accéder à l'administration de la billetterie.
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL COLLATE NOCASE UNIQUE CHECK (length(username) BETWEEN 3 AND 80),
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0 CHECK (is_admin IN (0, 1)),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Préférences globales de l'application, stockées sous forme clé/valeur.
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Tarifs administrés par liaison ; le même prix s'applique dans les deux sens.
CREATE TABLE IF NOT EXISTS fares (
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    price REAL NOT NULL CHECK (price >= 0),
    PRIMARY KEY (origin, destination),
    CHECK (origin != destination)
);

-- Journal des opérations sensibles pour assurer la traçabilité.
CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id),
    action TEXT NOT NULL,
    details TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Etat de conversation du bot WhatsApp pour chaque client.
CREATE TABLE IF NOT EXISTS whatsapp_conversations (
    phone TEXT PRIMARY KEY,
    step TEXT NOT NULL DEFAULT 'ASK_ORIGIN',
    data TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Journal minimal des messages WhatsApp recus et envoyes.
CREATE TABLE IF NOT EXISTS whatsapp_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('IN', 'OUT')),
    message TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Demandes de paiement réseau initiées auprès d'un fournisseur comme Shwary.
CREATE TABLE IF NOT EXISTS payment_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reservation_id INTEGER NOT NULL REFERENCES reservations(id),
    provider TEXT NOT NULL,
    provider_transaction_id TEXT,
    reference_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'PENDING',
    amount REAL NOT NULL,
    currency TEXT NOT NULL,
    phone TEXT NOT NULL,
    country_code TEXT NOT NULL,
    checkout_payload TEXT,
    failure_reason TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
