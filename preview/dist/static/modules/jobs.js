export function filterJobs(jobs, status = "all", query = "", accountNames = {}) {
  const q = String(query || "").trim().toLowerCase();
  return (jobs || []).filter((j) => {
    if (status === "active" && !["queued", "running"].includes(j.status))
      return false;
    if (status === "completed" && j.status !== "completed") return false;
    if (status === "problems") {
      const isProblem = ["failed", "unknown", "rate_limited"].includes(j.status);
      // A reconciled unknown job is closed; it should not keep asking for attention.
      const closed = j.status === "unknown" && Boolean(j.resolution);
      if (!isProblem || closed) return false;
    }
    if (!q) return true;
    const haystack = [j.prompt, j.id, j.status, accountNames[j.account_id] || ""]
      .join(" ")
      .toLowerCase();
    return haystack.includes(q);
  });
}
