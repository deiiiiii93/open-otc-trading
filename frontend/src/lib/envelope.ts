import type { Envelope } from "../types";

export function envelopeBadgeLabel(env: Envelope): string {
  switch (env) {
    case "pet_page":
      return "Pet";
    case "pet_diagnostic":
      return "Pet · diagnostic";
    case "desk_workflow":
      return "Desk";
    case "desk_async":
      return "Desk · async";
  }
}
