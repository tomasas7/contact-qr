// Ask for confirmation before submitting forms marked with data-confirm (e.g. delete).
// Kept in a static file because the Content-Security-Policy forbids inline scripts.
document.addEventListener("submit", (event) => {
  const message = event.target.dataset && event.target.dataset.confirm;
  if (message && !window.confirm(message)) event.preventDefault();
});
