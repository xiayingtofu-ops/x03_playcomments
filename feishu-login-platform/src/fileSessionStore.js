import fs from "node:fs/promises";
import path from "node:path";
import session from "express-session";

export class FileSessionStore extends session.Store {
  constructor({ filePath, ttlMs = 24 * 60 * 60 * 1000 }) {
    super();
    this.filePath = filePath;
    this.ttlMs = ttlMs;
    this.ready = this.ensureFile();
  }

  async ensureFile() {
    await fs.mkdir(path.dirname(this.filePath), { recursive: true });
    try {
      await fs.access(this.filePath);
    } catch {
      await fs.writeFile(this.filePath, "{}", "utf8");
    }
  }

  async readAll() {
    await this.ready;
    try {
      return JSON.parse(await fs.readFile(this.filePath, "utf8"));
    } catch {
      return {};
    }
  }

  async writeAll(sessions) {
    await this.ready;
    await fs.writeFile(this.filePath, JSON.stringify(sessions, null, 2), "utf8");
  }

  isExpired(record) {
    return !record?.expiresAt || record.expiresAt <= Date.now();
  }

  async get(sid, callback) {
    try {
      const sessions = await this.readAll();
      const record = sessions[sid];

      if (!record || this.isExpired(record)) {
        delete sessions[sid];
        await this.writeAll(sessions);
        callback(null, null);
        return;
      }

      callback(null, record.session);
    } catch (error) {
      callback(error);
    }
  }

  async set(sid, value, callback = () => {}) {
    try {
      const sessions = await this.readAll();
      const maxAge = value.cookie?.maxAge || this.ttlMs;
      sessions[sid] = {
        session: value,
        expiresAt: Date.now() + maxAge
      };
      await this.writeAll(this.compact(sessions));
      callback(null);
    } catch (error) {
      callback(error);
    }
  }

  async destroy(sid, callback = () => {}) {
    try {
      const sessions = await this.readAll();
      delete sessions[sid];
      await this.writeAll(sessions);
      callback(null);
    } catch (error) {
      callback(error);
    }
  }

  compact(sessions) {
    return Object.fromEntries(
      Object.entries(sessions).filter(([, record]) => !this.isExpired(record))
    );
  }
}
