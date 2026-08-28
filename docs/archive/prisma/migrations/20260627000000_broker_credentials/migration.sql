-- BrokerCredential: MT5 account credentials managed from the web UI.
-- Password is stored encrypted (AES-256-GCM); never plaintext.

CREATE TABLE "BrokerCredential" (
    "id"          TEXT NOT NULL,
    "broker"      TEXT NOT NULL DEFAULT 'exness',
    "login"       INTEGER NOT NULL,
    "passwordEnc" TEXT NOT NULL,
    "server"      TEXT NOT NULL,
    "env"         TEXT NOT NULL DEFAULT 'demo',
    "isActive"    BOOLEAN NOT NULL DEFAULT true,
    "lastTest"    JSONB,
    "createdAt"   TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt"   TIMESTAMP(3) NOT NULL,
    CONSTRAINT "BrokerCredential_pkey" PRIMARY KEY ("id")
);

CREATE INDEX "BrokerCredential_isActive_idx" ON "BrokerCredential"("isActive");
