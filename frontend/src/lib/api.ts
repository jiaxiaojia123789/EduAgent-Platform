const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";

export interface UserInfo {
  id: string;
  username: string;
  email: string;
  full_name: string;
  role: string;
  created_at?: string;
}

export interface UserProfile {
  user_id: string;
  subject: string;
  grade: string;
  textbook_version: string;
  student_analysis: string;
  teaching_style: string;
  auto_memory_enabled: boolean;
  updated_at: string;
}

export interface MemoryItem {
  id: string;
  user_id: string;
  category: "pedagogy" | "preference" | "student_status" | "custom" | string;
  title: string;
  content: string;
  importance: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface AgentInfo {
  id: string;
  name: string;
  avatar: string;
  badge: string;
  description: string;
  greeting: string;
  sample_prompts: string[];
}

export interface CitationItem {
  citation_id: number;
  chunk_id: string;
  title: string;
  page_number: number;
  confidence_score: number;
  snippet: string;
}

export interface TraceStep {
  step_id: string;
  node_name: string;
  action_type: string;
  title: string;
  detail?: string;
  elapsed_ms?: number;
}

export interface MessageItem {
  id: string;
  role: "user" | "assistant";
  content: string;
  agent_name?: string;
  agent_avatar?: string;
  steps?: TraceStep[];
  citations?: CitationItem[];
  artifact?: any;
  plan_dag?: PlanDAG | null;
  sub_results?: SubAgentResultSchema[];
  /** SSE 流式进行中标记：空内容时渲染思考态，有内容时渲染打字光标 */
  streaming?: boolean;
  timestamp: string;
}

export interface PlanDAGNode {
  task_id: string;
  agent: string;
  input_summary: string;
  depends_on: string[];
  map_count: number;
  status: string; // PENDING | RUNNING | DONE | FAILED
}

export interface PlanDAG {
  nodes: PlanDAGNode[];
  schedule: string;
  total_tasks: number;
}

export interface SubAgentResultSchema {
  task_id: string;
  agent_name: string;
  scope_id: string;
  output_preview: string;
  citations: any[];
  artifacts: any[];
  confidence: number;
  token_used: number;
  status: string;
  error_message?: string | null;
}

// ---------------- Auth Helpers ----------------

export function getAuthToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("edu_token");
}

export function saveAuthToken(token: string) {
  if (typeof window !== "undefined") {
    localStorage.setItem("edu_token", token);
  }
}

export function clearAuthToken() {
  if (typeof window !== "undefined") {
    localStorage.removeItem("edu_token");
    localStorage.removeItem("edu_user");
  }
}

export function getStoredUser(): UserInfo | null {
  if (typeof window === "undefined") return null;
  const raw = localStorage.getItem("edu_user");
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

export function saveStoredUser(user: UserInfo) {
  if (typeof window !== "undefined") {
    localStorage.setItem("edu_user", JSON.stringify(user));
  }
}

function getAuthHeaders(): HeadersInit {
  const headers: Record<string, string> = {
    "Content-Type": "application/json"
  };
  const token = getAuthToken();
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  return headers;
}

// ---------------- Auth API ----------------

export async function loginUser(username: string, password: string):Promise<{ access_token: string; user: UserInfo }> {
  const res = await fetch(`${API_BASE_URL}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password })
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "登录失败，请检查账号密码");
  }
  const data = await res.json();
  saveAuthToken(data.access_token);
  
  // Fetch user profile immediately
  const user = await getCurrentUser(data.access_token);
  saveStoredUser(user);
  return { access_token: data.access_token, user };
}

export async function registerUser(payload: {
  username: string;
  email: string;
  password: string;
  full_name?: string;
  role?: string;
}): Promise<{ access_token: string; user: UserInfo }> {
  const res = await fetch(`${API_BASE_URL}/api/v1/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "注册失败，请更换用户名或稍后重试");
  }
  const data = await res.json();
  saveAuthToken(data.access_token);
  const user = await getCurrentUser(data.access_token);
  saveStoredUser(user);
  return { access_token: data.access_token, user };
}

export async function getCurrentUser(tokenOverride?: string): Promise<UserInfo> {
  const token = tokenOverride || getAuthToken();
  const res = await fetch(`${API_BASE_URL}/api/v1/auth/me`, {
    headers: {
      "Authorization": `Bearer ${token}`
    }
  });
  if (!res.ok) throw new Error("获取当前用户身份失败");
  return res.json();
}

// ---------------- Memory Management API ----------------

export async function fetchMemoryProfile(): Promise<UserProfile> {
  const res = await fetch(`${API_BASE_URL}/api/v1/memory/profile`, {
    headers: getAuthHeaders()
  });
  if (!res.ok) throw new Error("获取教学画像失败");
  return res.json();
}

