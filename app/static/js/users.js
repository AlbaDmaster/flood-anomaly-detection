// Users page (administrators only): add accounts with any role, edit name,
// phone and role, and deactivate or reactivate accounts. The server enforces
// every rule; this page only mirrors them (e.g. no self-demotion).
const me = Number(document.getElementById("users-page").dataset.me);
const dialog = document.getElementById("user-dialog");
let users = [];
let editingId = null;

function show(id, message) {
  const box = document.getElementById(id);
  box.textContent = message || "";
  box.hidden = !message;
}

function capitalise(text) {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

async function loadUsers(focusId) {
  try {
    users = (await api("/api/admin/users")).users;
  } catch (error) {
    show("list-error", error.message);
    return;
  }
  show("list-error", "");
  const body = document.getElementById("user-rows");
  body.replaceChildren();
  for (const user of users) {
    const row = document.createElement("tr");
    if (!user.is_active) row.className = "inactive";
    const name = cell(user.user_id === me ? `${user.name} (you)` : user.name);
    const status = cell(user.is_active ? "Active" : "Deactivated", user.is_active ? "" : "muted");
    const action = document.createElement("td");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "link-button";
    button.textContent = "Edit";
    button.dataset.userId = user.user_id;
    button.setAttribute("aria-label", `Edit ${user.name}`);
    button.addEventListener("click", () => openEditor(user.user_id));
    action.append(button);
    row.append(name, cell(user.email), cell(user.phone || "–"), cell(capitalise(user.role)), status, action);
    body.append(row);
  }
  const active = users.filter((u) => u.is_active).length;
  document.getElementById("user-count").textContent =
    `${users.length} ${users.length === 1 ? "user" : "users"}, ${active} active`;
  if (focusId) document.querySelector(`#user-rows button[data-user-id="${focusId}"]`)?.focus();
}

function openEditor(userId) {
  const user = users.find((u) => u.user_id === userId);
  if (!user) return;
  editingId = userId;
  const isSelf = userId === me;
  document.getElementById("user-dialog-title").textContent = `Edit ${user.name}`;
  document.getElementById("edit-email").textContent = user.email;
  document.getElementById("edit-name").value = user.name;
  document.getElementById("edit-phone").value = user.phone || "";
  document.getElementById("edit-role").value = user.role;
  document.getElementById("edit-active").checked = user.is_active;
  document.getElementById("edit-role").disabled = isSelf;
  document.getElementById("edit-active").disabled = isSelf;
  document.getElementById("self-note").hidden = !isSelf;
  show("edit-error", "");
  dialog.showModal();
}

document.getElementById("user-dialog-close").addEventListener("click", () => dialog.close());
dialog.addEventListener("close", () => {
  const id = editingId;
  editingId = null;
  document.querySelector(`#user-rows button[data-user-id="${id}"]`)?.focus();
});

document.getElementById("edit-user").addEventListener("submit", async (event) => {
  event.preventDefault();
  const id = editingId;
  const user = users.find((u) => u.user_id === id);
  if (!user) return;
  const changes = {
    name: document.getElementById("edit-name").value,
    phone: document.getElementById("edit-phone").value,
  };
  if (id !== me) {
    changes.role = document.getElementById("edit-role").value;
    changes.is_active = document.getElementById("edit-active").checked;
  }
  const button = event.currentTarget.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    await api(`/api/admin/users/${id}`, { method: "PATCH", body: JSON.stringify(changes) });
    dialog.close();
    await loadUsers(id);
  } catch (error) {
    show("edit-error", error.message);
  } finally {
    button.disabled = false;
  }
});

document.getElementById("add-user").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector("button[type=submit]");
  show("add-error", "");
  show("add-ok", "");
  button.disabled = true;
  try {
    const { user } = await api("/api/admin/users", {
      method: "POST",
      body: JSON.stringify({
        name: document.getElementById("new-name").value,
        email: document.getElementById("new-email").value,
        phone: document.getElementById("new-phone").value,
        role: document.getElementById("new-role").value,
        password: document.getElementById("new-password").value,
      }),
    });
    form.reset();
    show("add-ok", `Added ${user.name} as ${user.role}. Share the password with them securely.`);
    await loadUsers();
  } catch (error) {
    show("add-error", error.message);
  } finally {
    button.disabled = false;
  }
});

loadUsers();
