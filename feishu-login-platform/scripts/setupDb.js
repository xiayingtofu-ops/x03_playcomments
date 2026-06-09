import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { closeDb, query } from "../src/db.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const rootDir = path.resolve(__dirname, "..");

try {
  const sql = await readFile(path.join(rootDir, "migrations", "001_base_sync_schema.sql"), "utf8");
  await query(sql);
  console.log("Database schema is ready.");
} finally {
  await closeDb();
}
