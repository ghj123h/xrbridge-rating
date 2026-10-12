// Shared by the rating site's ES modules and the play app's deferred scripts.
globalThis.PlayerName = (() => {
  const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));

  function formatRating(value, digits = 2) {
    return Number.isFinite(value)
      ? value.toLocaleString("zh-CN", {useGrouping: false, minimumFractionDigits: digits, maximumFractionDigits: digits})
      : "—";
  }

  function markup(name, info, {showRating = false, external = false, tag = "span",
    className = "player-name", ratingClass = "player-rating"} = {}) {
    const mature = info?.provisional === false || info?.provisional === 0;
    const attributes = `${className ? `class="${escapeHtml(className)}" ` : ""}style="color:${escapeHtml(info?.color || "#000000")}${mature ? "" : ";font-weight:400"}"`;
    const username = info?.profile_url
      ? `<a ${attributes} href="${escapeHtml(info.profile_url)}"${external ? ' target="_blank" rel="noopener noreferrer"' : ""}>${escapeHtml(name)}</a>`
      : `<${tag} ${attributes}>${escapeHtml(name)}</${tag}>`;
    return showRating
      ? `${username}<span class="${escapeHtml(ratingClass)}">（${formatRating(info?.rating)}）</span>`
      : username;
  }

  return {markup, formatRating};
})();
