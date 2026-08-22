const accessTokenKey = "access_token"

export function getAccessToken(): string | null {
  return localStorage.getItem(accessTokenKey)
}

export function setAccessToken(token: string): void {
  localStorage.setItem(accessTokenKey, token)
}

export function clearAccessToken(): void {
  localStorage.removeItem(accessTokenKey)
}

export function isLoggedIn(): boolean {
  return getAccessToken() !== null
}
