const DEBUGGER_VERSION = "1.3";
const EXTENSION_VERSION = "1.4.9";
const DEBUG_LOG_ENABLED = false;
const SETTINGS_KEY = "doubaoDolaHelperSettings";
const DEFAULT_SETTINGS = {
  enabled: true,
  duration15Enabled: true,
  watermarkEnabled: true
};

const ENDPOINTS = {
  doubaoSkillPack: "doubao.com/samantha/skill/pack",
  dolaSkillPack: "dola.com/samantha/skill/pack",
  actionBarConfig: ".com/alice/slot/action_bar_v3/get_item_conf",
  doubaoChainSingle: "doubao.com/im/chain/single",
  dolaChainSingle: "dola.com/im/chain/single"
};

const DURATION_LABELS = new Set([
  "时长",
  "鏃堕暱",
  "閺冨爼鏆?"
]);

const DOUBAO_SKILL_PACK_URL_PART = ENDPOINTS.doubaoSkillPack;
const DOLA_SKILL_PACK_URL_PART = ENDPOINTS.dolaSkillPack;
const ACTION_BAR_CONF_URL_PART = ENDPOINTS.actionBarConfig;
const DOUBAO_CHAIN_SINGLE_URL_PART = ENDPOINTS.doubaoChainSingle;
const DOLA_CHAIN_SINGLE_URL_PART = ENDPOINTS.dolaChainSingle;
const QAAB_SALT_HEX = "4dd4c2e6b83162090e52b3c7a6733ba4"
  + "1cb2462b829ab58a196b39db57177524"
  + "f49baf7f08e8d68d26a72e37c1a95a2f"
  + "1f05a51892aef2949732b62a38aadd58";

const fetchPatterns = [
  { urlPattern: `*${DOUBAO_SKILL_PACK_URL_PART}*`, requestStage: "Request" },
  { urlPattern: `*${DOLA_SKILL_PACK_URL_PART}*`, requestStage: "Request" },
  { urlPattern: `*${ACTION_BAR_CONF_URL_PART}*`, requestStage: "Response" },
  { urlPattern: `*${DOUBAO_CHAIN_SINGLE_URL_PART}*`, requestStage: "Response" },
  { urlPattern: `*${DOLA_CHAIN_SINGLE_URL_PART}*`, requestStage: "Response" }
];

const attachedTabs = new Set();
const responseFileBodyPromises = new Map();
let currentSettings = { ...DEFAULT_SETTINGS };

function debugLog(...args) {
  if (DEBUG_LOG_ENABLED) {
    console.log(`[Doubao Dola Helper ${EXTENSION_VERSION}]`, ...args);
  }
}

loadSettings().then(() => attachExistingTabs());
chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName !== "local" || !changes[SETTINGS_KEY]) {
    return;
  }
  currentSettings = normalizeSettings(changes[SETTINGS_KEY].newValue);
  refreshBadges();
});

chrome.runtime.onInstalled.addListener(attachExistingTabs);
chrome.runtime.onStartup.addListener(attachExistingTabs);

chrome.tabs.onActivated.addListener(async ({ tabId }) => {
  const tab = await safeGetTab(tabId);
  if (tab && shouldAttachToTab(tab.url)) {
    ensureAttached(tabId);
  }
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  const url = changeInfo.url || tab.url;
  if (shouldAttachToTab(url)) {
    ensureAttached(tabId);
  }
});

chrome.tabs.onRemoved.addListener((tabId) => {
  attachedTabs.delete(tabId);
});

chrome.action.onClicked.addListener(async (tab) => {
  if (tab && tab.id) {
    await ensureAttached(tab.id);
    await sendToTab(tab.id, { type: "SHOW_PANEL" });
  }
});

chrome.debugger.onDetach.addListener((source) => {
  if (source.tabId) {
    attachedTabs.delete(source.tabId);
    setBadge(source.tabId, "");
  }
});

