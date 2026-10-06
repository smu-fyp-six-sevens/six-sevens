export async function assertOk(response: Response, message: string): Promise<void> {
  if (response.ok) return;
  const detail = await response.text();
  throw new Error(`${message} (${response.status}): ${detail}`);
}
