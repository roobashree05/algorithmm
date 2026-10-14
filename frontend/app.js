const tokenInput = document.querySelector("#token");
const tokenStatus = document.querySelector("#token-status");
const bugStatus = document.querySelector("#bug-status");
const productStatus = document.querySelector("#product-status");

tokenInput.value = sessionStorage.getItem("bugtrackerToken") || "";

document.querySelector("#save-token").addEventListener("click", () => {
  sessionStorage.setItem("bugtrackerToken", tokenInput.value.trim());
  tokenStatus.textContent = "Token saved in this browser session.";
  tokenStatus.className = "ok";
  loadBugs();
});

document.querySelector("#refresh-bugs").addEventListener("click", loadBugs);
document.querySelector("#bug-form").addEventListener("submit", submitBug);
document.querySelector("#product-form").addEventListener("submit", saveProduct);

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  const token = sessionStorage.getItem("bugtrackerToken");
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  if (options.json) {
    headers.set("Content-Type", "application/json");
    options.body = JSON.stringify(options.json);
  }
  const response = await fetch(path, { ...options, headers });
  const text = await response.text();
  const payload = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const message = payload && payload.error ? payload.error.message : response.statusText;
    throw new Error(message);
  }
  return payload;
}

async function loadHealth() {
  const health = document.querySelector("#health");
  try {
    const payload = await api("/health");
    health.textContent = payload.status;
    health.className = "ok";
  } catch (error) {
    health.textContent = "unavailable";
    health.className = "error";
  }
}

async function loadBugs() {
  const body = document.querySelector("#bugs");
  body.innerHTML = "";
  try {
    const payload = await api("/api/v1/bugs");
    for (const bug of payload.items) {
      const row = document.createElement("tr");
      row.innerHTML = `<td></td><td></td><td></td><td></td><td></td>`;
      const cells = row.querySelectorAll("td");
      cells[0].textContent = bug.product_name;
      cells[1].textContent = bug.customer_code;
      cells[2].textContent = bug.severity;
      cells[3].textContent = bug.status;
      cells[4].textContent = bug.gitlab_issue_url || bug.gitlab_issue_iid || "";
      body.appendChild(row);
    }
    if (!payload.items.length) {
      body.innerHTML = "<tr><td colspan='5'>No reports are visible for this token.</td></tr>";
    }
  } catch (error) {
    body.innerHTML = `<tr><td colspan='5'></td></tr>`;
    body.querySelector("td").textContent = error.message;
  }
}

async function submitBug(event) {
  event.preventDefault();
  bugStatus.textContent = "";
  const form = new FormData();
  form.set("product_slug", document.querySelector("#product").value.trim());
  form.set("severity", document.querySelector("#severity").value);
  form.set("description", document.querySelector("#description").value.trim());
  const file = document.querySelector("#screenshot").files[0];
  if (file) {
    form.set("screenshot", file);
  }
  try {
    const token = sessionStorage.getItem("bugtrackerToken");
    const response = await fetch("/api/v1/bugs", {
      method: "POST",
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: form,
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error ? payload.error.message : "Submit failed");
    }
    bugStatus.textContent = `Submitted ${payload.id}. Reference: ${payload.gitlab_issue_url || payload.gitlab_issue_iid}`;
    bugStatus.className = "ok";
    loadBugs();
  } catch (error) {
    bugStatus.textContent = error.message;
    bugStatus.className = "error";
  }
}

async function saveProduct(event) {
  event.preventDefault();
  productStatus.textContent = "";
  try {
    const payload = await api("/api/v1/admin/products", {
      method: "POST",
      json: {
        name: document.querySelector("#product-name").value.trim(),
        slug: document.querySelector("#product-slug").value.trim(),
        gitlab_project_id: document.querySelector("#gitlab-project").value.trim(),
        gitlab_project_path: `demo/${document.querySelector("#product-slug").value.trim()}`,
        developer_team_slug: document.querySelector("#team-slug").value.trim(),
        default_labels: ["bug", "whatsapp", "customer-reported"],
      },
    });
    productStatus.textContent = `Saved ${payload.slug}.`;
    productStatus.className = "ok";
  } catch (error) {
    productStatus.textContent = error.message;
    productStatus.className = "error";
  }
}

loadHealth();
if (tokenInput.value) {
  loadBugs();
}
