// Login and registration forms: post JSON to the API, show errors inline.
function showError(message) {
  const box = document.getElementById("form-error");
  box.textContent = message;
  box.hidden = false;
}

function wire(formId, endpoint, buildBody, destination) {
  const form = document.getElementById(formId);
  if (!form) return;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      await api(endpoint, { method: "POST", body: JSON.stringify(buildBody(form)) });
      window.location.href = destination(form);
    } catch (error) {
      showError(error.message);
      button.disabled = false;
    }
  });
}

wire("login-form", "/api/auth/login",
  (f) => ({ email: f.email.value, password: f.password.value, remember: f.querySelector("#remember").checked }),
  (f) => f.dataset.next || "/");

wire("register-form", "/api/auth/register",
  (f) => ({ name: f.name.value, email: f.email.value, phone: f.phone.value, password: f.password.value }),
  () => "/");