chrome.debugger.onEvent.addListener((source, method, params) => {
  if (method === "Fetch.requestPaused" && source.tabId && params) {
    handlePausedRequest(source.tabId, params);
  }
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message) {
    return false;
  }

  if (message.type === "DOWNLOAD_MEDIA" && isHttpUrl(message.url)) {
    chrome.downloads.download({ url: message.url, saveAs: false })
      .then(() => sendResponse({ success: true }))
      .catch((error) => {
        console.warn("download failed:", error);
        sendResponse({ success: false, message: "下载失败" });
      });
    return true;
  }

  if (message.type === "GET_SETTINGS") {
    loadSettings()
      .then((settings) => sendResponse({ settings }))
      .catch(() => sendResponse({ settings: { ...DEFAULT_SETTINGS } }));
    return true;
  }

  if (message.type === "SET_SETTINGS" && message.settings) {
    saveSettings(message.settings)
      .then((settings) => {
        if (sender?.tab?.id) {
          setBadge(sender.tab.id, settings.enabled ? "ON" : "OFF");
        }
        broadcastSettings(settings);
        sendResponse({ settings });
      })
      .catch(() => sendResponse({ settings: currentSettings }));
    return true;
  }

  return false;
});

async function attachExistingTabs() {
  const tabs = await chrome.tabs.query({});
  for (const tab of tabs) {
    if (tab.id && shouldAttachToTab(tab.url)) {
      ensureAttached(tab.id);
    }
  }
}

async function loadSettings() {
  try {
    const stored = await chrome.storage.local.get(SETTINGS_KEY);
    currentSettings = normalizeSettings(stored[SETTINGS_KEY]);
  } catch {
    currentSettings = { ...DEFAULT_SETTINGS };
  }
  return currentSettings;
}

async function saveSettings(nextSettings) {
  currentSettings = normalizeSettings(nextSettings);
  await chrome.storage.local.set({ [SETTINGS_KEY]: currentSettings });
  await refreshBadges();
  return currentSettings;
}

function normalizeSettings(value) {
  return {
    enabled: value?.enabled !== false,
    duration15Enabled: value?.duration15Enabled !== false,
    watermarkEnabled: value?.watermarkEnabled !== false
  };
}

async function broadcastSettings(settings) {
  const tabs = await chrome.tabs.query({});
  for (const tab of tabs) {
    if (tab.id && shouldAttachToTab(tab.url)) {
      sendToTab(tab.id, { type: "SETTINGS_UPDATED", settings }).catch(() => {});
    }
  }
}

async function refreshBadges() {
  const tabs = await chrome.tabs.query({});
  for (const tab of tabs) {
    if (tab.id && shouldAttachToTab(tab.url)) {
      setBadge(tab.id, currentSettings.enabled ? "ON" : "OFF");
    }
  }
}

async function safeGetTab(tabId) {
  try {
    return await chrome.tabs.get(tabId);
  } catch {
    return null;
  }
}

function shouldAttachToTab(url) {
  return typeof url === "string"
    && /^https?:\/\//i.test(url)
    && (url.includes("doubao.com") || url.includes("dola.com"));
}

async function ensureAttached(tabId) {
  if (attachedTabs.has(tabId)) {
    return;
  }

  try {
    await attachDebugger(tabId);
    debugLog("debugger attached", { tabId });
  } catch (error) {
    console.warn("debugger attach failed:", error.message || error);
  }

  try {
    await sendCommand(tabId, "Fetch.enable", { patterns: fetchPatterns });
    attachedTabs.add(tabId);
    setBadge(tabId, currentSettings.enabled ? "ON" : "OFF");
    debugLog("Fetch enabled", { tabId, patterns: fetchPatterns });
  } catch (error) {
    console.warn("Fetch.enable failed:", error.message || error);
    setBadge(tabId, "");
  }
}

function attachDebugger(tabId) {
  return new Promise((resolve, reject) => {
    chrome.debugger.attach({ tabId }, DEBUGGER_VERSION, () => {
      const error = chrome.runtime.lastError;
      if (error) {
        reject(new Error(error.message));
        return;
      }
      resolve();
    });
  });
}

function sendCommand(tabId, method, params = {}) {
  return new Promise((resolve, reject) => {
    chrome.debugger.sendCommand({ tabId }, method, params, (result) => {
      const error = chrome.runtime.lastError;
      if (error) {
        reject(new Error(error.message));
        return;
      }
      resolve(result);
    });
  });
}

