// Posts the predefined message to iha.ee using a logged-in account.
//
// Usage:
//   node src/post.js            -> log in and post the message from config.json
//   node src/post.js --dry-run  -> log in, fill the form, but DO NOT submit
//   node src/post.js --inspect  -> log in, open the post page, dump every form
//                                  field it finds (so we can fill in real selectors)
//
// Credentials come from environment variables (.env): IHA_USERNAME / IHA_PASSWORD.
// Nothing secret is ever written to config.json or committed.

import { readFileSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { chromium } from "playwright";
import "dotenv/config";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..");
const config = JSON.parse(readFileSync(join(ROOT, "config.json"), "utf8"));

const DRY_RUN = process.argv.includes("--dry-run");
const INSPECT = process.argv.includes("--inspect");
const HEADFUL = process.env.HEADFUL === "true";

function log(...args) {
  console.log(new Date().toISOString(), ...args);
}

function requireEnv(name) {
  const v = process.env[name];
  if (!v) {
    throw new Error(
      `Missing env var ${name}. Copy .env.example to .env and fill it in.`
    );
  }
  return v;
}

async function screenshot(page, name) {
  const dir = join(ROOT, "screenshots");
  mkdirSync(dir, { recursive: true });
  const path = join(dir, `${name}-${Date.now()}.png`);
  await page.screenshot({ path, fullPage: true });
  log("Saved screenshot:", path);
  return path;
}

async function login(page) {
  const { loginUrl } = config.site;
  const s = config.selectors.login;
  const username = requireEnv("IHA_USERNAME");
  const password = requireEnv("IHA_PASSWORD");

  log("Opening login page:", loginUrl);
  await page.goto(loginUrl, { waitUntil: "domcontentloaded" });

  await page.fill(s.username, username);
  await page.fill(s.password, password);

  // Prefer clicking the button by its visible Estonian label, fall back to selector.
  const byText = page.getByRole("button", { name: s.submitText });
  if (await byText.count()) {
    await byText.first().click();
  } else {
    await page.click(s.submit);
  }

  await page.waitForLoadState("networkidle").catch(() => {});

  const ok = await page.locator(s.loggedInIndicator).count();
  if (!ok) {
    await screenshot(page, "login-failed");
    throw new Error(
      "Login may have failed — could not find a logged-in indicator. " +
        "Check credentials and the login selectors in config.json."
    );
  }
  log("Logged in.");
}

async function inspect(page) {
  // Dump every form field on the post page so we can map real selectors.
  await page.goto(config.site.postUrl, { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("networkidle").catch(() => {});

  const fields = await page.evaluate(() => {
    const out = [];
    for (const el of document.querySelectorAll("input, textarea, select, button")) {
      out.push({
        tag: el.tagName.toLowerCase(),
        type: el.getAttribute("type"),
        name: el.getAttribute("name"),
        id: el.getAttribute("id"),
        placeholder: el.getAttribute("placeholder"),
        value: (el.value || "").slice(0, 40),
        text: (el.textContent || "").trim().slice(0, 40),
      });
    }
    return out;
  });

  log("Form elements on post page:");
  console.table(fields);
  await screenshot(page, "post-page-inspect");
}

async function postMessage(page) {
  const s = config.selectors.post;
  const text = config.message.text;

  if (!text || text === "REPLACE_ME_WITH_YOUR_MESSAGE") {
    throw new Error(
      "config.json message.text is not set. Add your message before posting."
    );
  }
  if (text.length > 160) {
    log(`WARNING: message is ${text.length} chars; iha.ee limit is 160.`);
  }

  log("Opening post page:", config.site.postUrl);
  await page.goto(config.site.postUrl, { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("networkidle").catch(() => {});

  await page.fill(s.messageField, text);
  log("Filled message field.");

  if (DRY_RUN) {
    await screenshot(page, "dry-run-before-submit");
    log("DRY RUN — not submitting. Review the screenshot.");
    return;
  }

  const byText = page.getByRole("button", { name: s.submitText });
  if (await byText.count()) {
    await byText.first().click();
  } else {
    await page.click(s.submit);
  }
  await page.waitForLoadState("networkidle").catch(() => {});

  await screenshot(page, "after-submit");
  log("Submitted message.");
}

async function main() {
  const browser = await chromium.launch({ headless: !HEADFUL });
  const context = await browser.newContext();
  const page = await context.newPage();
  try {
    await login(page);
    if (INSPECT) {
      await inspect(page);
    } else {
      await postMessage(page);
    }
  } catch (err) {
    log("ERROR:", err.message);
    await screenshot(page, "error").catch(() => {});
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
}

main();
