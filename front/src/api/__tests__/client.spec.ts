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
    })
  })

  it('将代理返回的HTML识别为接口响应异常', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response('<html>error</html>', { status: 502 })),
    )
    await expect(getJson('/tasks')).rejects.toMatchObject({ reasonCode: 'INVALID_RESPONSE' })
  })
})
