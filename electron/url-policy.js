"use strict";

function isLocalAppUrl(rawUrl, baseUrl) {
  try {
    return new URL(rawUrl).origin === baseUrl;
  } catch {
    return false;
  }
}

function safeExternalHttpUrl(rawUrl) {
  try {
    const parsed = new URL(rawUrl);
    return parsed.protocol === "https:" || parsed.protocol === "http:"
      ? parsed.href
      : null;
  } catch {
    return null;
  }
}

module.exports = { isLocalAppUrl, safeExternalHttpUrl };
