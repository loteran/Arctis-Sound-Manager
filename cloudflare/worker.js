/**
 * ASM Telemetry — Cloudflare Worker + D1
 *
 * Routes:
 *   POST /collect   — receive anonymous usage data
 *   GET  /stats     — return aggregated public stats (JSON)
 *   GET  /          — HTML stats dashboard
 *
 * Deploy:
 *   1. Install Wrangler:  npm install -g wrangler
 *   2. Login:             wrangler login
 *   3. Create D1 db:      wrangler d1 create asm-telemetry
 *      → copy the database_id into wrangler.toml
 *   4. Apply schema:      wrangler d1 execute asm-telemetry --file=schema.sql
 *   5. Deploy:            wrangler deploy
 */

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // CORS pre-flight
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS });
    }

    // ── POST /collect ──────────────────────────────────────────────────────────
    if (request.method === "POST" && url.pathname === "/collect") {
      let body;
      try {
        body = await request.json();
      } catch {
        return new Response("Bad JSON", { status: 400 });
      }

      const distro     = String(body.distro     || "Unknown").slice(0, 120);
      const headset    = String(body.headset    || "Unknown").slice(0, 120);
      const product_id = String(body.product_id || "Unknown").slice(0, 30);
      const version    = String(body.version    || "Unknown").slice(0, 30);
      const now        = Date.now();

      await Promise.all([
        // Historical log — always append
        env.DB.prepare(
          "INSERT INTO stats (distro, headset, product_id, version, ts) VALUES (?, ?, ?, ?, ?)"
        ).bind(distro, headset, product_id, version, now).run(),

        // Unique users — upsert: create on first contact, update version + last_seen on return
        env.DB.prepare(`
          INSERT INTO users (distro, headset, product_id, version, first_seen, last_seen)
          VALUES (?, ?, ?, ?, ?, ?)
          ON CONFLICT(distro, headset) DO UPDATE SET
            product_id = excluded.product_id,
            version    = excluded.version,
            last_seen  = excluded.last_seen
        `).bind(distro, headset, product_id, version, now, now).run(),
      ]);

      return new Response("ok", { headers: CORS });
    }

    // ── GET /stats (JSON API) ──────────────────────────────────────────────────
    if (request.method === "GET" && url.pathname === "/stats") {
      const [distros, headsets, versions, totalRow, uniqueRow] = await Promise.all([
        env.DB.prepare(
          "SELECT distro AS label, COUNT(*) AS nb FROM stats GROUP BY distro ORDER BY nb DESC LIMIT 30"
        ).all(),
        env.DB.prepare(
          "SELECT headset AS label, product_id, COUNT(*) AS nb FROM stats GROUP BY headset, product_id ORDER BY nb DESC LIMIT 30"
        ).all(),
        // Versions counted per unique user (current version only, updates replace old)
        env.DB.prepare(
          "SELECT version AS label, COUNT(*) AS nb FROM users GROUP BY version ORDER BY nb DESC LIMIT 20"
        ).all(),
        env.DB.prepare("SELECT COUNT(*) AS nb FROM stats").first(),
        env.DB.prepare("SELECT COUNT(*) AS nb FROM users").first(),
      ]);

      return Response.json(
        {
          total:        totalRow?.nb ?? 0,
          unique_users: uniqueRow?.nb ?? 0,
          distros:      distros.results,
          headsets:     headsets.results,
          versions:     versions.results,
          generated_at: new Date().toISOString(),
        },
        { headers: { ...CORS, "Cache-Control": "public, max-age=3600" } }
      );
    }

    // ── GET / (HTML dashboard) ─────────────────────────────────────────────────
    if (request.method === "GET" && url.pathname === "/") {
      return new Response(HTML_DASHBOARD, {
        headers: { "Content-Type": "text/html; charset=utf-8" },
      });
    }

    return new Response("Not found", { status: 404 });
  },
};

