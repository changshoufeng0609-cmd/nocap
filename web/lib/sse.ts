/**
 * A small text/event-stream reader for fetch() bodies (EventSource can't POST).
 *
 * Follows the WHATWG parsing rules: lines end in \n, \r\n or \r; "event:" sets the type, each
 * "data:" line appends to the payload, a blank line dispatches, ":" lines are comments.
 * Chunks may split anywhere, including inside a UTF-8 sequence or between \r and \n.
 */
export interface SSEMessage {
  event: string;
  data: string;
  id?: string;
}

export class SSEParser {
  private buffer = "";
  private eventType = "";
  private dataLines: string[] = [];
  private lastId: string | undefined;
  private readonly onMessage: (msg: SSEMessage) => void;

  constructor(onMessage: (msg: SSEMessage) => void) {
    this.onMessage = onMessage;
  }

  /** Feed decoded text. A trailing \r is held back until we know whether \n follows. */
  push(text: string): void {
    this.buffer += text;
    let start = 0;
    for (let i = 0; i < this.buffer.length; i++) {
      const ch = this.buffer[i];
      if (ch !== "\n" && ch !== "\r") continue;
      if (ch === "\r") {
        if (i === this.buffer.length - 1) break; // maybe \r\n split across chunks
        this.line(this.buffer.slice(start, i));
        if (this.buffer[i + 1] === "\n") i++;
      } else {
        this.line(this.buffer.slice(start, i));
      }
      start = i + 1;
    }
    this.buffer = this.buffer.slice(start);
  }

  /** End of stream: finish a pending line and dispatch a final event even without a blank line. */
  end(): void {
    if (this.buffer.length) {
      this.line(this.buffer.replace(/\r$/, ""));
      this.buffer = "";
    }
    this.dispatch();
  }

  private line(line: string): void {
    if (line === "") {
      this.dispatch();
      return;
    }
    if (line.startsWith(":")) return;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") this.eventType = value;
    else if (field === "data") this.dataLines.push(value);
    else if (field === "id" && !value.includes("\0")) this.lastId = value;
  }

  private dispatch(): void {
    if (this.dataLines.length) {
      this.onMessage({ event: this.eventType || "message", data: this.dataLines.join("\n"), id: this.lastId });
    }
    this.eventType = "";
    this.dataLines = [];
  }
}

/** Read a fetch() body as SSE messages, in order, until the stream ends or the signal aborts. */
export async function* readSSE(body: ReadableStream<Uint8Array>, signal?: AbortSignal): AsyncGenerator<SSEMessage> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  const queue: SSEMessage[] = [];
  const parser = new SSEParser((m) => queue.push(m));
  let finished = false;
  const onAbort = () => {
    reader.cancel().catch(() => {});
  };
  signal?.addEventListener("abort", onAbort, { once: true });
  try {
    if (signal?.aborted) return;
    while (true) {
      const { value, done } = await reader.read();
      if (signal?.aborted) return;
      if (done) {
        finished = true;
        parser.push(decoder.decode());
        parser.end();
        while (queue.length && !signal?.aborted) yield queue.shift()!;
        return;
      }
      parser.push(decoder.decode(value, { stream: true }));
      while (queue.length && !signal?.aborted) yield queue.shift()!;
      if (signal?.aborted) return;
    }
  } finally {
    signal?.removeEventListener("abort", onAbort);
    // The consumer stopped early or the read failed: close the connection.
    if (!finished) reader.cancel().catch(() => {});
  }
}
