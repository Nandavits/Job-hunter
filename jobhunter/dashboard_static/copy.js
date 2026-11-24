// "Copy" buttons next to your answers: click, then paste into the application form.
document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-copy]");
  if (!b) return;
  const text = document.getElementById(b.dataset.copy).innerText;
  try { await navigator.clipboard.writeText(text); } catch (_) {
    const r = document.createRange(); r.selectNodeContents(document.getElementById(b.dataset.copy));
    getSelection().removeAllRanges(); getSelection().addRange(r); document.execCommand("copy");
  }
  const old = b.textContent; b.textContent = "Copied"; setTimeout(() => (b.textContent = old), 1200);
});
