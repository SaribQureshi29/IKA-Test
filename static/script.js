import { PublicClientApplication } from "https://cdn.jsdelivr.net/npm/@azure/msal-browser@4.27.0/+esm";

//existing configuration
const tenantId = "de953da2-aff8-466c-b550-c40b3abd58f2";
const webClientId = "a1ed222b-278c-47a1-860c-60fa16f9acd9";
const apiClientId = "bbeb10b8-51fa-4520-9f79-6f92fb55097a";
const apiBaseUrl = window.location.origin;

// DOM Elements
const loginBtn = document.getElementById('loginBtn');
const logoutBtn = document.getElementById('logoutBtn');
const userName = document.getElementById('userName');
const userAvatar = document.getElementById('userAvatar');
const userStatus = document.getElementById('userStatus');
const userInfo = document.getElementById('userInfo');
const statusEl = document.getElementById('status');
const chatIcon = document.getElementById('chatIcon');
const chatIconBtn = document.getElementById('chatIconBtn');
const chatModal = document.getElementById('chatModal');
const closeChatBtn = document.getElementById('closeChatBtn');
const chatBody = document.getElementById('chatBody');
const questionInput = document.getElementById('questionInput');
const sendBtn = document.getElementById('sendBtn');
const chatStatus = document.getElementById('chatStatus');
const typingIndicator = document.getElementById('typingIndicator');
const welcomeMessage = document.getElementById('welcomeMessage');
const referencesSection = document.getElementById('referencesSection');
const referencesList = document.getElementById('referencesList');
const adminPanelBtn = document.getElementById('adminPanelBtn');

// Redirect URI must match Azure app registration exactly
const configRes = await fetch(apiBaseUrl + "/config");
const config = await configRes.json().catch(() => ({}));
let redirectUri = (config.redirectUri && config.redirectUri.trim()) || "";
if (!redirectUri) {
  if (window.location.hostname === "127.0.0.1") {
    redirectUri = "http://localhost:" + (window.location.port || "8000");
  } else {
    redirectUri = window.location.origin;
  }
}
redirectUri = redirectUri.replace(/\/+$/, "");
// Always use localhost instead of 127.0.0.1 so it matches Azure app registration
if (redirectUri.includes("127.0.0.1")) {
  redirectUri = redirectUri.replace("127.0.0.1", "localhost");
}

// MSAL Configuration - FIXED for popup issues
const msalConfig = {
  auth: {
    clientId: webClientId,
    authority: `https://login.microsoftonline.com/${tenantId}`,
    redirectUri: redirectUri,
    postLogoutRedirectUri: redirectUri,
    navigateToLoginRequestUrl: false
  },
  cache: {
    cacheLocation: "sessionStorage",
    storeAuthStateInCookie: true // Helps with popup blockers
  },
  system: {
    windowHashTimeout: 60000,
    iframeHashTimeout: 6000,
    loadFrameTimeout: 6000,
    asyncPopups: false
  }
};

const tokenRequest = {
  scopes: [`api://${apiClientId}/access_as_user`],
  prompt: "select_account"
};

const msalInstance = new PublicClientApplication(msalConfig);

// Initialize MSAL
await msalInstance.initialize();

// Handle redirect promise first
try {
  const redirectResponse = await msalInstance.handleRedirectPromise();
  if (redirectResponse) {
    msalInstance.setActiveAccount(redirectResponse.account);
    await updateUserUI(redirectResponse.account);
    showStatus(`Logged in as: ${redirectResponse.account.username}`, 'success');
  }
} catch (error) {
  console.error("Redirect handling error:", error);
}

// Check if user is admin (uses existing /admin/check-access endpoint)
async function checkAdminStatus() {
  try {
    const token = await getAccessToken();
    const res = await fetch(apiBaseUrl + "/admin/check-access", {
      headers: { "Authorization": "Bearer " + token }
    });
    const data = await res.json().catch(() => ({}));
    return data.hasAccess === true;
  } catch (e) {
    console.error("Admin check failed:", e);
    return false;
  }
}