async function handlePausedRequest(tabId, event) {
  const requestId = event.requestId;
  const request = event.request || {};
  const url = request.url || "";
  debugLog("request paused", { tabId, requestId, url });

  try {
    const settings = await loadSettings();
    if (!settings.enabled) {
      await continueRequest(tabId, requestId);
      return;
    }

    if (url.includes(DOUBAO_SKILL_PACK_URL_PART)) {
      if (!settings.duration15Enabled) {
        await continueRequest(tabId, requestId);
        return;
      }
      await fulfillJsonFile(tabId, requestId, request.method, "doubao-skill-pack-response.json");
      return;
    }

    if (url.includes(DOLA_SKILL_PACK_URL_PART)) {
      if (!settings.duration15Enabled) {
        await continueRequest(tabId, requestId);
        return;
      }
      await fulfillJsonFile(tabId, requestId, request.method, "dola-skill-pack-response.json");
      return;
    }

    if (url.includes(ACTION_BAR_CONF_URL_PART)) {
      if (!settings.duration15Enabled) {
        await continueRequest(tabId, requestId);
        return;
      }
      await rewriteActionBarConfigResponse(tabId, event);
      return;
    }

    if (url.includes(DOUBAO_CHAIN_SINGLE_URL_PART)) {
      if (!settings.watermarkEnabled) {
        await continueRequest(tabId, requestId);
        return;
      }
      await inspectChainSingleResponse(tabId, event, "doubao");
      return;
    }

    if (url.includes(DOLA_CHAIN_SINGLE_URL_PART)) {
      if (!settings.watermarkEnabled) {
        await continueRequest(tabId, requestId);
        return;
      }
      await inspectChainSingleResponse(tabId, event, "dola");
      return;
    }

    await continueRequest(tabId, requestId);
  } catch (error) {
    console.warn("request handling failed:", error.message || error);
    await continueRequest(tabId, requestId).catch(() => {});
  }
}

async function fulfillJsonFile(tabId, requestId, method, fileName) {
  if ((method || "").toUpperCase() === "OPTIONS") {
    await sendCommand(tabId, "Fetch.fulfillRequest", {
      requestId,
      responseCode: 204,
      responsePhrase: "No Content",
      responseHeaders: corsHeaders()
    });
    return;
  }

  const body = await getResponseFileBody(fileName);
  await sendCommand(tabId, "Fetch.fulfillRequest", {
    requestId,
    responseCode: 200,
    responsePhrase: "OK",
    responseHeaders: responseHeadersForTextBody(corsHeaders(), body),
    body: toBase64Utf8(body)
  });
}

function continueRequest(tabId, requestId) {
  return sendCommand(tabId, "Fetch.continueRequest", { requestId });
}

async function rewriteActionBarConfigResponse(tabId, event) {
  const response = await getPausedResponseBody(tabId, event.requestId);
  const patchedBody = patchActionBarDuration(response.body);

  await sendCommand(tabId, "Fetch.fulfillRequest", {
    requestId: event.requestId,
    responseCode: event.responseStatusCode || 200,
    responsePhrase: event.responseStatusText || "OK",
    responseHeaders: responseHeadersForTextBody(event.responseHeaders || [], patchedBody),
    body: toBase64Utf8(patchedBody)
  });
}

