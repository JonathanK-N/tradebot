// Service worker minimal : rend la PWA installable. AUCUNE mise en cache des données
// (un P&L périmé affiché comme actuel serait dangereux) : réseau uniquement.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