export async function updateMemoryProfile(updates: Partial<UserProfile>): Promise<UserProfile> {
  const res = await fetch(`${API_BASE_URL}/api/v1/memory/profile`, {
    method: "PUT",
    headers: getAuthHeaders(),
    body: JSON.stringify(updates)
  });
  if (!res.ok) throw new Error("更新教学画像失败");
  return res.json();
}

export async function fetchMemoryItems(activeOnly: boolean = false, category?: string): Promise<MemoryItem[]> {
  const url = new URL(`${API_BASE_URL}/api/v1/memory/items`);
  if (activeOnly) url.searchParams.append("active_only", "true");
  if (category) url.searchParams.append("category", category);

  const res = await fetch(url.toString(), {
    headers: getAuthHeaders()
  });
  if (!res.ok) throw new Error("获取记忆列表失败");
  return res.json();
}

export async function createMemoryItem(item: {
  title: string;
  content: string;
  category?: string;
  importance?: number;
  is_active?: boolean;
}): Promise<MemoryItem> {
  const res = await fetch(`${API_BASE_URL}/api/v1/memory/items`, {
    method: "POST",
    headers: getAuthHeaders(),
    body: JSON.stringify(item)
  });
  if (!res.ok) throw new Error("创建记忆条目失败");
  return res.json();
}

export async function updateMemoryItem(itemId: string, updates: Partial<MemoryItem>): Promise<MemoryItem> {
  const res = await fetch(`${API_BASE_URL}/api/v1/memory/items/${itemId}`, {
    method: "PUT",
    headers: getAuthHeaders(),
    body: JSON.stringify(updates)
  });
  if (!res.ok) throw new Error("更新记忆条目失败");
  return res.json();
}