async function inspectChainSingleResponse(tabId, event, source) {
  const sourceKey = `${source}:${event.requestId}`;
  const response = await getPausedResponseBody(tabId, event.requestId);

  let items = [];
  try {
    const json = JSON.parse(response.body);
    if (source === "doubao") {
      items = await extractDoubaoItems(json, response.body);
    } else {
      items = extractDolaItems(json);
    }
  } catch (error) {
    console.warn(`${source} chain parse failed:`, error.message || error);
  }

  if (items.length) {
    await sendToTab(tabId, {
      type: "MEDIA_FOUND",
      sourceKey,
      items
    });

    // 直连 Web 工作台本地服务上报无水印视频！
    const videoItems = items.filter(it => it.type === "video");
    if (videoItems.length) {
      try {
        const tab = await safeGetTab(tabId);
        const tabUrl = tab?.url || "";
        const taskIdMatch = tabUrl.match(/vg_task=([A-Za-z0-9_-]+)/);
        const taskId = taskIdMatch ? taskIdMatch[1] : "";
        for (const v of videoItems) {
          fetch("http://127.0.0.1:8765/api/task/report_video", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              task_id: taskId,
              video_url: v.url,
              source: "service-worker"
            })
          }).catch(err => console.warn("[VG ServiceWorker] 直连上报视频失败:", err));
        }
      } catch (err) {
        console.warn("[VG ServiceWorker] 获取 tab 信息失败:", err);
      }
    }
  } else {
    await sendToTab(tabId, {
      type: "MEDIA_STATUS",
      sourceKey,
      text: "未提取到资源"
    });
  }

  await sendCommand(tabId, "Fetch.fulfillRequest", {
    requestId: event.requestId,
    responseCode: event.responseStatusCode || 200,
    responsePhrase: event.responseStatusText || "OK",
    responseHeaders: responseHeadersForTextBody(event.responseHeaders || [], response.body),
    body: toBase64Utf8(response.body)
  });
}

async function getPausedResponseBody(tabId, requestId) {
  const response = await sendCommand(tabId, "Fetch.getResponseBody", { requestId });
  return {
    body: response.base64Encoded ? fromBase64Utf8(response.body) : response.body
  };
}

async function extractDoubaoItems(json, rawBody) {
  const items = [];
  const seenUrls = new Set();

  for (const url of findImageOriRawUrls(json)) {
    addItem(items, seenUrls, "image", url);
  }

  for (const fallbackApi of findDoubaoFallbackApis(json, rawBody)) {
    const videoUrl = await getDoubaoVideoUrlFromFallbackApi(fallbackApi);
    addItem(items, seenUrls, "video", videoUrl);
  }

  return items;
}

function findDoubaoFallbackApis(json, rawBody) {
  const apis = new Set();

  for (const value of findValuesByKey(json, "fallback_api")) {
    addFallbackApi(apis, value);
  }

  const patterns = [
    /fallback_api\\":\\"(.*?)\\"/g,
    /"fallback_api"\s*:\s*"([^"]+)"/g
  ];

  for (const pattern of patterns) {
    let match = pattern.exec(rawBody);
    while (match) {
      addFallbackApi(apis, decodeJsonEscapedFragment(match[1]));
      match = pattern.exec(rawBody);
    }
  }

  return Array.from(apis);
}

function addFallbackApi(apis, value) {
  if (typeof value !== "string" || !value) {
    return;
  }

  const url = decodeJsonEscapedFragment(value);
  if (isHttpUrl(url)) {
    apis.add(url);
  }
}

