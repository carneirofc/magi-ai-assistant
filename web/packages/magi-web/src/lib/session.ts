// Operator session: a signed, httpOnly cookie. There are no user accounts — a
// single password (ADMIN_PASSWORD) unlocks the tool, and this cookie is the proof.
//
// Signed with Web Crypto HMAC so the same verify runs in both the edge middleware
// and the node route handlers. The cookie carries only an expiry; its value is
// meaningless without a valid signature, and the secret never leaves the server.

const COOKIE_NAME = "magi_admin_session";
const TTL_SECONDS = 60 * 60 * 12; // 12h

function secret(): string {
  const s = process.env.SESSION_SECRET;
  if (!s) throw new Error("SESSION_SECRET is not set");
  return s;
}

const enc = new TextEncoder();

function b64url(bytes: ArrayBuffer): string {
  const bin = String.fromCharCode(...new Uint8Array(bytes));
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function hmac(data: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    enc.encode(secret()),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  return b64url(await crypto.subtle.sign("HMAC", key, enc.encode(data)));
}

/** A signed session token valid for TTL_SECONDS. */
export async function signSession(): Promise<string> {
  const exp = String(Math.floor(Date.now() / 1000) + TTL_SECONDS);
  const sig = await hmac(exp);
  return `${exp}.${sig}`;
}

/** True when `token` is well-formed, correctly signed, and unexpired. */
export async function verifySession(token: string | undefined): Promise<boolean> {
  if (!token) return false;
  const [exp, sig] = token.split(".");
  if (!exp || !sig) return false;
  const expected = await hmac(exp);
  // Length-equal compare; values are our own base64url, so a plain === is fine here.
  if (sig !== expected) return false;
  return Number(exp) > Math.floor(Date.now() / 1000);
}

/** Whether operator auth is enabled. When ADMIN_PASSWORD is unset or empty the
 * tool is open — no password prompt, no session gate. There is no default
 * password: an unset value means open, never a fallback secret. */
export function authEnabled(): boolean {
  return !!process.env.ADMIN_PASSWORD;
}

export const session = {
  cookieName: COOKIE_NAME,
  ttlSeconds: TTL_SECONDS,
};