// Updated updateUserUI with admin check
async function updateUserUI(account) {
  if (account) {
    const name = account.username || account.name || "User";
    userName.textContent = name;
    userAvatar.textContent = name.split(' ').map(n => n[0]).join('').toUpperCase().substring(0, 2);
    userStatus.textContent = "Online";
    userStatus.style.color = "#27ae60";

    userInfo.classList.add('visible');
    loginBtn.classList.add('hidden');
    logoutBtn.classList.remove('hidden');

    questionInput.disabled = false;
    sendBtn.disabled = false;

    const isAdmin = await checkAdminStatus();
    if (adminPanelBtn) {
      if (isAdmin) adminPanelBtn.classList.remove('hidden');
      else adminPanelBtn.classList.add('hidden');
    }

    chatIcon.classList.add('visible');
    showStatus(`Logged in as: ${name}`, 'success');
  } else {
    userName.textContent = "User";
    userAvatar.textContent = "?";
    userStatus.textContent = "Offline";
    userStatus.style.color = "";

    userInfo.classList.remove('visible');
    loginBtn.classList.remove('hidden');
    logoutBtn.classList.add('hidden');
    if (adminPanelBtn) adminPanelBtn.classList.add('hidden');

    questionInput.disabled = true;
    sendBtn.disabled = true;

    chatIcon.classList.remove('visible');
    chatModal.classList.remove('active');
  }
}

function showStatus(message, type = '') {
  statusEl.textContent = message;
  statusEl.className = type ? `status ${type}` : 'status';
  setTimeout(() => {
    if (statusEl.textContent === message) {
      statusEl.textContent = '';
      statusEl.className = 'status';
    }
  }, 3000);
}

function showChatStatus(message, type = '') {
  chatStatus.textContent = message;
  chatStatus.style.color = type === 'error' ? '#e74c3c' : type === 'success' ? '#27ae60' : '#666';
}

function formatResponse(text) {
  if (!text) return text;

  let formatted = text
    .replace(/(\d+\.\s+)/g, '\n$1')
    .replace(/(?:^|\n)\s*[-•*]\s+/g, '\n• ')
    .replace(/(\d+\.)\s+/g, '$1 ')
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.*?)\*/g, '<em>$1</em>')
    .trim();

  const lines = formatted.split('\n');
  let inList = false;
  let resultLines = [];

  for (let line of lines) {
    const trimmedLine = line.trim();
    const numberMatch = trimmedLine.match(/^(\d+)\.\s+(.+)$/);
    const bulletMatch = trimmedLine.match(/^[•\-*]\s+(.+)$/);

    if (numberMatch || bulletMatch) {
      if (!inList) {
        inList = true;
        resultLines.push('<ol>');
      }
      const content = numberMatch ? numberMatch[2] : bulletMatch[1];
      resultLines.push(`<li>${content}</li>`);
    } else {
      if (inList) {
        inList = false;
        resultLines.push('</ol>');
      }
      if (trimmedLine) {
        if (trimmedLine.includes(':')) {
          const parts = trimmedLine.split(':');
          if (parts.length >= 2) {
            const key = parts[0].trim();
            const value = parts.slice(1).join(':').trim();
            resultLines.push(`<p><strong>${key}:</strong> ${value}</p>`);
          } else {
            resultLines.push(`<p>${trimmedLine}</p>`);
          }
        } else {
          resultLines.push(`<p>${trimmedLine}</p>`);
        }
      }
    }
  }
  if (inList) resultLines.push('</ol>');

  formatted = resultLines.join('\n');
  formatted = formatted.replace(/\n\n+/g, '\n');
  return formatted;
}

function addMessage(role, text) {
  const messageDiv = document.createElement('div');
  messageDiv.className = `message ${role}`;

  const avatarDiv = document.createElement('div');
  avatarDiv.className = 'avatar';
  if (role === 'user') {
    avatarDiv.textContent = userName.textContent.split(' ').map(n => n[0]).join('').toUpperCase().substring(0, 2);
  } else {
    avatarDiv.innerHTML = '<i class="fas fa-robot"></i>';
  }

  const contentDiv = document.createElement('div');
  contentDiv.className = 'message-content';
  const bubbleDiv = document.createElement('div');
  bubbleDiv.className = 'bubble';
  if (role === 'bot') {
    bubbleDiv.innerHTML = formatResponse(text);
  } else {
    bubbleDiv.textContent = text;
  }

  const metaDiv = document.createElement('div');
  metaDiv.className = 'meta';
  metaDiv.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  contentDiv.appendChild(bubbleDiv);
  contentDiv.appendChild(metaDiv);
  messageDiv.appendChild(avatarDiv);
  messageDiv.appendChild(contentDiv);

  if (chatBody) {
    if (typingIndicator && typingIndicator.parentNode === chatBody) {
      chatBody.insertBefore(messageDiv, typingIndicator);
    } else {
      chatBody.appendChild(messageDiv);
    }
  }
  if (role === 'user' && welcomeMessage && welcomeMessage.parentNode === chatBody) {
    welcomeMessage.remove();
  }
  chatBody.scrollTop = chatBody.scrollHeight;
  return messageDiv;
}

