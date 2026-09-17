export const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";
export async function fetchJson<T>(path: string, options?: RequestInit): Promise<T> {
  let response: Response;
  try { response = await fetch(`${apiBase}${path}`, {
    ...options, signal: options?.signal ?? AbortSignal.timeout(12000),
    headers: { "Content-Type": "application/json", ...options?.headers }
  }); } catch (error) {
    if (options?.signal?.aborted) throw error;
    throw new Error("无法连接后台 API，请检查服务是否已启动后重试。");
  }
  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    throw new Error(typeof detail?.detail === "string" ? detail.detail : Array.isArray(detail?.detail) ? detail.detail.map((x: {loc?: string[]; msg: string}) => `${x.loc?.join(".")}: ${x.msg}`).join("；") : `请求失败 (${response.status})`);
  }
  const value = response.status === 204 ? undefined : await response.json();
  if (options?.method && options.method !== "GET") {
    window.dispatchEvent(new Event("personal-os-change"));
    if (typeof BroadcastChannel !== "undefined") {
      const channel = new BroadcastChannel("personal-os-change");
      channel.postMessage("refresh"); channel.close();
    }

  }
  return value as T;
}
