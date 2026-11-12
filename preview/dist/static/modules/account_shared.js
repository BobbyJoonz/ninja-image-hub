export const DISPLAY_HINTS = {
  xvfb: "This server renders the browser on a virtual display (Xvfb): there is no window on a desk. Use method 1 or 3.",
  missing:
    "No browser display is available on this host. Install xvfb or run the panel with a real DISPLAY before starting workers.",
  x11: "The worker window is on the server's X display; use VNC to see it, or method 1 / 3.",
  desktop: "The worker Chrome window is open on this machine.",
};


export function planSummary(plan) {
  if (!plan) return "Detected plan: —";
  const parts = [`Detected plan: ${plan.label || plan.plan || "unknown"}`];
  if (plan.active === false) parts.push("no active subscription");
  if (plan.days_left != null) parts.push(`${plan.days_left} day(s) left`);
  if (plan.will_renew) parts.push("will renew");
  if (plan.session_expires) parts.push(`browser session until ${plan.session_expires.slice(0, 10)}`);
  return parts.join(" · ");
}
