import { execFileSync } from "node:child_process";

const ports = process.argv.slice(2).map((value) => Number(value));

if (
  ports.length === 0 ||
  ports.some(
    (port) => !Number.isInteger(port) || port < 1 || port > 65_535,
  )
) {
  console.error("用法：node scripts/free-dev-ports.mjs <端口> [端口…]");
  process.exit(1);
}

function listenerPids(port) {
  try {
    const output = execFileSync(
      "lsof",
      ["-nP", `-tiTCP:${port}`, "-sTCP:LISTEN"],
      { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] },
    );
    return [
      ...new Set(
        output
          .split(/\s+/)
          .map((value) => Number(value))
          .filter(
            (pid) =>
              Number.isInteger(pid) && pid > 0 && pid !== process.pid,
          ),
      ),
    ];
  } catch (error) {
    if (error && typeof error === "object" && error.status === 1) {
      return [];
    }
    throw error;
  }
}

function isRunning(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error?.code !== "ESRCH";
  }
}

function sendSignal(pid, signal, port) {
  try {
    process.kill(pid, signal);
    return true;
  } catch (error) {
    if (error?.code === "ESRCH") return false;
    if (error?.code === "EPERM") {
      console.error(
        `没有权限停止旧进程 ${pid}（端口 ${port}）。请在原终端按 Ctrl+C 后重试。`,
      );
      process.exit(1);
    }
    throw error;
  }
}

async function waitForExit(processes, attempts) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    if (processes.every(({ pid }) => !isRunning(pid))) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
}

const stopped = [];
for (const port of ports) {
  for (const pid of listenerPids(port)) {
    console.log(`端口 ${port} 已占用，正在停止旧进程 ${pid}…`);
    if (sendSignal(pid, "SIGTERM", port)) {
      stopped.push({ pid, port });
    }
  }
}

await waitForExit(stopped, 30);

const forceStopped = stopped.filter(({ pid }) => isRunning(pid));
for (const { pid, port } of forceStopped) {
  console.warn(`旧进程 ${pid} 未能正常退出，正在强制结束…`);
  sendSignal(pid, "SIGKILL", port);
}

await waitForExit(forceStopped, 20);

const occupied = ports.flatMap((port) =>
  listenerPids(port).map((pid) => ({ pid, port })),
);
if (occupied.length > 0) {
  for (const { pid, port } of occupied) {
    console.error(`旧进程 ${pid} 未能结束，端口 ${port} 仍被占用。`);
  }
  process.exit(1);
}

if (stopped.length > 0) {
  console.log("旧服务已停止，正在启动新服务。");
}
