import type { UserProfile } from "./types";

const SESSION_KEY = "vindex:authSession";
const PKCE_KEY = "vindex:pkceVerifier";

export const AUTH_ENABLED = import.meta.env.VITE_AUTH_ENABLED === "true";

const COGNITO_DOMAIN = import.meta.env.VITE_COGNITO_DOMAIN ?? "";
const COGNITO_CLIENT_ID = import.meta.env.VITE_COGNITO_CLIENT_ID ?? "";
const REDIRECT_URI = import.meta.env.VITE_COGNITO_REDIRECT_URI || window.location.origin + "/";

export type AuthSession = {
  idToken: string;
  accessToken: string;
  expiresAt: number;
  profile: UserProfile;
};

function base64Url(bytes: Uint8Array) {
  let binary = "";
  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte);
  });

  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function randomString(length = 64) {
  const bytes = new Uint8Array(length);
  crypto.getRandomValues(bytes);
  return base64Url(bytes);
}

async function sha256Base64Url(value: string) {
  const encoded = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", encoded);
  return base64Url(new Uint8Array(digest));
}

function decodeJwtPayload(token: string) {
  const [, payload] = token.split(".");
  if (!payload) return {};

  const normalized = payload.replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized.padEnd(normalized.length + ((4 - (normalized.length % 4)) % 4), "=");
  return JSON.parse(atob(padded));
}

function profileFromIdToken(idToken: string): UserProfile {
  const claims = decodeJwtPayload(idToken) as Record<string, string>;

  return {
    auth_required: true,
    user_id: claims.sub,
    email: claims.email ?? null,
    name: claims.name ?? claims["cognito:username"] ?? null,
    picture_url: claims.picture ?? null
  };
}

function ensureCognitoConfig() {
  if (!COGNITO_DOMAIN || !COGNITO_CLIENT_ID) {
    throw new Error("Cognito frontend settings are missing");
  }
}

export function getStoredSession() {
  const raw = localStorage.getItem(SESSION_KEY);
  if (!raw) return null;

  try {
    const session = JSON.parse(raw) as AuthSession;
    if (Date.now() >= session.expiresAt) {
      localStorage.removeItem(SESSION_KEY);
      return null;
    }

    return session;
  } catch {
    localStorage.removeItem(SESSION_KEY);
    return null;
  }
}

export function storeSession(session: AuthSession) {
  localStorage.setItem(SESSION_KEY, JSON.stringify(session));
}

export async function signIn() {
  ensureCognitoConfig();

  const verifier = randomString();
  const challenge = await sha256Base64Url(verifier);
  sessionStorage.setItem(PKCE_KEY, verifier);

  const params = new URLSearchParams({
    client_id: COGNITO_CLIENT_ID,
    code_challenge: challenge,
    code_challenge_method: "S256",
    redirect_uri: REDIRECT_URI,
    response_type: "code",
    scope: "openid email profile"
  });

  window.location.href = `${COGNITO_DOMAIN}/oauth2/authorize?${params.toString()}`;
}

export async function handleAuthCallback() {
  if (!AUTH_ENABLED) return getStoredSession();

  const params = new URLSearchParams(window.location.search);
  const code = params.get("code");
  if (!code) return getStoredSession();

  ensureCognitoConfig();

  const verifier = sessionStorage.getItem(PKCE_KEY);
  if (!verifier) {
    throw new Error("Missing login verifier. Start login again.");
  }

  const response = await fetch(`${COGNITO_DOMAIN}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      client_id: COGNITO_CLIENT_ID,
      code,
      code_verifier: verifier,
      grant_type: "authorization_code",
      redirect_uri: REDIRECT_URI
    })
  });

  if (!response.ok) {
    throw new Error("Could not complete login");
  }

  const tokens = await response.json();
  const idToken = tokens.id_token as string;
  const accessToken = tokens.access_token as string;
  const expiresIn = Number(tokens.expires_in ?? 3600);
  const session = {
    idToken,
    accessToken,
    expiresAt: Date.now() + expiresIn * 1000,
    profile: profileFromIdToken(idToken)
  };

  storeSession(session);
  sessionStorage.removeItem(PKCE_KEY);
  window.history.replaceState({}, document.title, window.location.pathname);

  return session;
}

export function signOut() {
  localStorage.removeItem(SESSION_KEY);
  sessionStorage.removeItem(PKCE_KEY);

  if (AUTH_ENABLED && COGNITO_DOMAIN && COGNITO_CLIENT_ID) {
    const params = new URLSearchParams({
      client_id: COGNITO_CLIENT_ID,
      logout_uri: REDIRECT_URI
    });
    window.location.href = `${COGNITO_DOMAIN}/logout?${params.toString()}`;
    return;
  }

  window.location.reload();
}
