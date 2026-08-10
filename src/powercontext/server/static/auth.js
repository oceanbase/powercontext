const tokenKey = "powercontext.server.token";

export function readServerToken() {
  try {
    return sessionStorage.getItem(tokenKey);
  } catch (error) {
    return null;
  }
}

export function storeServerToken(token) {
  try {
    sessionStorage.setItem(tokenKey, token);
  } catch (error) {
    // Authentication still applies to the current request when storage is unavailable.
  }
}

export function clearServerToken() {
  try {
    sessionStorage.removeItem(tokenKey);
  } catch (error) {
    // The current page can still return to its signed-out state.
  }
}

export function fetchWithBearer(resource, token, options = {}) {
  const headers = new Headers(options.headers);
  headers.set("Authorization", `Bearer ${token}`);
  return fetch(resource, {...options, headers, cache: options.cache || "no-store"});
}
