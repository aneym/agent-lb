import { Activity } from "lucide-react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { SpinnerBlock } from "@/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useSession } from "@/features/sessions/hooks/use-sessions";
import { getErrorMessageOrNull } from "@/utils/errors";
import {
  formatCompactNumber,
  formatCurrency,
  formatDateTimeInline,
  formatModelLabel,
  formatTokensWithCached,
} from "@/utils/formatters";

export type SessionDetailProps = {
  sessionId: string | null;
  onClose: () => void;
};

export function SessionDetail({ sessionId, onClose }: SessionDetailProps) {
  const detailQuery = useSession(sessionId);
  const errorMessage = getErrorMessageOrNull(detailQuery.error);
  const detail = detailQuery.data;

  return (
    <Dialog
      open={sessionId !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent className="max-h-[90vh] sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>Session Details</DialogTitle>
          <DialogDescription className="break-all font-mono text-xs">
            {sessionId}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-5 overflow-y-auto pr-1">
          {detailQuery.isLoading ? (
            <div className="py-12">
              <SpinnerBlock />
            </div>
          ) : errorMessage ? (
            <AlertMessage variant="error">
              Failed to load session details: {errorMessage}
            </AlertMessage>
          ) : detail ? (
            <>
              <div className="grid gap-3 rounded-xl border bg-muted/30 p-4 sm:grid-cols-2 lg:grid-cols-4">
                <Metric
                  label="Client"
                  value={detail.session.useragentGroup ?? "--"}
                />
                <Metric
                  label="Requests"
                  value={formatCompactNumber(detail.session.requests)}
                />
                <Metric
                  label="Tokens"
                  value={formatTokensWithCached(
                    detail.session.inputTokens + detail.session.outputTokens,
                    detail.session.cachedInputTokens,
                  )}
                />
                <Metric
                  label="Cost"
                  value={formatCurrency(detail.session.costUsd)}
                />
                <Metric
                  label="First activity"
                  value={formatDateTimeInline(detail.session.firstSeen)}
                />
                <Metric
                  label="Last activity"
                  value={formatDateTimeInline(detail.session.lastSeen)}
                />
                <Metric
                  label="Provider"
                  value={detail.session.provider ?? "--"}
                />
                <Metric
                  label="Errors"
                  value={formatCompactNumber(detail.session.errors)}
                  isEmphasized={detail.session.errors > 0}
                />
              </div>

              <section className="space-y-2">
                <h3 className="text-sm font-semibold">Usage by model</h3>
                <div className="overflow-x-auto rounded-xl border">
                  <Table>
                    <TableHeader>
                      <TableRow className="hover:bg-transparent">
                        <TableHead>Model</TableHead>
                        <TableHead className="text-right">Requests</TableHead>
                        <TableHead className="text-right">Tokens</TableHead>
                        <TableHead className="text-right">Cost</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {detail.byModel.map((entry) => (
                        <TableRow key={entry.model}>
                          <TableCell className="font-mono text-xs">
                            {formatModelLabel(entry.model, null)}
                          </TableCell>
                          <TableCell className="text-right font-mono text-xs tabular-nums">
                            {formatCompactNumber(entry.requests)}
                          </TableCell>
                          <TableCell className="text-right font-mono text-xs tabular-nums">
                            {formatTokensWithCached(
                              entry.inputTokens + entry.outputTokens,
                              entry.cachedInputTokens,
                            )}
                          </TableCell>
                          <TableCell className="text-right font-mono text-xs tabular-nums">
                            {formatCurrency(entry.costUsd)}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              </section>

              <section className="space-y-2">
                <h3 className="text-sm font-semibold">Recent requests</h3>
                {detail.recentRequests.length === 0 ? (
                  <EmptyState
                    icon={Activity}
                    title="No recent requests"
                    description="No request-log rows are available for this session."
                  />
                ) : (
                  <div className="overflow-x-auto rounded-xl border">
                    <Table>
                      <TableHeader>
                        <TableRow className="hover:bg-transparent">
                          <TableHead>Time</TableHead>
                          <TableHead>Model</TableHead>
                          <TableHead>Status</TableHead>
                          <TableHead className="text-right">Tokens</TableHead>
                          <TableHead className="text-right">Cost</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {detail.recentRequests.map((request) => (
                          <TableRow key={request.requestId}>
                            <TableCell className="whitespace-nowrap text-xs">
                              {formatDateTimeInline(request.requestedAt)}
                            </TableCell>
                            <TableCell className="font-mono text-xs">
                              {formatModelLabel(
                                request.model,
                                request.reasoningEffort,
                                request.actualServiceTier ??
                                  request.serviceTier,
                              )}
                            </TableCell>
                            <TableCell
                              className={
                                request.status === "ok"
                                  ? "text-xs text-muted-foreground"
                                  : "text-xs font-medium"
                              }
                            >
                              {request.status}
                            </TableCell>
                            <TableCell className="text-right font-mono text-xs tabular-nums">
                              {formatTokensWithCached(
                                request.tokens,
                                request.cachedInputTokens,
                              )}
                            </TableCell>
                            <TableCell className="text-right font-mono text-xs tabular-nums">
                              {formatCurrency(request.costUsd)}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                )}
              </section>
            </>
          ) : null}
        </div>
        <DialogFooter showCloseButton />
      </DialogContent>
    </Dialog>
  );
}

type MetricProps = {
  label: string;
  value: string;
  isEmphasized?: boolean;
};

function Metric({ label, value, isEmphasized = false }: MetricProps) {
  return (
    <div className="space-y-1">
      <div className="text-xs font-medium text-muted-foreground">{label}</div>
      <div className={`text-sm ${isEmphasized ? "font-semibold" : ""}`}>
        {value}
      </div>
    </div>
  );
}
