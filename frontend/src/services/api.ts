import axios from 'axios'
import type { ChatRequest, ChatResult, DemoProfile, Health } from '../types'

const http = axios.create({ baseURL: '/api', timeout: 90_000, headers: { 'Content-Type': 'application/json' } })
export class ApiError extends Error {
  constructor(message: string, public retryable = false, public conflict = false) { super(message) }
}
export function describeError(error: unknown): ApiError {
  if (error instanceof ApiError) return error
  if (!axios.isAxiosError(error)) return new ApiError('暂时无法读取服务回复，请重新尝试。', true)
  const status = error.response?.status
  if (status === 409) return new ApiError('这次请求与会话状态发生冲突。请开始新对话后重新描述需求。', false, true)
  if (status === 403) return new ApiError('当前演示仅支持合成画像，请选择演示用户。')
  if (status === 404) return new ApiError('会话或演示用户不存在，请开始新对话。', false, true)
  if (status === 422) return new ApiError('输入未通过校验，请检查内容后重新发送。')
  if (status === 429) return new ApiError('当前请求较多，请稍后重试。', true)
  if (status === 502 || status === 503) return new ApiError('AI 服务暂时不可用。你的输入已保留，可以重试。', true)
  return new ApiError('连接未完成，请检查服务后重试。重试会保留本次请求。', true)
}
export async function getProfiles(): Promise<DemoProfile[]> {
  const { data } = await http.get('/demo/profiles', { timeout: 15_000 })
  if (data.data_scope !== 'synthetic' || !Array.isArray(data.profiles) || !data.profiles.length) throw new ApiError('没有可用的合成演示画像。')
  return data.profiles
}
export async function getHealth(): Promise<Health> {
  return (await http.get<Health>('/health', { timeout: 15_000 })).data
}
export async function postChat(request: ChatRequest): Promise<ChatResult> {
  const { data } = await http.post<ChatResult>('/chat', request)
  if (data.schema_version !== '2.0' || !['ok', 'clarification_required', 'no_feasible_menu'].includes(data.status)
    || !data.conversation_state?.session_id || data.conversation_state.user_id !== request.user_id
    || !Array.isArray(data.menu) || !Array.isArray(data.clarification_questions)) {
    throw new ApiError('回复格式暂不兼容，请重试或开始新对话。', true)
  }
  return data
}
