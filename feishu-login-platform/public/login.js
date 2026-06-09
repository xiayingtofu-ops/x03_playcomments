async function redirectIfLoggedIn() {
  const response = await fetch("/api/auth/me");
  if (response.ok) {
    window.location.href = "/dashboard.html";
  }
}

document.querySelector("#loginButton").addEventListener("click", () => {
  window.location.href = "/api/auth/login";
});

redirectIfLoggedIn();
