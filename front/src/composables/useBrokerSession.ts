import { readonly, ref } from 'vue'

const instanceId = ref<string | null>(null)
const epoch = ref(0)
const restarted = ref(false)

export class SessionChanged extends Error {}

/** Observe only responses from the epoch in which their request began. */
export function useBrokerSession() {
  function observe(id: string | null | undefined, expected: number) {
    if (expected !== epoch.value) throw new SessionChanged()
    if (!id) return
    if (instanceId.value && instanceId.value !== id) {
      instanceId.value = id
      restarted.value = true
      epoch.value++
      throw new SessionChanged()
    }
    instanceId.value = id
  }
  return {
    instanceId: readonly(instanceId),
    epoch: readonly(epoch),
    restarted: readonly(restarted),
    observe,
  }
}