// Minimal inline dashboard — no external dependencies
const HTML_DASHBOARD = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ASM — Anonymous Usage Stats</title>
<style>
  body { font-family: system-ui, sans-serif; background: #16191E; color: #C8C8C8;
         max-width: 900px; margin: 2rem auto; padding: 0 1rem; }
  h1   { color: #FB4A00; }
  h2   { color: #8D96AA; font-size: 1rem; text-transform: uppercase; letter-spacing: .05em; }
  .grid{ display: grid; grid-template-columns: 1fr 1fr; gap: 2rem; }
  table{ width: 100%; border-collapse: collapse; }
  th,td{ text-align: left; padding: .4rem .6rem; border-bottom: 1px solid #2A3038; }
  th   { color: #FB4A00; font-weight: 600; }
  .nb  { text-align: right; color: #04C5A8; font-weight: bold; }
  .bar { display: inline-block; background: #FB4A00; height: 10px; border-radius: 3px; }
  small{ color: #8D96AA; }
  .counters { display: flex; gap: 2rem; margin-bottom: 1.5rem; }
  .counter  { background: #1E2228; border-radius: 8px; padding: 1rem 1.5rem; }
  .counter .val { font-size: 2rem; font-weight: bold; color: #FB4A00; }
  .counter .lbl { font-size: .8rem; color: #8D96AA; text-transform: uppercase; }
</style>
</head>
<body>
<h1>Arctis Sound Manager — Anonymous Usage Stats</h1>
<div class="counters">
  <div class="counter"><div class="val" id="cnt-total">…</div><div class="lbl">Data points</div></div>
  <div class="counter"><div class="val" id="cnt-unique">…</div><div class="lbl">Unique users</div></div>
</div>
<div class="grid">
  <div>
    <h2>Linux Distributions</h2>
    <table id="distros"><tr><td>Loading…</td></tr></table>
  </div>
  <div>
    <h2>Headsets</h2>
    <table id="headsets"><tr><td>Loading…</td></tr></table>
  </div>
</div>
<br>
<div>
  <h2>ASM Versions</h2>
  <table id="versions"><tr><td>Loading…</td></tr></table>
</div>
<br>
<small>Data is anonymous — no personal data or IP address is stored.
       Stats show each user's current version only (updated on each launch).
       Stats are cached for 1 hour. · <a href="https://github.com/loteran/Arctis-Sound-Manager"
       style="color:#FB4A00">GitHub</a></small>

<script>
fetch('/stats')
  .then(r => r.json())
  .then(data => {
    document.getElementById('cnt-total').textContent  = data.total;
    document.getElementById('cnt-unique').textContent = data.unique_users;

    const fill = (id, rows) => {
      const max = rows[0]?.nb || 1;
      document.getElementById(id).innerHTML =
        '<tr><th>Name</th><th class=nb>Count</th><th></th></tr>' +
        rows.map(r =>
          '<tr><td>' + r.label + '</td><td class=nb>' + r.nb + '</td>' +
          '<td><span class=bar style="width:' + Math.round(r.nb/max*120) + 'px"></span></td></tr>'
        ).join('');
    };
    const fillHeadsets = (id, rows) => {
      const max = rows[0]?.nb || 1;
      document.getElementById(id).innerHTML =
        '<tr><th>Name</th><th>PID</th><th class=nb>Count</th><th></th></tr>' +
        rows.map(r =>
          '<tr><td>' + r.label + '</td><td><code>0x' + r.product_id + '</code></td><td class=nb>' + r.nb + '</td>' +
          '<td><span class=bar style="width:' + Math.round(r.nb/max*120) + 'px"></span></td></tr>'
        ).join('');
    };
    fill('distros',  data.distros);
    fillHeadsets('headsets', data.headsets);
    fill('versions', data.versions);
  })
  .catch(() => {
    document.getElementById('cnt-total').textContent = 'Could not load statistics. The endpoint may be unreachable.';
  });
</script>
</body>
</html>`;
