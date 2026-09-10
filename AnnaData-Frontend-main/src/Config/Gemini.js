import { getApiUrl } from "./api";

const GEOLOCATION_TIMEOUT_MS = 1000;

/**
 * Ask the browser for coordinates. Resolves to null rather than rejecting, so a
 * location is optional: waiting for GPS or a permission prompt must not hold up
 * a question for more than one second.
 */
async function getCoordinates() {
  if (!("geolocation" in navigator)) {
    console.warn("Geolocation is not supported by this browser.");
    return null;
  }

  try {
    const position = await new Promise((resolve) => {
      // Browser GPS timeouts do not cover time spent awaiting permission.
      const timer = setTimeout(() => resolve(null), GEOLOCATION_TIMEOUT_MS);
      const finish = (value) => {
        clearTimeout(timer);
        resolve(value);
      };
      try {
        navigator.geolocation.getCurrentPosition(finish, () => finish(null), {
          enableHighAccuracy: false,
          timeout: GEOLOCATION_TIMEOUT_MS,
          maximumAge: 300000,
        });
      } catch (error) {
        finish(null);
      }
    });
    if (!position) return null;
    return {
      latitude: position.coords.latitude,
      longitude: position.coords.longitude,
    };
  } catch (error) {
    console.warn("Geolocation unavailable, proceeding without it:", error.message);
    return null;
  }
}

async function readProgressStream(response, onProgress, onText) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) throw new Error("Connection ended before the answer arrived. Please retry.");
      buffer += decoder.decode(value, { stream: true });
      if (buffer.length > 1024 * 1024) throw new Error("Response exceeded the size limit.");
      let boundary;
      while ((boundary = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const data = frame.split("\n").filter(line => line.startsWith("data:"))
          .map(line => line.slice(5).trimStart()).join("\n");
        if (!data) continue;
        const event = JSON.parse(data);
        if (event.type === "status") onProgress(event.stage);
        if (event.type === "delta" && typeof event.text === "string") onText(event.text);
        if (event.type === "error") throw new Error("Agent is temporarily unavailable. Please retry.");
        if (event.type === "result" && typeof event.answer === "string") return event.answer;
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

async function run(prompt, history, onProgress = () => {}, onText = () => {}) {
  const requestBody = { query: prompt, history };

  const coords = await getCoordinates();
  if (coords) {
    requestBody.latitude = coords.latitude;
    requestBody.longitude = coords.longitude;
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 180000);
  let receivedText = false;
  try {
    const response = await fetch(`${getApiUrl()}/agent/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "text/event-stream" },
      body: JSON.stringify(requestBody),
      signal: controller.signal,
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

    if (response.headers?.get("content-type")?.includes("text/event-stream")) {
      return await readProgressStream(response, onProgress, text => {
        receivedText = true;
        onText(text);
      });
    }
    const data = await response.json();
    return data.answer || "No response from agent.";
  } catch (error) {
    console.error("Error calling agent API:", error);
    if (receivedText) return "The response was interrupted before completion. Please retry.";
    if (error.name === "AbortError") return "The request timed out. Please try again shortly.";
    return `Sorry, could not reach the AnnaData service. (${error.message})`;
  } finally {
    clearTimeout(timeout);
  }
}

export default run;
