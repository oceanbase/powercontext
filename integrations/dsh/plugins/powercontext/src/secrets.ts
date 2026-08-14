const SECRET_MARKERS = ['sk-', 'api_key', 'BEGIN PRIVATE']

export function containsSecret(text: string): boolean {
  return SECRET_MARKERS.some((marker) => text.includes(marker))
}