export async function deleteMemoryItem(itemId: string): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/api/v1/memory/items/${itemId}`, {
    method: "DELETE",
    headers: getAuthHeaders()
  });
  if (!res.ok) throw new Error("删除记忆条目失败");
}

export async function reflectMemories(dialogues: string[]): Promise<any> {
  const res = await fetch(`${API_BASE_URL}/api/v1/memory/reflect`, {
    method: "POST",
    headers: getAuthHeaders(),
    body: JSON.stringify({ recent_dialogues: dialogues })
  });
  if (!res.ok) throw new Error("智能反思提取记忆失败");
  return res.json();
}

// ---------------- Agent & Artifact APIs ----------------

export async function fetchAgentMatrix(): Promise<AgentInfo[]> {
  try {
    const res = await fetch(`${API_BASE_URL}/api/v1/agents/matrix`, {
      headers: getAuthHeaders()
    });
    if (!res.ok) throw new Error("Failed to fetch agent matrix");
    const data = await res.json();
    return data.agents;
  } catch (err) {
    console.warn("Using fallback agent matrix:", err);
    return [
      {
        id: "supervisor",
        name: "中台总控智能体",
        avatar: "🧭",
        badge: "智能中枢",
        description: "智能识别教学需求意图，自动分流至最适合的学科专家智能体",
        greeting: "您好，我是中台总控智能体，请告诉我您的教学需求。",
        sample_prompts: ["高中数学导数第二课时教学设计", "2024年高考物理受力分析试题命制"]
      },
      {
        id: "lesson_plan",
        name: "教案大师智能体",
        avatar: "📚",
        badge: "教学设计",
        description: "严格依循新课标，输出包含素养目标、教学环节、板书设计的导学案",
        greeting: "您好，我是教案大师，请把课题与课时要求告诉我。",
        sample_prompts: ["请为《导数的几何意义》设计一份45分钟精品公开课教案"]
      },
      {
        id: "academic_rag",
        name: "学术研读智能体",
        avatar: "🔬",
        badge: "文献综述",
        description: "基于 Milvus 2.4 与 Hybrid Search 深度阅读学术期刊论文，提供严密学术论证",
        greeting: "您好，我是学术研读智能体，请给出研究主题或文献方向。",
        sample_prompts: ["大模型赋能个性化自适应学习的有效性学术综述"]
      },
      {
        id: "exam_quiz",
        name: "命题组卷专家",
        avatar: "📝",
        badge: "自适应题库",
        description: "自适应命制梯度试题，自带标准评分细则、分步采分点与学生常见易错点剖析",
        greeting: "您好，我是命题组卷专家，请说明学段、学科与考查范围。",
        sample_prompts: ["命制3道高中数学导数单调性与极值典型压轴题"]
      },
      {
        id: "code_grader",
        name: "代码批改与沙箱实测专家",
        avatar: "💻",
        badge: "沙箱真机评测",
        description: "基于AST安全防护与隔离子进程沙箱，真机跑测学生代码用例，输出时空复杂度诊断与规范重构方案",
        greeting: "您好，我是代码批改专家，请粘贴需要批改的代码。",
        sample_prompts: [
          "批改二分查找算法代码并运行测试用例",
          "批改两数之和 Python 解法并评估复杂度"
        ]
      }
    ];
  }
}

export async function runAgentSync(
  message: string,
  agentType: string,
  kbIds?: string[],
  userId?: string,
  subAgentMode?: boolean,
  conversationId?: string
): Promise<any> {
  const res = await fetch(`${API_BASE_URL}/api/v1/agents/sync-run`, {
    method: "POST",
    headers: getAuthHeaders(),
    body: JSON.stringify({
      message,
      agent_type: agentType,
      kb_ids: kbIds,
      user_id: userId || "u-001",
      sub_agent_mode: subAgentMode || false,
      conversation_id: conversationId || null
    })
  });
  if (!res.ok) {
    let detail = "Agent execution failed";
    try {
      const errBody = await res.json();
      detail = errBody.detail || detail;
    } catch {
      // 忽略非 JSON 错误响应
    }
    throw new Error(detail);
  }
  return res.json();
}

// ---------------- Agent SSE 流式运行 ----------------
// 注意：浏览器原生 EventSource 不支持自定义请求头（Bearer 鉴权无法携带），
// 因此固定使用 fetch + ReadableStream 自行解析 SSE 文本帧。

export interface AgentStreamHandlers {
  /** 思维链步骤事件（trace），payload 含 title/action/node_name/step_id */
  onTrace?: (payload: any) => void;
  /** 流式 token：打字机逐块追加 */
  onToken?: (text: string, agent?: string) => void;
  /** 结构化产物事件（教案/试卷等 artifact） */
  onArtifact?: (artifact: any) => void;
  /** 任务计划更新（Orchestrator DAG） */
  onPlanUpdate?: (payload: any) => void;
  /** 终态成功：payload 为权威结果，前端据此一次性定稿消息 */
  onDone?: (payload: any) => void;
  /** 终态失败：message 已为可展示中文提示 */
  onError?: (message: string, payload?: any) => void;
}

export interface AgentStreamOptions {
  message: string;
  agentType: string;
  kbIds?: string[];
  userId?: string;
  subAgentMode?: boolean;
  conversationId?: string | null;
  /** 外部中断信号（切换会话/组件卸载时 abort） */
  signal?: AbortSignal;
}

/**
 * 异步 Agent 流式执行：
 * 1) POST /run 提交任务，立即拿到 task_id 与 stream_url；
 * 2) GET SSE 通道（Bearer 鉴权），解析 trace/token/artifact/done/error 事件。
 * 历史对话由后端在执行器开场与终态落库，前端只消费事件，不直接写历史。
 */
export async function runAgentStream(
  options: AgentStreamOptions,
  handlers: AgentStreamHandlers
): Promise<void> {
  const { message, agentType, kbIds, userId, subAgentMode, conversationId, signal } = options;

  // 1. 提交异步任务
  const submitRes = await fetch(`${API_BASE_URL}/api/v1/agents/run`, {
    method: "POST",
    headers: getAuthHeaders(),
    body: JSON.stringify({
      message,
      agent_type: agentType,
      kb_ids: kbIds,
      user_id: userId || "u-001",
      sub_agent_mode: subAgentMode || false,
      conversation_id: conversationId || null
    }),
    signal
  });

  if (!submitRes.ok) {
    let detail = "任务提交失败";
    try {
      const errBody = await submitRes.json();
      detail = errBody.detail || detail;
    } catch {
      // 忽略非 JSON 错误响应
    }
    throw new Error(detail);
  }

  const submitted = await submitRes.json();
  if (submitted.status === "DUPLICATE") {
    throw new Error(submitted.message || "该会话已有任务执行中，请稍候");
  }

  const streamUrl = submitted.stream_url;
  if (!streamUrl) {
    throw new Error("后端未返回 SSE 通道地址");
  }
  const streamEndpoint = streamUrl.startsWith("http")
    ? streamUrl
    : `${API_BASE_URL}${streamUrl.startsWith("/") ? "" : "/"}${streamUrl}`;

  // 2. 建立 SSE 长连接（fetch 流式读取，携带 Bearer）
  const sseRes = await fetch(streamEndpoint, {
    method: "GET",
    headers: getAuthHeaders(),
    signal
  });
  if (!sseRes.ok || !sseRes.body) {
    throw new Error(`实时通道连接失败（HTTP ${sseRes.status}）`);
  }

  const reader = sseRes.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  const dispatchFrame = (rawFrame: string): boolean => {
    // 单帧可能含多行 data:，拼接后 JSON.parse；忽略注释行（: keep-alive）与事件行
    const dataLines = rawFrame
      .split(/\r?\n/)
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).replace(/^ /, ""));
    if (dataLines.length === 0) return false;

    let evt: any;
    try {
      evt = JSON.parse(dataLines.join("\n"));
    } catch {
      return false;
    }

    const payload = evt.payload || {};
    switch (evt.event_type) {
      case "trace":
        handlers.onTrace?.(payload);
        return false;
      case "token":
        handlers.onToken?.(payload.text || "", payload.agent);
        return false;
      case "artifact":
        // artifact 事件的 payload 即产物本体
        handlers.onArtifact?.(payload);
        return false;
      case "plan_update":
        handlers.onPlanUpdate?.(payload);
        return false;
      case "done":
        handlers.onDone?.(payload);
        return true;
      case "error":
        handlers.onError?.(payload.error || "任务执行失败", payload);
        return true;
      default:
        return false;
    }
  };

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE 事件以空行（\n\n）分隔；最后一段可能是半帧，留在 buffer 等待后续 chunk
      let sepIndex: number;
      while ((sepIndex = buffer.indexOf("\n\n")) !== -1) {
        const rawFrame = buffer.slice(0, sepIndex);
        buffer = buffer.slice(sepIndex + 2);
        if (rawFrame.trim()) {
          const terminal = dispatchFrame(rawFrame);
          if (terminal) {
            try {
              await reader.cancel();
            } catch {
              // 终态后取消读取，忽略取消异常
            }
            return;
          }
        }
      }
    }

    // 流正常结束但未见 done/error（代理截断等）：按异常提示，避免 loading 永久挂起
    handlers.onError?.("实时通道提前关闭，请重试或查看历史对话");
  } finally {
    try {
      reader.releaseLock();
    } catch {
      // 重复释放忽略
    }
  }
}

// ==================== 历史对话 ====================

export interface ConversationMeta {
  id: string;
  user_id: string;
  title: string;
  agent_type: string;
  thread_id: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface StoredMessage {
  id: string;
  session_id: string;
  role: string;
  content: string;
  thought_process?: string | null;
  citations?: any[];
  extra?: {
    plan_dag?: any;
    sub_results?: any[];
    artifact?: any;
    artifact_type?: string;
    agent_name?: string;
    agent_avatar?: string;
  };
  created_at: string;
}

/** 列出指定 agent 的历史对话 */
export async function fetchConversations(
  agentType: string,
  userId?: string
): Promise<ConversationMeta[]> {
  const params = new URLSearchParams({
    agent_type: agentType,
    user_id: userId || "u-001"
  });
  const res = await fetch(`${API_BASE_URL}/api/v1/conversations?${params}`, {
    headers: getAuthHeaders()
  });
  if (!res.ok) throw new Error("获取历史对话失败");
  const data = await res.json();
  return data.conversations || [];
}

/** 获取某条历史对话的完整内容（恢复窗口用） */
export async function fetchConversationDetail(
  conversationId: string
): Promise<{ session: ConversationMeta; messages: StoredMessage[] }> {
  const res = await fetch(
    `${API_BASE_URL}/api/v1/conversations/${conversationId}`,
    { headers: getAuthHeaders() }
  );
  if (!res.ok) throw new Error("获取对话详情失败");
  return res.json();
}

/** 删除一条历史对话 */
export async function deleteConversation(conversationId: string): Promise<void> {
  const res = await fetch(
    `${API_BASE_URL}/api/v1/conversations/${conversationId}`,
    { method: "DELETE", headers: getAuthHeaders() }
  );
  if (!res.ok) throw new Error("删除对话失败");
}

export async function exportWordDocument(title: string, markdownContent: string): Promise<Blob> {
  const res = await fetch(`${API_BASE_URL}/api/v1/artifacts/export/word`, {
    method: "POST",
    headers: getAuthHeaders(),
    body: JSON.stringify({ title, markdown_content: markdownContent })
  });
  if (!res.ok) throw new Error("Export failed");
  return res.blob();
}

/** 导出教学成果为 PDF 文档 */
export async function exportPdfDocument(title: string, markdownContent: string): Promise<Blob> {
  const res = await fetch(`${API_BASE_URL}/api/v1/artifacts/export/pdf`, {
    method: "POST",
    headers: getAuthHeaders(),
    body: JSON.stringify({ title, markdown_content: markdownContent })
  });
  if (!res.ok) throw new Error("Export failed");
  return res.blob();
}