function updateReferences(meta) {
  const sources = meta?.sources;
  const refTitles = meta?.References;
  const hasSources = sources && sources.length > 0;
  const hasRefs = refTitles && refTitles.length > 0;

  if (!hasSources && !hasRefs) {
    referencesSection.classList.add('hidden');
    return;
  }
  referencesSection.classList.remove('hidden');
  referencesList.innerHTML = '';

  if (hasSources) {
    sources.forEach((src, idx) => {
      const refItem = document.createElement('div');
      refItem.className = 'ref-item';
      const num = document.createElement('span');
      num.className = 'ref-num';
      num.textContent = `${idx + 1}. `;
      refItem.appendChild(num);
      const label = document.createElement('span');
      label.className = 'ref-label';
      label.textContent = 'From document: ';
      refItem.appendChild(label);
      if (src.url) {
        const link = document.createElement('a');
        link.href = src.url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = src.title || 'Document';
        link.className = 'ref-link';
        refItem.appendChild(link);
      } else {
        const name = document.createElement('span');
        name.textContent = src.title || 'Document';
        refItem.appendChild(name);
      }
      referencesList.appendChild(refItem);
    });
  } else {
    refTitles.forEach((title, idx) => {
      const refItem = document.createElement('div');
      refItem.className = 'ref-item';
      const num = document.createElement('span');
      num.className = 'ref-num';
      num.textContent = `${idx + 1}. `;
      refItem.appendChild(num);
      const label = document.createElement('span');
      label.className = 'ref-label';
      label.textContent = 'From document: ';
      refItem.appendChild(label);
      const name = document.createElement('span');
      name.textContent = title;
      refItem.appendChild(name);
      referencesList.appendChild(refItem);
    });
  }
  referencesSection.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function getAccountOrThrow() {
  let account = msalInstance.getActiveAccount();
  if (!account) {
    const accounts = msalInstance.getAllAccounts();
    if (accounts.length === 1) {
      account = accounts[0];
      msalInstance.setActiveAccount(account);
    } else if (accounts.length > 1) {
      throw new Error("Multiple cached accounts. Logout and login with only one account.");
    } else {
      throw new Error("No account found. Please login first.");
    }
  }
  return account;
}

async function getAccessToken() {
  const account = getAccountOrThrow();
  try {
    const resp = await msalInstance.acquireTokenSilent({ ...tokenRequest, account });
    return resp.accessToken;
  } catch (error) {
    try {
      const resp = await msalInstance.acquireTokenPopup({ ...tokenRequest, account });
      return resp.accessToken;
    } catch (popupError) {
      throw new Error("Failed to acquire token");
    }
  }
}

async function callChatApi(question) {
  const token = await getAccessToken();
  const res = await fetch(`${apiBaseUrl}/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": "Bearer " + token
    },
    body: JSON.stringify({ question })
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data?.detail || "Request failed");
  }
  return data;
}

// Admin panel: get admin token from /admin/check-access then go to /admin (no second login)
if (adminPanelBtn) {
  adminPanelBtn.addEventListener('click', async (e) => {
    e.preventDefault();
    try {
      const token = await getAccessToken();
      const res = await fetch(apiBaseUrl + '/admin/check-access', {
        headers: { 'Authorization': 'Bearer ' + token }
      });
      const data = await res.json().catch(() => ({}));
      if (data.hasAccess && data.token) {
        sessionStorage.setItem('ika_admin_token', data.token);
        window.location.href = '/admin';
      } else {
        showStatus('You do not have access to the admin panel.', 'error');
      }
    } catch (err) {
      showStatus(err?.message || 'Could not open admin panel.', 'error');
    }
  });
}

chatIconBtn.addEventListener('click', () => {
  chatModal.classList.add('active');
  document.body.classList.add('chat-open');
  questionInput.focus();
});

closeChatBtn.addEventListener('click', () => {
  chatModal.classList.remove('active');
  document.body.classList.remove('chat-open');
});

chatModal.addEventListener('click', (e) => {
  if (e.target === chatModal) {
    chatModal.classList.remove('active');
    document.body.classList.remove('chat-open');
  }
});

// Detect if we're inside a popup or iframe (MSAL blocks nested popups)
function isInsidePopupOrIframe() {
  return !!(window.opener || (window.self !== window.top));
}

// Login: when in popup/iframe, go to /login page (redirect-only, no nested popup). Otherwise try popup then redirect.
loginBtn.onclick = async () => {
  chatStatus.textContent = "";
  if (isInsidePopupOrIframe()) {
    showStatus("Redirecting to sign in...", '');
    window.location.href = "/login";
    return;
  }
  showStatus("Opening login window...", '');
  try {
    const resp = await msalInstance.loginPopup({
      ...tokenRequest,
      windowFeatures: "popup"
    });
    msalInstance.setActiveAccount(resp.account);
    await updateUserUI(resp.account);
    showStatus(`Logged in as: ${resp.account.username}`, 'success');
  } catch (e) {
    console.error("Popup login failed:", e);
    const isNestedOrBlocked = e.message?.includes("block_nested_popups") ||
      e.message?.includes("popup") || e.errorCode?.includes("popup") ||
      e.message?.includes("blocked") || e.name === "PopupWindowError";
    if (isNestedOrBlocked) {
      showStatus("Redirecting to sign in...", '');
      window.location.href = "/login";
    } else {
      showStatus(`Login failed: ${e?.message || e}`, 'error');
    }
  }
};

// Logout: always use redirect so user lands on login screen cleanly (no stale UI)
logoutBtn.onclick = async () => {
  showStatus("Signing out...", '');
  sessionStorage.removeItem('ika_admin_token');
  const acc = msalInstance.getActiveAccount();
  try {
    await msalInstance.logoutRedirect({
      account: acc || undefined,
      postLogoutRedirectUri: redirectUri
    });
  } catch (e) {
    console.error("Logout error:", e);
    window.location.href = redirectUri || window.location.origin;
  }
};

sendBtn.onclick = async () => {
  const question = questionInput.value.trim();
  if (!question) return;

  addMessage('user', question);
  questionInput.value = "";
  showChatStatus("");
  typingIndicator.classList.add('visible');
  sendBtn.disabled = true;
  questionInput.disabled = true;

  try {
    const response = await callChatApi(question);
    typingIndicator.classList.remove('visible');
    addMessage('bot', response.answer || "No answer returned.");
    updateReferences(response.meta);
    showChatStatus("Response received", 'success');
  } catch (error) {
    typingIndicator.classList.remove('visible');
    addMessage('bot', `Error: ${error.message}`);
    showChatStatus(`Error: ${error.message}`, 'error');
  } finally {
    sendBtn.disabled = false;
    questionInput.disabled = false;
    questionInput.focus();
  }
};

questionInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    if (!sendBtn.disabled) sendBtn.click();
  }
});

questionInput.addEventListener('input', function() {
  this.style.height = 'auto';
  this.style.height = Math.min(this.scrollHeight, 100) + 'px';
  this.style.overflowY = 'hidden';
});

// If landing with ?signout=1 (e.g. from admin panel Sign out), do full Azure logout and redirect back
if (window.location.search.includes("signout=1")) {
  sessionStorage.removeItem("ika_admin_token");
  history.replaceState({}, "", window.location.pathname || "/");
  msalInstance.logoutRedirect({ postLogoutRedirectUri: redirectUri }).catch(() => {});
} else {
  // Already logged in on load
  try {
    const accounts = msalInstance.getAllAccounts();
    if (accounts.length === 1) {
      msalInstance.setActiveAccount(accounts[0]);
      await updateUserUI(accounts[0]);
    } else {
      updateUserUI(null);
    }
  } catch (error) {
    console.error("Initialization error:", error);
    updateUserUI(null);
  }
}
