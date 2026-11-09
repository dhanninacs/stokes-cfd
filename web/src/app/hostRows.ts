/**
 * What a row of `host.list` means to the Settings screen.
 *
 * **The remote list is for SSH records, not every host that is not `local`.** WSL is discovered
 * rather than added, lives in no `hosts.json`, and `host.remove` cannot remove it - so listing it
 * under "Remote hosts" offered a Remove button that could only fail. Measured in Settings after
 * the WSL runner landed: the host appeared once in the Compute list and once in Remote hosts.
 */
export interface SshHostRow {
  name: string;
  host?: string;
  user?: string;
  port?: number;
  available?: boolean;
  why?: string;
  runner?: string;
}

/** The SSH hosts only: not this computer, and not a discovered WSL distribution. */
export function isRemoteHost(host: SshHostRow): boolean {
  const runner = host.runner ?? "";
  return host.name !== "local" && runner !== "local" && !host.name.startsWith("wsl:") && !runner.startsWith("wsl:");
}
