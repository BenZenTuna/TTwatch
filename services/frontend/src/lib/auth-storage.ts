const ACCESS_TOKEN_KEY = "ttwatch_access_token";
const REFRESH_TOKEN_KEY = "ttwatch_refresh_token";

function isClient(): boolean {
  return typeof window !== "undefined";
}

export function getAccessToken(): string | null {
  if (!isClient()) return null;
  return localStorage.getItem(ACCESS_TOKEN_KEY);
}

export function getRefreshToken(): string | null {
  if (!isClient()) return null;
  return localStorage.getItem(REFRESH_TOKEN_KEY);
}

export function setTokens(accessToken: string, refreshToken: string): void {
  if (!isClient()) return;
  localStorage.setItem(ACCESS_TOKEN_KEY, accessToken);
  localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken);
}

export function clearTokens(): void {
  if (!isClient()) return;
  localStorage.removeItem(ACCESS_TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
}

export function isTokenExpired(token: string): boolean {
  try {
    const payload = JSON.parse(atob(token.split(".")[1]));
    // Add 10-second buffer so we refresh slightly before actual expiry
    return payload.exp * 1000 < Date.now() + 10_000;
  } catch {
    return true;
  }
}

export function hasValidToken(): boolean {
  const token = getAccessToken();
  return token !== null && !isTokenExpired(token);
}
