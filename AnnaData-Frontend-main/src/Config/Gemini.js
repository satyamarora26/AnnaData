import { getApiUrl } from "./api";

const GEOLOCATION_TIMEOUT_MS = 8000;

/**
 * Ask the browser for coordinates. Resolves to null rather than rejecting, so a
 * denied or unavailable location never blocks the request - the backend simply
 * answers without location context.
 */
async function getCoordinates() {
  if (!("geolocation" in navigator)) {
    console.warn("Geolocation is not supported by this browser.");
    return null;
  }

  try {
    const position = await new Promise((resolve, reject) => {
      navigator.geolocation.getCurrentPosition(resolve, reject, {
        enableHighAccuracy: true,
        timeout: GEOLOCATION_TIMEOUT_MS,
        maximumAge: 300000,
      });
    });
    return {
      latitude: position.coords.latitude,
      longitude: position.coords.longitude,
    };
  } catch (error) {
    console.warn("Geolocation unavailable, proceeding without it:", error.message);
    return null;
  }
}

async function run(prompt, history) {
  const requestBody = { query: prompt, history };

  const coords = await getCoordinates();
  if (coords) {
    requestBody.latitude = coords.latitude;
    requestBody.longitude = coords.longitude;
  }

  try {
    const response = await fetch(`${getApiUrl()}/agent`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestBody),
    });

    if (!response.ok) {
      // The backend now returns a real status code with a {detail} body.
      let detail = response.statusText;
      try {
        const body = await response.json();
        detail = body.detail || body.error || detail;
      } catch (e) {
        /* non-JSON error body */
      }
      throw new Error(`API error ${response.status}: ${detail}`);
    }

    const data = await response.json();
    return data.answer || "No response from agent.";
  } catch (error) {
    console.error("Error calling agent API:", error);
    return `Sorry, could not reach the AnnaData service. (${error.message})`;
  }
}

export default run;
