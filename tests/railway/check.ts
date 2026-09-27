// Test de l'infrastructure Railway : exécute le VRAI programme .railway/railway.ts avec le
// SDK officiel et vérifie ce qu'il produit.  npm run test:railway
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { test } from "node:test";

import {
  RAILWAY_GRAPH_VERSION,
  createRailwayContext,
  project,
  validateGraph,
  type ResourceNode,
  type ServiceNode,
  type VariableValue,
} from "railway/iac";

import program, { BRANCH, REPO } from "../../.railway/railway.ts";

type AnyNode = ResourceNode | AnyNode[];

function flatten(items: AnyNode[] = []): ResourceNode[] {
  return items.flatMap((i) => (Array.isArray(i) ? flatten(i) : [i]));
}

async function load(environment = "production") {
  const ctx = createRailwayContext({ environment, command: "plan", projectName: "tradebot" });
  const def = await program(ctx, project);
  const nodes = flatten(def.resources as AnyNode[]);
  const services = new Map(
    nodes.filter((n): n is ServiceNode => n.type === "service").map((n) => [n.name, n]),
  );
  return { def, nodes, services };
}

const SECRET_NAME = /(TOKEN|SECRET|PASSWORD|KEY|CHAT_IDS|HEALTHCHECK_URL)/;

test("ressources attendues", async () => {
  const { nodes, services } = await load();
  assert.deepEqual([...services.keys()].sort(), ["api", "bot", "live"]);
  const dbs = nodes.filter((n) => n.type === "database").map((n) => (n as { engine: string }).engine).sort();
  assert.deepEqual(dbs, ["postgres", "redis"]);
  assert.ok(nodes.some((n) => n.type === "volume" && n.name === "tradebot-state"));
});

test("chaque service : dépôt GitHub, branche main, build Dockerfile", async () => {
  const { services } = await load();
  for (const [name, s] of services) {
    assert.equal(s.source?.repo, REPO, name);
    assert.equal(s.source?.branch, BRANCH, name);
    assert.equal(s.build?.builder, "DOCKERFILE", name);
    assert.equal(s.build?.dockerfilePath, "Dockerfile", name);
    assert.ok(existsSync(s.build!.dockerfilePath!), "Dockerfile introuvable");
    assert.equal(s.deploy?.restartPolicyType, "ON_FAILURE", name);
  }
});

test("commandes de démarrage et healthcheck", async () => {
  const { services } = await load();
  assert.equal(services.get("api")!.deploy?.startCommand, "tradebot api --host 0.0.0.0");
  assert.equal(services.get("api")!.deploy?.healthcheckPath, "/livez");
  assert.equal(services.get("bot")!.deploy?.startCommand, "tradebot bot");
  assert.equal(services.get("live")!.deploy?.startCommand, "tradebot live");
  // les workers n'exposent pas de HTTP : un healthcheck les ferait échouer au déploiement
  assert.equal(services.get("bot")!.deploy?.healthcheckPath ?? null, null);
  assert.equal(services.get("live")!.deploy?.healthcheckPath ?? null, null);
});

test("le volume n'est monté que sur « live », en /app/var", async () => {
  const { services } = await load();
  const mounted = [...services].filter(([, s]) => {
    const m = { ...(s.volumeMounts ?? {}), ...(s.volumeAttachments ?? {}) };
    return Object.keys(m).length > 0;
  });
  assert.deepEqual(mounted.map(([n]) => n), ["live"]);
  const live = services.get("live")!;
  const paths = [
    ...Object.keys(live.volumeMounts ?? {}),
    ...Object.values(live.volumeAttachments ?? {}).map((a) => a.mountPath),
  ];
  assert.ok(paths.includes("/app/var"), `points de montage : ${paths}`);
  const uid = live.variables?.RAILWAY_RUN_UID as VariableValue & { value?: string };
  assert.equal(uid?.type, "literal");
  assert.equal(uid?.value, "0");
});

test("aucun secret en clair dans le fichier", async () => {
  const { services } = await load();
  for (const [name, s] of services) {
    for (const [key, v] of Object.entries(s.variables ?? {})) {
      if (SECRET_NAME.test(key)) {
        assert.equal(v.type, "sharedReference", `${name}.${key} doit référencer une shared variable`);
      }
      if (key === "DATABASE_URL" || key === "REDIS_URL") {
        assert.equal(v.type, "reference", `${name}.${key} doit référencer la base Railway`);
      }
    }
  }
});

test("moindre privilège : chaque service ne reçoit que ses secrets", async () => {
  const { services } = await load();
  const keys = (n: string) => Object.keys(services.get(n)!.variables ?? {});
  assert.ok(!keys("api").includes("TELEGRAM_BOT_TOKEN"));
  assert.ok(!keys("bot").includes("API_TOKEN"));
  assert.ok(!keys("live").includes("API_TOKEN"));
  assert.ok(!keys("live").includes("TOTP_SECRET"));
  for (const n of ["api", "bot", "live"]) {
    for (const k of ["TRADEBOT_CONFIG", "DATABASE_URL", "REDIS_URL"]) assert.ok(keys(n).includes(k), `${n}.${k}`);
    const cfg = services.get(n)!.variables!.TRADEBOT_CONFIG as VariableValue & { value?: string };
    assert.ok(existsSync(cfg.value!), `${cfg.value} introuvable`);
  }
});

test("le graphe est valide selon le SDK Railway", async () => {
  const { def, nodes } = await load();
  const errors = validateGraph({
    version: RAILWAY_GRAPH_VERSION,
    project: { name: def.name },
    environments: [{ name: "production" }],
    resources: nodes,
    edges: [],
  });
  assert.deepEqual(errors, []);
});

test("même résultat pour un autre environnement (pas de logique cachée)", async () => {
  const a = await load("production");
  const b = await load("staging");
  assert.deepEqual([...a.services.keys()].sort(), [...b.services.keys()].sort());
});
