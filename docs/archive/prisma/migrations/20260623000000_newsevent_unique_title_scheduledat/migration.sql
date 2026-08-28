-- Deduplicate NewsEvent ingestion: a (title, scheduledAt) pair identifies one event.
-- Drop any pre-existing duplicates before adding the constraint.
DELETE FROM "NewsEvent" a
USING "NewsEvent" b
WHERE a."ctid" < b."ctid"
  AND a."title" = b."title"
  AND a."scheduledAt" = b."scheduledAt";

-- CreateIndex
CREATE UNIQUE INDEX "NewsEvent_title_scheduledAt_key" ON "NewsEvent"("title", "scheduledAt");
