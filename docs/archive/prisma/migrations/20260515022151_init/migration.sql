-- CreateEnum
CREATE TYPE "Impact" AS ENUM ('LOW', 'MEDIUM', 'HIGH');

-- CreateEnum
CREATE TYPE "Direction" AS ENUM ('LONG', 'SHORT');

-- CreateEnum
CREATE TYPE "SignalStatus" AS ENUM ('PENDING', 'ACTIVE', 'CLOSED', 'CANCELLED');

-- CreateEnum
CREATE TYPE "TradeStatus" AS ENUM ('OPEN', 'CLOSED');

-- CreateTable
CREATE TABLE "Candle" (
    "id" TEXT NOT NULL,
    "symbol" TEXT NOT NULL,
    "timeframe" TEXT NOT NULL,
    "open" DECIMAL(18,8) NOT NULL,
    "high" DECIMAL(18,8) NOT NULL,
    "low" DECIMAL(18,8) NOT NULL,
    "close" DECIMAL(18,8) NOT NULL,
    "volume" DECIMAL(24,8) NOT NULL,
    "timestamp" TIMESTAMP(3) NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "Candle_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "Indicator" (
    "id" TEXT NOT NULL,
    "symbol" TEXT NOT NULL,
    "timeframe" TEXT NOT NULL,
    "timestamp" TIMESTAMP(3) NOT NULL,
    "rsi" DECIMAL(10,4),
    "ema20" DECIMAL(18,8),
    "ema50" DECIMAL(18,8),
    "ema200" DECIMAL(18,8),
    "atr" DECIMAL(18,8),
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "Indicator_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "NewsEvent" (
    "id" TEXT NOT NULL,
    "title" TEXT NOT NULL,
    "impact" "Impact" NOT NULL,
    "currency" TEXT NOT NULL,
    "scheduledAt" TIMESTAMP(3) NOT NULL,
    "actual" TEXT,
    "forecast" TEXT,
    "previous" TEXT,
    "aiSummary" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "NewsEvent_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "Signal" (
    "id" TEXT NOT NULL,
    "symbol" TEXT NOT NULL,
    "timeframe" TEXT NOT NULL,
    "direction" "Direction" NOT NULL,
    "entryPrice" DECIMAL(18,8) NOT NULL,
    "stopLoss" DECIMAL(18,8) NOT NULL,
    "takeProfit" DECIMAL(18,8) NOT NULL,
    "confidenceScore" INTEGER NOT NULL,
    "aiReasoning" TEXT NOT NULL,
    "status" "SignalStatus" NOT NULL DEFAULT 'PENDING',
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "Signal_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "Trade" (
    "id" TEXT NOT NULL,
    "signalId" TEXT NOT NULL,
    "entryPrice" DECIMAL(18,8) NOT NULL,
    "exitPrice" DECIMAL(18,8),
    "positionSize" DECIMAL(24,8) NOT NULL,
    "riskAmount" DECIMAL(18,2) NOT NULL,
    "profitLoss" DECIMAL(18,2),
    "status" "TradeStatus" NOT NULL DEFAULT 'OPEN',
    "openedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "closedAt" TIMESTAMP(3),

    CONSTRAINT "Trade_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "Journal" (
    "id" TEXT NOT NULL,
    "tradeId" TEXT NOT NULL,
    "notes" TEXT NOT NULL,
    "aiReview" TEXT NOT NULL,
    "emotions" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "Journal_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "RiskLog" (
    "id" TEXT NOT NULL,
    "accountBalance" DECIMAL(18,2) NOT NULL,
    "riskPercent" DECIMAL(6,4) NOT NULL,
    "positionSize" DECIMAL(24,8) NOT NULL,
    "dailyLoss" DECIMAL(18,2) NOT NULL,
    "dailyLossLimit" DECIMAL(18,2) NOT NULL,
    "circuitBreakerTripped" BOOLEAN NOT NULL DEFAULT false,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "RiskLog_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "Candle_symbol_timeframe_timestamp_idx" ON "Candle"("symbol", "timeframe", "timestamp");

-- CreateIndex
CREATE UNIQUE INDEX "Candle_symbol_timeframe_timestamp_key" ON "Candle"("symbol", "timeframe", "timestamp");

-- CreateIndex
CREATE INDEX "Indicator_symbol_timeframe_timestamp_idx" ON "Indicator"("symbol", "timeframe", "timestamp");

-- CreateIndex
CREATE UNIQUE INDEX "Indicator_symbol_timeframe_timestamp_key" ON "Indicator"("symbol", "timeframe", "timestamp");

-- CreateIndex
CREATE INDEX "NewsEvent_scheduledAt_idx" ON "NewsEvent"("scheduledAt");

-- CreateIndex
CREATE INDEX "NewsEvent_currency_idx" ON "NewsEvent"("currency");

-- CreateIndex
CREATE INDEX "Signal_symbol_status_idx" ON "Signal"("symbol", "status");

-- CreateIndex
CREATE INDEX "Signal_createdAt_idx" ON "Signal"("createdAt");

-- CreateIndex
CREATE INDEX "Trade_signalId_idx" ON "Trade"("signalId");

-- CreateIndex
CREATE INDEX "Trade_status_idx" ON "Trade"("status");

-- CreateIndex
CREATE INDEX "Journal_tradeId_idx" ON "Journal"("tradeId");

-- CreateIndex
CREATE INDEX "RiskLog_createdAt_idx" ON "RiskLog"("createdAt");

-- AddForeignKey
ALTER TABLE "Trade" ADD CONSTRAINT "Trade_signalId_fkey" FOREIGN KEY ("signalId") REFERENCES "Signal"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Journal" ADD CONSTRAINT "Journal_tradeId_fkey" FOREIGN KEY ("tradeId") REFERENCES "Trade"("id") ON DELETE RESTRICT ON UPDATE CASCADE;
