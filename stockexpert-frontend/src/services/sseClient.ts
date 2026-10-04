import { API_BASE, getToken, ApiError } from "./api";
import type { ProgressEventData } from "../types";
/** Fetch-based SSE supports Authorization headers and cancellation on session changes. */
export async function streamProgress(
  sid: string,
  signal: AbortSignal,
  onMessage: (value: ProgressEventData) => void,
) {
  const response = await fetch(
    `${API_BASE}/procurement/sessions/${sid}/stream-progress`,
    {
      signal,
      headers: {
        Authorization: `Bearer ${getToken()}`,
        Accept: "text/event-stream",
      },
    },
  );
  if (!response.ok)
    throw new ApiError(
      response.status,
      `Поток прогресса: HTTP ${response.status}`,
    );
  if (!response.body) throw new Error("Поток недоступен");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
      let index;
      while ((index = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, index);
        buffer = buffer.slice(index + 2);
        const data = block
          .split("\n")
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trimStart())
          .join("\n");
        if (data) onMessage(JSON.parse(data) as ProgressEventData);
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
