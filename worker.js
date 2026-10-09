// worker.js — Cloudflare Worker: WebSocket -> TCP bridge to Telegram DCs
// Health: GET /    WS: GET /apiws?dst=<ip>&port=443

const ALLOWED_DCS = new Set([
  "149.154.175.50",
  "149.154.167.51",
  "149.154.175.100",
  "149.154.167.91",
  "149.154.171.5",
  "91.105.192.100",
]);
const ALLOWED_PORTS = new Set([443, 80, 5222]);

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (url.pathname === "/" || url.pathname === "/health") {
      return new Response(
        JSON.stringify({
          status: "ok",
          service: "cf-ws-tcp-bridge",
          time: new Date().toISOString(),
          allowed_dcs: [...ALLOWED_DCS],
        }),
        { headers: { "content-type": "application/json" } }
      );
    }

    if (url.pathname !== "/apiws") {
      return new Response("Not Found", { status: 404 });
    }

    const dst = url.searchParams.get("dst");
    const port = parseInt(url.searchParams.get("port") || "443", 10);

    if (!dst || !ALLOWED_DCS.has(dst)) {
      return new Response("Forbidden: dst not allowed", { status: 403 });
    }
    if (!ALLOWED_PORTS.has(port)) {
      return new Response("Forbidden: port not allowed", { status: 403 });
    }

    const upgrade = request.headers.get("Upgrade");
    if (!upgrade || upgrade.toLowerCase() !== "websocket") {
      return new Response("Expected WebSocket upgrade", { status: 426 });
    }

    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair);
    server.accept();

    let tcp;
    try {
      tcp = connect(`${dst}:${port}`, { allowHalfOpen: false });
    } catch (err) {
      try { server.close(1011, `tcp connect failed: ${err.message}`); } catch (_) {}
      return new Response("TCP connect failed", { status: 502 });
    }

    let closed = false;
    const safeClose = (ws, code, reason) => {
      if (closed) return;
      closed = true;
      try { ws.close(code, reason); } catch (_) {}
    };

    server.addEventListener("message", async (ev) => {
      try {
        const writer = tcp.writable.getWriter();
        const data =
          typeof ev.data === "string"
            ? new TextEncoder().encode(ev.data)
            : new Uint8Array(ev.data);
        await writer.write(data);
        writer.releaseLock();
      } catch (err) {
        safeClose(server, 1011, `write error: ${err.message}`);
      }
    });

    (async () => {
      try {
        const reader = tcp.readable.getReader();
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          if (value && value.byteLength) server.send(value);
        }
      } catch (_) {
      } finally {
        safeClose(server, 1000, "tcp closed");
      }
    })();

    server.addEventListener("close", () => {
      safeClose(server, 1000, "ws closed");
      try { tcp.close(); } catch (_) {}
    });
    server.addEventListener("error", () => {
      try { tcp.close(); } catch (_) {}
    });

    return new Response(null, { status: 101, webSocket: client });
  },
};