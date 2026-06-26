// Long-running scheduler: runs the post job on the cron schedule in config.json.
// Use this if you keep the process alive (e.g. a VPS with pm2/systemd).
// Alternatively, skip this file and use a real system cron — see README.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { spawn } from "node:child_process";
import cron from "node-cron";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..");
const config = JSON.parse(readFileSync(join(ROOT, "config.json"), "utf8"));

const { cron: expr, timezone } = config.schedule;

function log(...args) {
  console.log(new Date().toISOString(), ...args);
}

function runPost() {
  log("Triggering daily post...");
  const child = spawn(process.execPath, [join(__dirname, "post.js")], {
    stdio: "inherit",
    cwd: ROOT,
  });
  child.on("exit", (code) => log(`Post job exited with code ${code}`));
}

if (!cron.validate(expr)) {
  throw new Error(`Invalid cron expression in config.json: ${expr}`);
}

log(`Scheduler started. Posting on "${expr}" (${timezone}).`);
cron.schedule(expr, runPost, { timezone });
