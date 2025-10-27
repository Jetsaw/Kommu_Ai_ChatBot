const TOKEN_KEY = "agentToken";
const LEGACY_TOKEN_KEY = "agent_token";

/**
 * Read the stored agent token, migrating any legacy value to the new key.
 * @returns {string}
 */
export function readStoredToken() {
  const current = localStorage.getItem(TOKEN_KEY);
  if (current && current.trim()) {
    localStorage.removeItem(LEGACY_TOKEN_KEY);
    return current;
  }

  const legacy = localStorage.getItem(LEGACY_TOKEN_KEY);
  if (legacy && legacy.trim()) {
    localStorage.setItem(TOKEN_KEY, legacy);
    localStorage.removeItem(LEGACY_TOKEN_KEY);
    return legacy;
  }

  return "";
}

/**
 * Persist 
 * @param {string} rawToken
 * @returns {string} 
 */
export function writeStoredToken(rawToken) {
  const normalized = (rawToken ?? "").trim();
  if (normalized) {
    localStorage.setItem(TOKEN_KEY, normalized);
  } else {
    localStorage.removeItem(TOKEN_KEY);
  }

  localStorage.removeItem(LEGACY_TOKEN_KEY);
  return normalized;
}


export function clearStoredToken() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(LEGACY_TOKEN_KEY);
}