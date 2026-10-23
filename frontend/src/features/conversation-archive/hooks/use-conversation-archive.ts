import { useQuery } from "@tanstack/react-query";

import {
  listConversationArchiveRecords,
  listConversationArchiveFiles,
  type ConversationArchiveRecordParams,
} from "@/features/conversation-archive/api";

export function useConversationArchiveRecords(params: ConversationArchiveRecordParams | null) {
  return useQuery({
    queryKey: ["conversation-archive", "records", params],
    queryFn: () => listConversationArchiveRecords(params!),
    enabled: params !== null,
  });
}

export function useConversationArchiveFiles() {
  return useQuery({
    queryKey: ["conversation-archive", "files"],
    queryFn: listConversationArchiveFiles,
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
  });
}
