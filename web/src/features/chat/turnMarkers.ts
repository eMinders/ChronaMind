/**
 * The moment each user message was sent, kept for Time Travel (WEB-09): the
 * memory write for a message lands a few seconds *after* its marker, so a
 * marker is an instant when the previous fact was still the current one.
 */

export interface TurnMarker {
  /** ISO-8601 instant of sending. */
  at: string;
  text: string;
}

export const MAX_MARKERS = 50;

function key(userKey: string): string {
  return `chronamind.markers.${userKey}`;
}

export function readMarkers(userKey: string): TurnMarker[] {
  try {
    const raw = sessionStorage.getItem(key(userKey));
    return raw ? (JSON.parse(raw) as TurnMarker[]) : [];
  } catch {
    return [];
  }
}

export function recordMarker(
  userKey: string,
  text: string,
  at: Date = new Date(),
): TurnMarker[] {
  const markers = [
    ...readMarkers(userKey),
    { at: at.toISOString(), text },
  ].slice(-MAX_MARKERS);
  try {
    sessionStorage.setItem(key(userKey), JSON.stringify(markers));
  } catch {
    // Storage blocked: Time Travel falls back to its manual date input.
  }
  return markers;
}
