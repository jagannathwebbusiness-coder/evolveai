export type Message = { role: "user" | "assistant"; content: string };
export type ModelStatus = {
  status:
    | "ready"
    | "not_configured"
    | "invalid_key"
    | "model_missing"
    | "unreachable"
    | "error";
  provider: string;
  api_key_configured: boolean;
  provider_reachable: boolean;
  model_available: boolean | null;
  model: string;
  base_url: string;
  message: string;
};
export type ToolCatalog = {
  demo: {
    enabled: boolean;
    available: boolean;
    reason: string;
    secure_sandbox: false;
  };
  tools: { name: string; version: string; description: string }[];
  upload_max_bytes: number;
  csv_max_rows: number;
  csv_max_columns: number;
};
export type Connection = {
  health: boolean;
  model: ModelStatus;
  tools: ToolCatalog;
};
export type ActivityEvent = {
  sequence: number;
  kind: string;
  message: string;
  iteration: number | null;
  tool: string | null;
  call_id: string | null;
  arguments: Record<string, unknown> | null;
  result: Record<string, unknown> | null;
};
export type Upload = {
  task_id: string;
  upload_id: string;
  filename: string;
  size_bytes: number;
  row_count: number;
  columns: string[];
  conversation_id?: string | null;
  kind?: "csv" | "xlsx";
  sheets?: { name: string; row_count: number; columns: string[] }[];
};
export type ChatOptions = {
  taskId: string;
  upload?: Upload | null;
  conversationId: string;
  retryOf?: string | null;
};
export class ApiError extends Error {
  constructor(
    message: string,
    public activity: ActivityEvent[] = [],
    public taskId: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}
export type ChatReply = {
  model: string;
  message: string;
  duration_ms: number;
  truncated: boolean;
  task_id: string | null;
  activity: ActivityEvent[];
};

export async function request<T>(
  path: string,
  init?: RequestInit,
  timeout = 10_000,
): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(`/api${path}`, {
      ...init,
      signal: controller.signal,
    });
    const body = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = body?.detail;
      const message =
        typeof detail === "object" && !Array.isArray(detail)
          ? detail?.message
          : null;
      throw new ApiError(
        message ||
          (response.status === 422
            ? "The message is invalid or the chat context is too long. Start a new chat or shorten your message."
            : `Backend returned HTTP ${response.status}. Check that FastAPI is running on the configured port.`),
        Array.isArray(detail?.activity) ? detail.activity : [],
        typeof detail?.task_id === "string" ? detail.task_id : null,
      );
    }
    if (body === null)
      throw new Error(
        "Backend returned an invalid response. Check the FastAPI server.",
      );
    return body as T;
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new Error(
        "The request timed out. Check the backend and your internet connection, then retry.",
      );
    }
    if (error instanceof TypeError) {
      throw new Error(
        "Cannot connect to the backend. Start FastAPI and check VITE_API_PROXY_TARGET in .env.",
      );
    }
    throw error;
  } finally {
    window.clearTimeout(timer);
  }
}

export async function checkConnection(): Promise<Connection> {
  const [health, model, tools] = await Promise.all([
    request<{ status: string }>("/health"),
    request<ModelStatus>("/model-status"),
    request<ToolCatalog>("/tools"),
  ]);
  return { health: health.status === "ok", model, tools };
}

// Keep recent exchanges within the backend's explicit character/message limits.
export function contextFor(messages: Message[]): Message[] {
  let remaining = 6000;
  const context: Message[] = [];
  for (const original of messages.slice(-20).reverse()) {
    const message = { ...original, content: original.content.slice(0, 2000) };
    if (message.content.length > remaining) break;
    context.unshift(message);
    remaining -= message.content.length;
  }
  while (context.length > 1 && context[0].role === "assistant") context.shift();
  return context;
}

export function sendChat(
  messages: Message[],
  options: ChatOptions,
): Promise<ChatReply> {
  return request<ChatReply>(
    "/chat",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        messages: [messages[messages.length - 1]],
        conversation_id: options.conversationId,
        task_id: options.taskId,
        use_tools: true,
        tool_choice: "auto",
        agentic: true,
        demo_tools: true,
        upload_ids: options.upload ? [options.upload.upload_id] : [],
        retry_of: options.retryOf ?? null,
      }),
    },
    620_000,
  );
}

export function uploadFile(
  file: File,
  taskId: string,
  conversationId: string,
): Promise<Upload> {
  const form = new FormData();
  form.append("file", file);
  form.append("task_id", taskId);
  form.append("conversation_id", conversationId);
  return request<Upload>("/uploads", { method: "POST", body: form }, 30_000);
}

export type Conversation = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
};
export type Task = {
  id: string;
  status: string;
  created_at: string;
  duration_ms: number | null;
  error: string | null;
  activity: ActivityEvent[];
  memory: { key: string; value: string }[];
};
export type ConversationDetail = Conversation & {
  messages: (Message & {
    id: string;
    task_id: string;
    created_at: string;
    metadata: { model?: string; duration_ms?: number; truncated?: boolean };
  })[];
  tasks: Task[];
};
export type Preference = {
  id: string;
  key: string;
  value: string;
  keywords: string;
  conversation_id: string | null;
  updated_at: string;
};
export type StoredFile = {
  id: string;
  kind: "upload" | "report";
  conversation_id: string | null;
  task_id: string;
  filename: string;
  relative_path: string;
  size_bytes: number;
  available: boolean;
  metadata: {
    row_count?: number;
    columns?: string[];
    sheets?: { name: string; row_count: number; columns: string[] }[];
    title?: string;
    format?: string;
  };
};
export function jsonRequest<T>(
  path: string,
  method: string,
  body?: unknown,
): Promise<T> {
  return request<T>(path, {
    method,
    headers: { "Content-Type": "application/json" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
}