function decodeJsonEscapedFragment(value) {
  let text = value;
  for (let index = 0; index < 3; index += 1) {
    try {
      const decoded = JSON.parse(`"${text.replace(/"/g, '\\"')}"`);
      if (decoded === text) {
        break;
      }
      text = decoded;
    } catch {
      break;
    }
  }
  return text.replace(/\\u0026/g, "&").replace(/\\\//g, "/");
}

async function getDoubaoVideoUrlFromFallbackApi(fallbackApi) {
  try {
    const url = replaceQueryParams(fallbackApi, {
	  channel: "no",
      codec_type: "8",
      logo_type: "unwatermarked"
    });
    const response = await fetch(url, {
      method: "GET",
      credentials: "omit",
      headers: {
        "accept": "application/json,text/plain,*/*"
      }
    });
    const payload = await response.json();
    const data = getVideoData(payload);
    const token = pickMainUrlToken(data);
    if (!token) {
      return "";
    }
    return await decodeMainUrl(token, findKeySeedDeep(payload));
  } catch (error) {
    console.warn("doubao fallback_api failed:", error.message || error);
    return "";
  }
}

function replaceQueryParams(url, params) {
  const parsedUrl = new URL(url);
  for (const [key, value] of Object.entries(params)) {
    parsedUrl.searchParams.set(key, value);
  }
  return parsedUrl.toString();
}

function getVideoData(payload) {
  const videoInfo = payload?.video_info || payload?.data?.video_info || payload;
  const data = videoInfo?.data || videoInfo;
  return data && typeof data === "object" ? data : {};
}

function pickMainUrlToken(data) {
  const videoList = data?.video_list;
  const entries = videoList && typeof videoList === "object" && Object.keys(videoList).length
    ? Object.values(videoList)
    : [data];
  let best = null;

  for (const entry of entries) {
    if (!entry || typeof entry !== "object") {
      continue;
    }
    const token = entry.main_url || entry.play_url || "";
    if (typeof token !== "string" || !token.trim()) {
      continue;
    }
    const score = Number(entry.bitrate || entry.real_bitrate || 0)
      + Number(entry.vwidth || entry.width || 0) * Number(entry.vheight || entry.height || 0);
    if (!best || score > best.score) {
      best = { token: token.trim(), score };
    }
  }

  return best ? best.token : "";
}

function findKeySeedDeep(value, depth = 0) {
  if (depth > 10 || value == null) {
    return "";
  }

  if (typeof value === "string") {
    let match = value.match(/(?:^|[?&])key_seed=([^&"'<>\\\s]+)/i);
    if (match) {
      return decodeURIComponent(match[1]);
    }
    match = value.match(/["']key_seed["']\s*:\s*["']([^"']+)/i);
    return match ? decodeURIComponent(match[1]) : "";
  }

  if (typeof value !== "object") {
    return "";
  }

  if (typeof value.key_seed === "string" && value.key_seed.trim()) {
    return value.key_seed.trim();
  }

  for (const item of Object.values(value)) {
    const hit = findKeySeedDeep(item, depth + 1);
    if (hit) {
      return hit;
    }
  }

  return "";
}

async function decodeMainUrl(token, keySeed = "") {
  if (isHttpUrl(token)) {
    return token;
  }

  const plainUrl = tryDecodeBase64Url(token);
  if (plainUrl) {
    return plainUrl;
  }

  if (token.startsWith("qAAB") && keySeed) {
    return await decodeQaabToken(token, keySeed);
  }

  return "";
}

function tryDecodeBase64Url(token) {
  const bytes = base64DecodeLoose(token);
  if (!bytes) {
    return "";
  }
  const text = asciiUrlFromBytes(bytes);
  return isHttpUrl(text) ? text : "";
}

function base64DecodeLoose(text) {
  const input = String(text || "").trim();
  const variants = [
    input,
    input.replace(/[$@#]/g, (char) => ({ "$": "_", "@": "/", "#": "." }[char])),
    input.replace(/[$@#]/g, (char) => ({ "$": "+", "@": "/", "#": "=" }[char]))
  ];
  const seen = new Set();

  for (const candidate of variants) {
    if (!candidate || seen.has(candidate)) {
      continue;
    }
    seen.add(candidate);
    try {
      const normalized = padBase64(candidate).replace(/-/g, "+").replace(/_/g, "/");
      const binary = atob(normalized);
      const bytes = new Uint8Array(binary.length);
      for (let index = 0; index < binary.length; index += 1) {
        bytes[index] = binary.charCodeAt(index);
      }
      return bytes;
    } catch {
      // Try the next variant.
    }
  }

  return null;
}

function padBase64(text) {
  const pad = (4 - (text.length % 4)) % 4;
  return text + "=".repeat(pad);
}

function asciiUrlFromBytes(bytes) {
  if (!bytes || !bytes.length) {
    return "";
  }
  for (const byte of bytes) {
    if (byte !== 9 && byte !== 10 && byte !== 13 && (byte < 32 || byte > 126)) {
      return "";
    }
  }
  return new TextDecoder().decode(bytes);
}

async function decodeQaabToken(token, keySeed) {
  const data = base64DecodeLoose(token);
  const seed = base64DecodeLoose(keySeed);
  if (!data || !seed) {
    return "";
  }

  const digest1 = await crypto.subtle.digest("SHA-512", seed.slice(0, 32));
  const salt = hexToBytes(QAAB_SALT_HEX);
  const digest2Input = concatBytes(new Uint8Array(digest1), salt);
  const digest2 = new Uint8Array(await crypto.subtle.digest("SHA-512", digest2Input));
  const key = digest2.slice(0, 16);
  const iv = digest2.slice(16, 32);
  const attempts = [];

  if (data.length >= 4 && data[0] === 0xa8 && data[1] === 0x00 && data[2] === 0x01 && data[3] === 0x00) {
    attempts.push({ payload: data.slice(4), key, iv });
    attempts.push({ payload: data.slice(4), key: iv, iv: key });
    if (data.length > 36) {
      attempts.push({ payload: data.slice(36), key, iv: data.slice(20, 36) });
      attempts.push({ payload: data.slice(36), key, iv });
    }
  } else {
    attempts.push({ payload: data, key, iv });
  }

  for (const attempt of attempts) {
    const url = await decryptAesCbcUrl(attempt.payload, attempt.key, attempt.iv);
    if (url) {
      return url;
    }
  }

  return "";
}

async function decryptAesCbcUrl(payload, keyBytes, ivBytes) {
  if (!payload.length || payload.length % 16 !== 0) {
    return "";
  }

  try {
    const key = await crypto.subtle.importKey("raw", keyBytes, "AES-CBC", false, ["decrypt"]);
    const plain = new Uint8Array(await crypto.subtle.decrypt({ name: "AES-CBC", iv: ivBytes }, key, payload));
    const direct = asciiUrlFromBytes(plain);
    if (isHttpUrl(direct)) {
      return direct;
    }
    const stripped = stripPkcs7(plain);
    const url = asciiUrlFromBytes(stripped);
    return isHttpUrl(url) ? url : "";
  } catch {
    return "";
  }
}

function stripPkcs7(bytes) {
  if (!bytes || !bytes.length) {
    return new Uint8Array();
  }
  const pad = bytes[bytes.length - 1];
  if (pad < 1 || pad > 16 || pad > bytes.length) {
    return bytes;
  }
  for (let index = bytes.length - pad; index < bytes.length; index += 1) {
    if (bytes[index] !== pad) {
      return bytes;
    }
  }
  return bytes.slice(0, bytes.length - pad);
}

function hexToBytes(hex) {
  const bytes = new Uint8Array(hex.length / 2);
  for (let index = 0; index < bytes.length; index += 1) {
    bytes[index] = parseInt(hex.slice(index * 2, index * 2 + 2), 16);
  }
  return bytes;
}

function concatBytes(first, second) {
  const bytes = new Uint8Array(first.length + second.length);
  bytes.set(first, 0);
  bytes.set(second, first.length);
  return bytes;
}

function extractDolaItems(json) {
  const items = [];
  const seenUrls = new Set();

  for (const url of findImageOriRawUrls(json)) {
    addItem(items, seenUrls, "image", url);
  }

  for (const encodedUrl of findDolaEncodedVideoUrls(json)) {
    const url = decodeBase64Url(encodedUrl);
    addItem(items, seenUrls, "video", url);
  }

  return items;
}

function findDolaEncodedVideoUrls(json) {
  const values = [];
  for (const value of findValuesByKey(json, "man_url")) {
    values.push(value);
  }
  for (const value of findValuesByKey(json, "main_url")) {
    values.push(value);
  }
  return values;
}

function patchActionBarDuration(body) {
  try {
    const json = JSON.parse(body);
    const changed = patchNestedJsonStrings(json);
    return changed ? JSON.stringify(json) : body;
  } catch (error) {
    console.warn("patch action bar duration failed:", error.message || error);
    return body;
  }
}

function patchNestedJsonStrings(value, seen = new Set()) {
  if (value == null || typeof value !== "object" || seen.has(value)) {
    return false;
  }

  seen.add(value);
  let changed = false;

  if (Array.isArray(value)) {
    for (const item of value) {
      changed = patchNestedJsonStrings(item, seen) || changed;
    }
    return changed;
  }

  for (const key of Object.keys(value)) {
    const child = value[key];
    if (typeof child === "string") {
      const patchedString = patchJsonStringDurationSafe(child);
      if (patchedString !== child) {
        value[key] = patchedString;
        changed = true;
      }
    } else {
      changed = patchNestedJsonStrings(child, seen) || changed;
    }
  }

  return changed;
}

function patchJsonStringDurationSafe(text) {
  if (!text || !mightContainDurationLabel(text)) {
    return text;
  }

  try {
    const json = JSON.parse(text);
    const changed = patchDurationSelectorSafe(json);
    return changed ? JSON.stringify(json) : text;
  } catch {
    return text;
  }
}

function patchJsonStringDuration(text) {
  if (!text || (!text.includes("时长") && !text.includes("鏃堕暱"))) {
    return text;
  }

  try {
    const json = JSON.parse(text);
    const changed = patchDurationSelector(json);
    return changed ? JSON.stringify(json) : text;
  } catch {
    return text;
  }
}

function patchDurationSelectorSafe(value, seen = new Set()) {
  if (value == null || typeof value !== "object" || seen.has(value)) {
    return false;
  }

  seen.add(value);
  let changed = false;

  if (Array.isArray(value)) {
    for (const item of value) {
      changed = patchDurationSelectorSafe(item, seen) || changed;
    }
    return changed;
  }

  if (isDurationSelector(value) && Array.isArray(value.option_list)) {
    const has15s = value.option_list.some((option) => option && option.option_key === "15");
    if (!has15s) {
      const tenSecondIndex = value.option_list.findIndex((option) => option && option.option_key === "10");
      const insertIndex = tenSecondIndex >= 0 ? tenSecondIndex + 1 : value.option_list.length;
      value.option_list.splice(insertIndex, 0, createFifteenSecondOption(value.option_list));
      changed = true;
    }
  }

  for (const key of Object.keys(value)) {
    const child = value[key];
    if (typeof child === "string") {
      const patchedString = patchJsonStringDurationSafe(child);
      if (patchedString !== child) {
        value[key] = patchedString;
        changed = true;
      }
    } else {
      changed = patchDurationSelectorSafe(child, seen) || changed;
    }
  }

  return changed;
}

function mightContainDurationLabel(text) {
  return Array.from(DURATION_LABELS).some((label) => text.includes(label));
}

function isDurationSelector(value) {
  return value && typeof value.label === "string" && DURATION_LABELS.has(value.label);
}

function patchDurationSelector(value, seen = new Set()) {
  if (value == null || typeof value !== "object" || seen.has(value)) {
    return false;
  }

  seen.add(value);
  let changed = false;

  if (Array.isArray(value)) {
    for (const item of value) {
      changed = patchDurationSelector(item, seen) || changed;
    }
    return changed;
  }

  if ((value.label === "时长" || value.label === "鏃堕暱") && Array.isArray(value.option_list)) {
    const has15s = value.option_list.some((option) => option && option.option_key === "15");
    if (!has15s) {
      const tenSecondIndex = value.option_list.findIndex((option) => option && option.option_key === "10");
      const insertIndex = tenSecondIndex >= 0 ? tenSecondIndex + 1 : value.option_list.length;
      value.option_list.splice(insertIndex, 0, createFifteenSecondOption(value.option_list));
      changed = true;
    }
  }

  for (const key of Object.keys(value)) {
    const child = value[key];
    if (typeof child === "string") {
      const patchedString = patchJsonStringDurationSafe(child);
      if (patchedString !== child) {
        value[key] = patchedString;
        changed = true;
      }
    } else {
      changed = patchDurationSelector(child, seen) || changed;
    }
  }

  return changed;
}

function createFifteenSecondOption(optionList) {
  const maxId = optionList.reduce((maxValue, option) => {
    const id = Number(option?.id);
    return Number.isFinite(id) ? Math.max(maxValue, id) : maxValue;
  }, 0);

  return {
    id: maxId + 1,
    display_text: "15s",
    message_text: "",
    option_key: "15"
  };
}

function findImageOriRawUrls(value) {
  const urls = [];
  walkJsonAndStrings(value, (node) => {
    if (node && typeof node === "object" && !Array.isArray(node)) {
      const image = node.image_ori_raw;
      if (image && typeof image === "object" && isHttpUrl(image.url)) {
        urls.push(image.url);
      }
    }
  });
  return urls;
}

function findValuesByKey(value, targetKey) {
  const values = [];
  walkJsonAndStrings(value, (node) => {
    if (!node || typeof node !== "object" || Array.isArray(node)) {
      return;
    }
    if (Object.prototype.hasOwnProperty.call(node, targetKey)) {
      values.push(node[targetKey]);
    }
  });
  return values;
}

function walkJsonAndStrings(value, visitor, seen = new Set()) {
  if (value == null) {
    return;
  }

  if (typeof value === "string") {
    const parsed = parseJsonString(value);
    if (parsed !== null) {
      walkJsonAndStrings(parsed, visitor, seen);
    }
    return;
  }

  if (typeof value !== "object" || seen.has(value)) {
    return;
  }

  seen.add(value);
  visitor(value);

  if (Array.isArray(value)) {
    for (const item of value) {
      walkJsonAndStrings(item, visitor, seen);
    }
    return;
  }

  for (const key of Object.keys(value)) {
    walkJsonAndStrings(value[key], visitor, seen);
  }
}

function parseJsonString(text) {
  const trimmed = text.trim();
  if (!trimmed || (!trimmed.startsWith("{") && !trimmed.startsWith("["))) {
    return null;
  }

  try {
    return JSON.parse(trimmed);
  } catch {
    return null;
  }
}

function addItem(items, seenUrls, type, url) {
  if (!isHttpUrl(url) || seenUrls.has(url)) {
    return;
  }
  seenUrls.add(url);
  items.push({ type, url });
}

function decodeBase64Url(value) {
  if (typeof value !== "string" || !value) {
    return "";
  }

  if (isHttpUrl(value)) {
    return value;
  }

  try {
    const padded = value.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(value.length / 4) * 4, "=");
    const decoded = fromBase64Utf8(padded);
    return isHttpUrl(decoded) ? decoded : "";
  } catch {
    return "";
  }
}

function responseHeadersForTextBody(headers, body) {
  const contentLength = String(new TextEncoder().encode(body).length);
  const nextHeaders = [];
  let hasContentType = false;
  let hasContentLength = false;

  for (const header of headers) {
    const name = header.name || "";
    const lowerName = name.toLowerCase();
    if (lowerName === "content-encoding") {
      continue;
    }

    if (lowerName === "content-type") {
      hasContentType = true;
      nextHeaders.push({ name, value: "application/json; charset=utf-8" });
      continue;
    }

    if (lowerName === "content-length") {
      hasContentLength = true;
      nextHeaders.push({ name, value: contentLength });
      continue;
    }

    nextHeaders.push(header);
  }

  if (!hasContentType) {
    nextHeaders.push({ name: "content-type", value: "application/json; charset=utf-8" });
  }

  if (!hasContentLength) {
    nextHeaders.push({ name: "content-length", value: contentLength });
  }

  return nextHeaders;
}

function getResponseFileBody(fileName) {
  if (!responseFileBodyPromises.has(fileName)) {
    responseFileBodyPromises.set(fileName, fetch(chrome.runtime.getURL(fileName)).then((response) => response.text()));
  }
  return responseFileBodyPromises.get(fileName);
}

function corsHeaders() {
  return [
    { name: "access-control-allow-origin", value: "*" },
    { name: "access-control-allow-credentials", value: "true" },
    { name: "access-control-allow-methods", value: "GET, POST, OPTIONS" },
    { name: "access-control-allow-headers", value: "*" }
  ];
}

function toBase64Utf8(text) {
  const bytes = new TextEncoder().encode(text);
  let binary = "";
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary);
}

function fromBase64Utf8(base64Text) {
  const binary = atob(base64Text);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return new TextDecoder().decode(bytes);
}

function isHttpUrl(url) {
  return typeof url === "string" && /^https?:\/\//i.test(url);
}

async function sendToTab(tabId, message) {
  try {
    await chrome.tabs.sendMessage(tabId, message);
  } catch (error) {
    console.warn("send panel message failed:", error.message || error);
  }
}

function setBadge(tabId, text) {
  chrome.action.setBadgeText({ tabId, text }).catch(() => {});
  chrome.action.setBadgeBackgroundColor({ tabId, color: "#166534" }).catch(() => {});
}
