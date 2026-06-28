import type { ChatConversation, ChatConversationDetail } from "./types";
import { API_BASE, apiGet, apiSend } from "./api";

export async function listConversations(
  projectId: string
): Promise<ChatConversation[]> {
  return apiGet<ChatConversation[]>(`/projects/${projectId}/conversations`);
}

export async function createConversation(
  projectId: string,
  content: string
): Promise<ChatConversation> {
  const conv = await apiSend<ChatConversation>(
    "POST",
    `/projects/${projectId}/conversations`,
    { content }
  );
  if (!conv) throw new Error("create conversation failed: no body");
  return conv;
}

export async function getConversation(
  projectId: string,
  conversationId: string
): Promise<ChatConversationDetail> {
  return apiGet<ChatConversationDetail>(
    `/projects/${projectId}/conversations/${conversationId}`
  );
}

export async function deleteConversation(
  projectId: string,
  conversationId: string
): Promise<void> {
  await apiSend("DELETE", `/projects/${projectId}/conversations/${conversationId}`);
}

export function chatMessagesUrl(
  projectId: string,
  conversationId: string
): string {
  return `${API_BASE}/v1/projects/${projectId}/conversations/${conversationId}/messages`;
}
