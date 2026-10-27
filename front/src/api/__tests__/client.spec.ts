import { afterEach, describe, expect, it, vi } from 'vitest'
import { getJson } from '../client'

afterEach(() => vi.unstubAllGlobals())

describe('只读API请求', () => {
  it('保留后端错误码和请求ID', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            request_id: 'req-test',
            instance_id: 'inst-test',
            reason_code: 'AUDIT_UNAVAILABLE',
            message: '审计不可用',
          }),
          { status: 503 },
        ),
      ),
    )
    await expect(getJson('/health')).rejects.toMatchObject({
      status: 503,
      reasonCode: 'AUDIT_UNAVAILABLE',
      requestId: 'req-test',
      instanceId: 'inst-test',
    })
  })

  it('将代理返回的HTML识别为接口响应异常', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response('<html>error</html>', { status: 502 })),
    )
    await expect(getJson('/tasks')).rejects.toMatchObject({ reasonCode: 'INVALID_RESPONSE' })
  })

  it('保留读取响应体期间的取消错误', async () => {
    const controller = new AbortController()
    const cancelled = new DOMException('请求已取消', 'AbortError')
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => {
          controller.abort()
          throw cancelled
        },
      }),
    )
    await expect(getJson('/health', controller.signal)).rejects.toBe(cancelled)
  })
})
