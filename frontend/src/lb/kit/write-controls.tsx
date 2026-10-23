import type { PropsWithChildren } from "react";
import { useAuthStore } from "@/features/auth/hooks/use-auth";

export function WriteControls({ children }: PropsWithChildren) {
  const canWrite = useAuthStore((state) => state.canWrite);
  return (
    <fieldset disabled={!canWrite} className="write-controls">
      {children}
    </fieldset>
  );
}
