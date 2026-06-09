import fs from "node:fs/promises";
import path from "node:path";

export class UserStore {
  constructor(filePath) {
    this.filePath = filePath;
    this.ready = this.ensureFile();
  }

  async ensureFile() {
    await fs.mkdir(path.dirname(this.filePath), { recursive: true });
    try {
      await fs.access(this.filePath);
    } catch {
      await fs.writeFile(this.filePath, "[]", "utf8");
    }
  }

  async readAll() {
    await this.ready;
    try {
      return JSON.parse(await fs.readFile(this.filePath, "utf8"));
    } catch {
      return [];
    }
  }

  async upsert(user) {
    const users = await this.readAll();
    const key = user.openId || user.unionId || user.userId || user.email;
    const index = users.findIndex((entry) =>
      [entry.openId, entry.unionId, entry.userId, entry.email].includes(key)
    );
    const savedUser = {
      ...user,
      lastLoginAt: new Date().toISOString()
    };

    if (index >= 0) {
      users[index] = { ...users[index], ...savedUser };
    } else {
      users.push({ ...savedUser, firstLoginAt: new Date().toISOString() });
    }

    await fs.writeFile(this.filePath, JSON.stringify(users, null, 2), "utf8");
    return savedUser;
  }
}
