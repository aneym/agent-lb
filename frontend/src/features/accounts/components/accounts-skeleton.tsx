import { Skeleton } from "@/components/ui/skeleton";

export function AccountsSkeleton() {
  return (
    <div className="grid gap-4 lg:grid-cols-[24rem_minmax(0,1fr)]">
      {/* Left: filter toolbar + account list */}
      <div className="space-y-3 rounded-xl border bg-card p-4">
        <div className="flex gap-2">
          <Skeleton className="h-8 flex-1 rounded-md" />
          <Skeleton className="h-8 flex-1 rounded-md" />
        </div>
        <Skeleton className="h-8 w-full rounded-md" />
        <Skeleton className="h-8 w-full rounded-md" />
        <div className="grid grid-cols-3 gap-2">
          <Skeleton className="h-8 rounded-md" />
          <Skeleton className="h-8 rounded-md" />
          <Skeleton className="h-8 rounded-md" />
        </div>
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="space-y-2 rounded-md px-3 py-2.5">
            <div className="flex items-center justify-between gap-2">
              <Skeleton className="h-4 w-44" />
              <Skeleton className="h-3.5 w-3.5 rounded-full" />
            </div>
            <Skeleton className="h-3 w-32" />
            <div className="grid grid-cols-2 gap-3">
              <Skeleton className="h-1 w-full rounded-full" />
              <Skeleton className="h-1 w-full rounded-full" />
            </div>
          </div>
        ))}
      </div>

      {/* Right: account detail */}
      <div className="rounded-xl border bg-card">
        {/* Header: identity + actions */}
        <div className="space-y-3 p-5">
          <div className="flex items-center justify-between gap-2">
            <Skeleton className="h-5 w-48" />
            <Skeleton className="h-3.5 w-16" />
          </div>
          <Skeleton className="h-3 w-56" />
          <div className="flex flex-wrap gap-2">
            <Skeleton className="h-8 w-20 rounded-md" />
            <Skeleton className="h-8 w-28 rounded-md" />
            <Skeleton className="h-8 w-32 rounded-md" />
            <Skeleton className="h-8 w-20 rounded-md" />
          </div>
        </div>

        {/* Usage */}
        <div className="space-y-4 border-t p-5">
          <Skeleton className="h-4 w-14" />
          <div className="grid grid-cols-2 gap-4">
            {Array.from({ length: 2 }).map((_, i) => (
              <div key={i} className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <Skeleton className="h-3 w-28" />
                  <Skeleton className="h-3 w-10" />
                </div>
                <Skeleton className="h-1 w-full rounded-full" />
                <Skeleton className="h-3 w-24" />
              </div>
            ))}
          </div>
          <div className="flex items-center justify-between">
            <Skeleton className="h-3 w-28" />
            <Skeleton className="h-3 w-48" />
          </div>
        </div>

        {/* Trend */}
        <div className="border-t p-5">
          <div className="mb-2 flex items-center justify-between">
            <Skeleton className="h-4 w-24" />
            <div className="flex items-center gap-3">
              <Skeleton className="h-2.5 w-12" />
              <Skeleton className="h-2.5 w-16" />
            </div>
          </div>
          <Skeleton className="h-24 w-full rounded-md" />
        </div>

        {/* Connection */}
        <div className="space-y-3 border-t p-5">
          <Skeleton className="h-4 w-24" />
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="flex items-center justify-between">
              <Skeleton className="h-3 w-20" />
              <Skeleton className="h-3 w-24" />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
