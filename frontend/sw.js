/* Painel sem internet (só funciona por HTTPS: o endereço .ts.net do Tailscale).
   Tudo vem primeiro da rede, para nunca mostrar versão velha; sem rede (ou se ela demorar), usa a última cópia
   guardada. Escritas (salvar página etc.) não passam por aqui: a fila fica no painel (painel.html). */
const CACHE = "hub-painel";
const SHELL = ["/painel", "/manifest.webmanifest", "/logo.svg", "/icon-180.png", "/icon-192.png",
               "/fonts/BricolageGrotesque.ttf"];
const TIMEOUT_MS = 4000;  // sinal fraco: depois disso, mostra a cópia guardada
// Dados que fazem sentido sem internet; o resto (status ao vivo, busca de livros) precisa do servidor
const API_CACHED = ["/api/library", "/api/shelf", "/api/settings", "/api/stats", "/api/history", "/api/devices", "/api/pin"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {}).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

async function networkFirst(request) {
  const cache = await caches.open(CACHE);
  const net = fetch(request).then((res) => {
    if (res.ok) cache.put(request, res.clone());
    return res;
  });
  const slow = new Promise((resolve) => setTimeout(resolve, TIMEOUT_MS));
  try {
    const res = await Promise.race([net, slow]);
    if (res) return res;
  } catch { /* sem rede */ }
  const saved = await cache.match(request, { ignoreVary: true });
  if (saved) return saved;
  return net;  // nada guardado: espera a rede mesmo (ou falha)
}

async function cacheFirst(request) {
  const cache = await caches.open(CACHE);
  const saved = await cache.match(request);
  if (saved) return saved;
  const res = await fetch(request);
  if (res.ok) cache.put(request, res.clone());
  return res;
}

self.addEventListener("fetch", (e) => {
  const { request } = e;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/covers/")) { e.respondWith(cacheFirst(request)); return; }  // capas mudam de ?v=
  if (url.pathname.startsWith("/api/")) {
    if (API_CACHED.some((p) => url.pathname === p || url.pathname.startsWith(p + "/"))) e.respondWith(networkFirst(request));
    return;  // /api/stream, /api/status, catálogo: direto na rede
  }
  if (request.mode === "navigate" && url.pathname.startsWith("/painel")) { e.respondWith(networkFirst(new Request("/painel"))); return; }
  if (request.mode !== "navigate") e.respondWith(networkFirst(request));  // fontes, ícones
});
