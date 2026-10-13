import type { ChildProcess } from "node:child_process";
import { readFileSync } from "node:fs";

function descendants(pid: number): number[] {
  try {
    const children = readFileSync(`/proc/${pid}/task/${pid}/children`, "utf8").trim().split(/\s+/).filter(Boolean).map(Number);

    return children.flatMap(child => [...descendants(child), child]);
  } catch {
    return [];
  }
}

export async function stopChrome(proc: ChildProcess): Promise<void> {
  if (proc.pid === undefined) return;

  const children = descendants(proc.pid);

  if (proc.exitCode === null && proc.signalCode === null) {
    await new Promise<void>(resolve => {
      const timer = setTimeout(() => proc.kill("SIGKILL"), 2000);

      proc.once("exit", () => {
        clearTimeout(timer);
        resolve();
      });
      proc.kill("SIGTERM");
    });
  }

  for (const pid of children) {
    try {
      process.kill(pid, "SIGKILL");
    } catch {
    }
  }
}
