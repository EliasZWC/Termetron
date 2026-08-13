/**
 * Termetron VS Code extension public API.
 * Other extensions obtain it via:
 *   const tmt = vscode.extensions.getExtension('eliaszhang.termetron')?.exports;
 * TypeScript projects can reference this file (or import the type) for typing.
 */
export interface TermetronApi {
  /** Open the embedded terminal panel (starts the Python server if needed). */
  open(): Promise<void>;
  /** Open Termetron in the system default browser. */
  openInBrowser(): Promise<void>;
  /** Restart the Python server and reopen the panel. */
  restart(): Promise<void>;
  /** Stop the Python server. */
  stop(): Promise<void>;
  /** Current server state. */
  getStatus(): Promise<{ running: boolean; port: number | null }>;
  /** The port the server is listening on (or null if not running). */
  getPort(): Promise<number | null>;
  /** Send a command to a session (created on demand); output appears in the panel. */
  exec(session: string, command: string): Promise<{ ok: boolean; error?: string }>;
  /** Discover all local Termetron servers. */
  listServers(): Promise<ServerInfo[]>;
  /** Open the panel connected to the given local port (server must be running). */
  connect(port: number): Promise<void>;
}

/** Info about a discovered local Termetron server. */
export interface ServerInfo {
  port: number;
  sessions: string[];
  own: boolean;
}

export declare const api: TermetronApi;
