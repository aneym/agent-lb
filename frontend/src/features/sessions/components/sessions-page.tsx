import { Activity } from "lucide-react";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { PaginationControls } from "@/features/dashboard/components/filters/pagination-controls";
import { useSessions } from "@/features/sessions/hooks/use-sessions";
import type { SessionAggregate } from "@/features/sessions/schemas";
import { getErrorMessageOrNull } from "@/utils/errors";
import {
  formatCompactNumber,
  formatCurrency,
  formatDateTimeInline,
  formatModelLabel,
  formatTokensWithCached,
  truncateText,
} from "@/utils/formatters";
import { SessionDetail } from "./session-detail";

const WINDOW_OPTIONS = [
  { label: "1h", minutes: 60 },
  { label: "6h", minutes: 360 },
  { label: "24h", minutes: 1_440 },
  { label: "3d", minutes: 4_320 },
  { label: "7d", minutes: 10_080 },
] as const;
const DEFAULT_WINDOW_MINUTES = 4_320;
const DEFAULT_LIMIT = 25;

export function SessionsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [windowMinutes, setWindowMinutes] = useState(DEFAULT_WINDOW_MINUTES);
  const [limit, setLimit] = useState(DEFAULT_LIMIT);
  const [offset, setOffset] = useState(0);
  const selectedSessionId = searchParams.get("session");
  const sessionsQuery = useSessions({ windowMinutes, limit, offset });
  const errorMessage = getErrorMessageOrNull(sessionsQuery.error);
  const sessions = sessionsQuery.data?.sessions ?? [];
  const total = sessionsQuery.data?.total ?? 0;

  const handleWindowChange = (minutes: number) => {
    setWindowMinutes(minutes);
    setOffset(0);
  };

  const setSelectedSession = (sessionId: string | null) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      if (sessionId) {
        next.set("session", sessionId);
      } else {
        next.delete("session");
      }
      return next;
    });
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">
            Sessions
          </h1>
          <p className="text-sm text-muted-foreground">
            Usage grouped by client session
          </p>
        </div>
        <div
          className="flex items-center gap-1 rounded-lg border bg-card p-1"
          aria-label="Session window"
        >
          {WINDOW_OPTIONS.map((option) => (
            <Button
              key={option.minutes}
              type="button"
              size="sm"
              variant={windowMinutes === option.minutes ? "secondary" : "ghost"}
              className="h-7 px-2.5 text-xs"
              aria-pressed={windowMinutes === option.minutes}
              onClick={() => handleWindowChange(option.minutes)}
            >
              {option.label}
            </Button>
          ))}
        </div>
      </div>

      {errorMessage ? (
        <div className="space-y-3">
          <AlertMessage variant="error">
            Failed to load sessions: {errorMessage}
          </AlertMessage>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => {
              void sessionsQuery.refetch();
            }}
          >
            Retry
          </Button>
        </div>
      ) : sessionsQuery.isLoading ? (
        <SessionsTableSkeleton />
      ) : sessions.length === 0 ? (
        <EmptyState
          icon={Activity}
          title="No sessions"
          description="No client sessions were active during this window."
        />
      ) : (
        <>
          <div className="overflow-x-auto rounded-xl border bg-card">
            <Table className="min-w-[1120px] table-fixed">
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="w-44 pl-4">Session</TableHead>
                  <TableHead className="w-32">Client</TableHead>
                  <TableHead>Models</TableHead>
                  <TableHead className="w-24 text-right">Requests</TableHead>
                  <TableHead className="w-36 text-right">Tokens</TableHead>
                  <TableHead className="w-24 text-right">Cost</TableHead>
                  <TableHead className="w-20 text-right">Errors</TableHead>
                  <TableHead className="w-40 pr-4">Activity</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sessions.map((session) => (
                  <SessionRow
                    key={session.sessionId}
                    session={session}
                    onSelect={() => setSelectedSession(session.sessionId)}
                  />
                ))}
              </TableBody>
            </Table>
          </div>
          <div className="flex justify-end">
            <PaginationControls
              total={total}
              limit={limit}
              offset={offset}
              hasMore={offset + sessions.length < total}
              onLimitChange={(nextLimit) => {
                setLimit(nextLimit);
                setOffset(0);
              }}
              onOffsetChange={setOffset}
            />
          </div>
        </>
      )}

      <SessionDetail
        sessionId={selectedSessionId}
        onClose={() => setSelectedSession(null)}
      />
    </div>
  );
}

type SessionRowProps = {
  session: SessionAggregate;
  onSelect: () => void;
};

function SessionRow({ session, onSelect }: SessionRowProps) {
  return (
    <TableRow
      className="cursor-pointer"
      tabIndex={0}
      aria-label={`View session ${session.sessionId}`}
      onClick={onSelect}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelect();
        }
      }}
    >
      <TableCell className="pl-4 font-mono text-xs" title={session.sessionId}>
        {truncateText(session.sessionId, 22)}
      </TableCell>
      <TableCell
        className="truncate text-sm"
        title={session.useragentGroup ?? undefined}
      >
        {session.useragentGroup ?? "--"}
      </TableCell>
      <TableCell>
        <div className="flex flex-wrap gap-1.5">
          {session.models.map((entry) => (
            <Badge
              key={entry.model}
              variant="outline"
              className="max-w-52 font-mono text-[10px]"
              title={`${entry.requests} request${entry.requests === 1 ? "" : "s"}`}
            >
              <span className="truncate">
                {formatModelLabel(entry.model, null)}
              </span>
            </Badge>
          ))}
        </div>
      </TableCell>
      <TableCell className="text-right font-mono text-xs tabular-nums">
        {formatCompactNumber(session.requests)}
      </TableCell>
      <TableCell className="text-right font-mono text-xs tabular-nums">
        {formatTokensWithCached(
          session.inputTokens + session.outputTokens,
          session.cachedInputTokens,
        )}
      </TableCell>
      <TableCell className="text-right font-mono text-xs tabular-nums">
        {formatCurrency(session.costUsd)}
      </TableCell>
      <TableCell
        className={`text-right font-mono text-xs tabular-nums ${session.errors > 0 ? "font-semibold" : "text-muted-foreground"}`}
      >
        {formatCompactNumber(session.errors)}
      </TableCell>
      <TableCell className="pr-4 text-xs whitespace-nowrap">
        <div>
          <span className="text-muted-foreground">First </span>
          {formatDateTimeInline(session.firstSeen)}
        </div>
        <div>
          <span className="text-muted-foreground">Last </span>
          {formatDateTimeInline(session.lastSeen)}
        </div>
      </TableCell>
    </TableRow>
  );
}

function SessionsTableSkeleton() {
  return (
    <div className="space-y-2 rounded-xl border bg-card p-4">
      <div className="grid grid-cols-4 gap-4 border-b pb-3">
        {Array.from({ length: 4 }).map((_, index) => (
          <Skeleton key={index} className="h-3 w-24" />
        ))}
      </div>
      {Array.from({ length: 6 }).map((_, index) => (
        <div key={index} className="grid grid-cols-4 gap-4 py-2">
          <Skeleton className="h-4 w-32" />
          <Skeleton className="h-4 w-24" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-20" />
        </div>
      ))}
    </div>
  );
}
