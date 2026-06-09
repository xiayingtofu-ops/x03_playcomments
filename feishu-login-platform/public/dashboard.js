async function loadProfile() {
  const response = await fetch("/api/auth/me");
  if (!response.ok) {
    window.location.href = "/";
    return;
  }

  const { user } = await response.json();
  document.querySelector("#name").textContent = user.name || "飞书用户";
  document.querySelector("#email").textContent = user.email || "未返回邮箱";
  document.querySelector("#openId").textContent = user.openId || "-";
  document.querySelector("#unionId").textContent = user.unionId || "-";
  document.querySelector("#tenantKey").textContent = user.tenantKey || "-";
  document.querySelector("#rawUser").textContent = JSON.stringify(user, null, 2);

  const avatar = document.querySelector("#avatar");
  if (user.avatarUrl) {
    avatar.src = user.avatarUrl;
  } else {
    avatar.removeAttribute("src");
    avatar.classList.add("empty-avatar");
  }
}

document.querySelector("#logoutButton").addEventListener("click", async () => {
  await fetch("/api/auth/logout", { method: "POST" });
  window.location.href = "/";
});

loadProfile();
