const DEFAULT_API_URL = "http://127.0.0.1:8000";

/**
 * Base URL of the AnnaData backend, normalised.
 *
 * Deploy dashboards expose a service's address as a bare hostname rather than a
 * URL, so REACT_APP_API_URL often arrives as "annadata-backend.onrender.com"
 * with no scheme. Without a scheme the browser treats it as a relative path and
 * every request 404s against the frontend's own origin.
 */
export function getApiUrl() {
  const raw = (process.env.REACT_APP_API_URL || DEFAULT_API_URL).trim();
  const withoutTrailingSlash = raw.replace(/\/+$/, "");

  if (/^https?:\/\//i.test(withoutTrailingSlash)) {
    return withoutTrailingSlash;
  }
  // localhost keeps http so local development works without a certificate.
  const scheme = /^(localhost|127\.0\.0\.1)(:|$)/.test(withoutTrailingSlash)
    ? "http://"
    : "https://";
  return scheme + withoutTrailingSlash;
}

export default getApiUrl;
