export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded bg-neutral-800/70 ${className}`} />;
}
