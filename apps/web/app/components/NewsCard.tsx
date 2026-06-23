"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api";
import type { Impact, NewsItem, NewsResponse } from "@/lib/types";

const IMPACT_TONE: Record<Impact, string> = {
  HIGH: "bg-rose-500/15 text-rose-300 border-rose-500/30",
  MEDIUM: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  LOW: "bg-neutral-700/40 text-neutral-400 border-neutral-700",
};

function when(iso: string, upcoming: boolean): string {
  const d = new Date(iso);
  const day = d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  const time = d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  return `${upcoming ? "in " : ""}${day} ${time}`;
}

export function NewsCard() {
  const { data, error, isLoading } = useSWR<NewsResponse>("/api/news?limit=12", fetcher, {
    refreshInterval: 60_000,
  });

  const rows = data?.data ?? [];
  const highCount = rows.filter((r) => r.impact === "HIGH" && r.upcoming).length;

  return (
    <section className="rounded-lg border border-neutral-800 bg-neutral-900/30">
      <div className="flex items-center justify-between border-b border-neutral-800 px-5 py-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-400">
          News &amp; Calendar
        </h2>
        {highCount > 0 && (
          <span className="rounded-full bg-rose-500/15 px-2 py-0.5 text-[11px] font-medium text-rose-300">
            {highCount} high-impact ahead
          </span>
        )}
      </div>

      {isLoading && <p className="px-5 py-6 text-sm text-neutral-500">Loading…</p>}
      {error && <p className="px-5 py-6 text-sm text-rose-400">Failed to load news</p>}
      {!isLoading && !error && rows.length === 0 && (
        <p className="px-5 py-6 text-sm text-neutral-500">
          No news yet. The n8n calendar + AI-summary workflows populate this feed.
        </p>
      )}

      <ul className="divide-y divide-neutral-800/60">
        {rows.map((n) => (
          <NewsRow key={n.id} item={n} />
        ))}
      </ul>
    </section>
  );
}

function NewsRow({ item }: { item: NewsItem }) {
  return (
    <li className="px-5 py-3">
      <div className="flex items-start gap-3">
        <span
          className={`mt-0.5 rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase ${IMPACT_TONE[item.impact]}`}
        >
          {item.impact}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <span className="font-mono text-[11px] text-neutral-500">{item.currency}</span>
            <span className="truncate text-sm text-neutral-200">{item.title}</span>
          </div>
          {item.aiSummary && (
            <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-neutral-500">{item.aiSummary}</p>
          )}
          {(item.forecast || item.previous || item.actual) && (
            <div className="mt-1 flex gap-3 font-mono text-[11px] text-neutral-600">
              {item.actual && <span>act {item.actual}</span>}
              {item.forecast && <span>fc {item.forecast}</span>}
              {item.previous && <span>prev {item.previous}</span>}
            </div>
          )}
        </div>
        <span
          className={`whitespace-nowrap font-mono text-[11px] ${
            item.upcoming ? "text-emerald-400/80" : "text-neutral-600"
          }`}
        >
          {when(item.scheduledAt, item.upcoming)}
        </span>
      </div>
    </li>
  );
}
