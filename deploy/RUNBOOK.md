# Public demo runbook

One VM, `docker-compose.prod.yml` + `docker-compose.demo.yml`, Caddy with automatic HTTPS, Supabase email + password sign-up with an emailed activation link (email-code sign-in kept as a second way in), Resend for the email. You create every account and enter every secret.

`<DEMO_DOMAIN>` below is the hostname the demo is served on (e.g. `demo.example.com`).

## 1. VM

1. Create an Ubuntu LTS VM with at least 4 vCPU / 8GB and 40GB of disk. The LaTeX compiler image is TeX Live scheme-full (about 6GB on disk, built once, which takes a while on the first deploy), and each compile may use up to 2 CPUs and 2GB of memory.
2. Install Docker Engine and the compose plugin (https://docs.docker.com/engine/install/ubuntu/), and `make` and `git`: `apt install -y make git` (Ubuntu cloud images lack `make`; `make demo-up` and the backup cron need it).
3. Firewall: `ufw allow 22 && ufw allow 80 && ufw allow 443 && ufw enable`. Nothing else is open (the database and backend publish no ports).
4. The only container on ports 80/443 is the **edge Caddy** (`edge-caddy`, its own stack at `/srv/edge`; section 12). `docker ps --format '{{.Names}} {{.Ports}}' | grep -E ':(80|443)->'` must show it and nothing else. This stack publishes no ports.
5. Create the shared proxy network and sites directory, once: `docker network create proxy && mkdir -p /srv/edge/sites`. `make demo-up` fails with `network proxy declared as external, but could not be found` until the network exists.
6. As root (`sudo -i`; `/srv` is root-owned, so a plain `git clone` there is refused): `git clone https://github.com/KadircanKara/ResearcherX.git /srv/researcherx && cd /srv/researcherx && git checkout main`.
7. Give Docker real DNS servers. On OVH (and other images whose `/etc/resolv.conf` points at systemd-resolved's `127.0.0.53`), containers inherit that address, cannot reach it, and Caddy can never get a certificate (`lookup acme-v02.api.letsencrypt.org on 127.0.0.53:53: ... connection refused`). If `/etc/docker/daemon.json` does not exist: `echo '{"dns": ["1.1.1.1", "8.8.8.8"]}' > /etc/docker/daemon.json && systemctl restart docker` (merge the `dns` key in instead if the file exists).

## 2. DNS

1. Add an `A` record for `<DEMO_DOMAIN>` pointing at the VM's public IP.
2. Wait until `dig +short <DEMO_DOMAIN>` returns that IP. Caddy cannot get a certificate before it does.

## 3. Resend

1. Create a Resend account and add the domain. Verify exactly `<DEMO_DOMAIN>` (the same host the sender `no-reply@<DEMO_DOMAIN>` uses), not only the apex: if the demo is on a subdomain and only the apex is verified, sending fails. Alternatively set the sender to the address of the domain you did verify.
2. Add the SPF and DKIM records it shows to your DNS and wait for "Verified".
3. API Keys → Create API Key: permission "Sending access", restricted to the verified domain. Resend has no separate SMTP credential: this key is the SMTP password in section 4, step 2 (host `smtp.resend.com`, port `465`, user `resend`). It is shown once.
4. Turn link tracking **off** (it rewrites links and can break or delay the email).

## 4. Supabase (new free project)

1. Authentication → Sign In / Providers → Email: enabled; "Allow new users to sign up" **on**; "Confirm email" **on** (this is what makes an account inactive until its activation link is opened -- never turn it off: an unconfirmed address would then sign straight in); "Secure email change" **on**; "Secure password change" on. Password requirements: minimum length **10** (the sign-up page says 10; `PASSWORD_MIN` in `frontend/src/lib/auth-flow.ts`).
2. Authentication → Emails → SMTP: custom SMTP pointed at Resend, sender `no-reply@<DEMO_DOMAIN>`, name "ResearcherX".
3. Authentication → Emails → templates. The two link templates point at the app's OWN pages (`/auth/confirm`, `/auth/reset`), which verify the token by script: the link then works in any browser, and a mail scanner that pre-opens it cannot spend it. Do not put `{{ .ConfirmationURL }}` back.
   - "Confirm signup": paste `deploy/supabase/confirm-email.html`, subject "Activate your ResearcherX account".
   - "Reset password": paste `deploy/supabase/reset-email.html`, subject "Reset your ResearcherX password".
   - "Magic link or OTP": paste `deploy/supabase/otp-email.html`, subject "Your ResearcherX sign-in code".
4. Authentication → Sign In / Providers → Email: Email OTP Expiration `3600` seconds, **Email OTP Length `6`**. New projects default to 8, and the sign-in page accepts only 6 digits.
5. URL Configuration: Site URL `https://<DEMO_DOMAIN>` (the email templates build their links from it); Redirect URLs: `https://<DEMO_DOMAIN>/auth/confirm` and `https://<DEMO_DOMAIN>/auth/reset`.
   Because the templates use the Site URL, testing sign-up locally needs a separate Supabase project whose Site URL is `http://localhost:3000`.
5a. **Attack Protection.** Sign-ups are public, and every account spends paid LLM, embedding and rerank calls.
   - CAPTCHA: create a Cloudflare Turnstile widget for `<DEMO_DOMAIN>` (Cloudflare dashboard → Turnstile), then Authentication → Attack Protection → enable CAPTCHA, provider Turnstile, paste the **secret** key. The **site** key goes in `TURNSTILE_SITE_KEY` (section 5). Enable both together: with the dashboard on and no site key in the build, every sign-up and sign-in fails with "The verification check failed".
   - Rate Limits: "emails sent per hour" at or below what Resend allows (free tier: 100/day); keep the token-verification and sign-up limits at their defaults.
   - The per-user daily limits in `docker-compose.demo.yml` and `DEMO_MAX_USERS` are the spending ceiling: total daily spend is roughly users × per-user limits. Raise `DEMO_MAX_USERS` deliberately.
6. **JWT signing keys.** The backend verifies access tokens against the project's public keys, so the project must sign with an asymmetric key (ES256 or RS256): Settings → JWT Keys / signing keys, and make sure an ES256 or RS256 key is the current signing key. With the legacy HS256 shared secret, every login succeeds and then loops back to `/login` with no message. Check this **before the first login**.
7. SQL editor: create the keep-alive table (used by the cron in section 8; it holds no data and anon can only read it):
   ```sql
   create table public.keepalive (id int primary key);
   insert into public.keepalive values (1);
   alter table public.keepalive enable row level security;
   create policy "anon can read keepalive" on public.keepalive for select to anon using (true);
   grant select on public.keepalive to anon;
   ```
8. Project Settings → API: copy the Project URL and the anon (publishable) key.

## 5. Secrets on the VM

1. Create `/etc/researcherx-demo.env` (mode `600`, owner root) with these variables, one `NAME='value'` per line, no `export`. **Single-quote every value**: the file is shell-sourced, so an unquoted `$`, space or backtick would be expanded:
   `POSTGRES_PASSWORD` (random: `openssl rand -hex 32`; it is the password of the demo's own Postgres container, set on first start and never read from anywhere else), `LLM_API_KEY` (OpenAI), `EMBEDDING_API_KEY` (OpenAI), `COHERE_API_KEY`, `OWNER_API_KEY` (random: `openssl rand -hex 32`), `SUPABASE_URL` (`https://<project-ref>.supabase.co`), `SUPABASE_ANON_KEY` (the publishable or anon key, never the secret/service_role key) `DEMO_DOMAIN` (hostname only, no `https://`), and optionally `TURNSTILE_SITE_KEY` (the Turnstile **site** key from 5a; public, baked into the frontend).

   Create the file already locked down, as root: `install -m 600 -o root -g root /dev/null /etc/researcherx-demo.env`, then edit it with `nano` as root. Long keys pasted through an editor get truncated easily; write them without one instead, so they are never echoed:
   ```bash
   read -rsp 'OpenAI key: ' K; echo
   printf "LLM_API_KEY='%s'\nEMBEDDING_API_KEY='%s'\n" "$K" "$K" >> /etc/researcherx-demo.env; unset K
   ```
   Check it before deploying: `bash -n /etc/researcherx-demo.env` prints nothing when the syntax is valid, and `cut -d= -f1 /etc/researcherx-demo.env` lists the names without the values. Every `docker compose` command for the demo (including `logs` and `ps`) needs this file loaded in the same shell first, or it fails with `required variable ... is missing a value`.
2. **Start on a fresh database.** The demo must not reuse a dev database or `data/postgres` from a dev machine. `AUTH_MODE=supabase` never seeds users, so a fresh database has no seed users; a database carried over from dev would hold the dev seed accounts and their projects.
3. Deploy as root (`sudo -i` first; the env file is mode 600, root only): `cd /srv/researcherx && set -a; . /etc/researcherx-demo.env; set +a; make demo-up`. This starts ResearcherX only; it publishes no ports, so the site is not reachable yet. On a fresh VM with no edge Caddy running, install it next, still as root from `/srv/researcherx`: `deploy/edge/edge-cutover.sh install` (section 12). On a VM that already runs the edge Caddy, skip this; it picks the app up on its own.
4. Health check: `curl -fsS https://<DEMO_DOMAIN>/v1/health`.
5. `SUPABASE_ANON_KEY`, `SUPABASE_URL` and `TURNSTILE_SITE_KEY` are baked into the frontend image at build time; if you change any, redeploy (step 3 rebuilds).
6. FastAPI's `/docs`, `/redoc` and `/openapi.json` are not exposed: Caddy proxies only `/v1/*` to the backend, and everything else goes to the frontend.

## 6. Accounts

1. People sign up themselves at `https://<DEMO_DOMAIN>/signup`: email + password, then the activation link. Until it is opened the account cannot sign in ("Activate your account first"), and the login page offers to resend it.
2. Forgotten password: `/forgot-password` emails a reset link to `/auth/reset`.
3. Accounts created by hand before sign-up opened have an unknown random password: those users use "Forgot password?" once, or "Email me a sign-in code instead".
4. **A deleted account's email must not reach its old data.** Re-registering the address gives a new Supabase `sub`. Before the security-audit fixes (PR #38) are deployed, the backend relinks by email and the new owner of the address would get the old papers and chats; after them, it refuses unless `AUTH_RELINK_BY_EMAIL=true`. Either way, delete a removed user's data (section 7) before the address can come back.
5. The demo admits at most `DEMO_MAX_USERS` accounts; the next sign-up gets "The demo is full." on its first request after activation.

## 7. Remove an account

1. Supabase → Users → the user → Delete (or Ban).
2. Their existing sessions end when the access token expires, within about an hour.
3. Their papers stay in our database; remove them by hand if needed.

## 8. Cron (`crontab -e` as root; the lines go in root's crontab)

```
0 3 * * * cd /srv/researcherx && set -a && . /etc/researcherx-demo.env && set +a && make demo-backup
0 12 * * * set -a && . /etc/researcherx-demo.env && set +a && curl -fsS "$SUPABASE_URL/rest/v1/keepalive?select=id&limit=1" -H "apikey: $SUPABASE_ANON_KEY" >/dev/null
```

The first is the nightly DB dump (kept 7 days in `/srv/researcherx/backups`, mode 600; a failed dump leaves the previous dumps untouched). The second is a daily read of the keep-alive table through the public anon key, to avoid the free-tier inactivity pause (free projects pause after 7 idle days); if the project pauses anyway, restore it from the dashboard (see section 11, step 1). Never use the service_role key here. Copy backups off the VM occasionally.

After deploy, run both cron commands once by hand (as root, without the `>/dev/null`) and confirm they succeed; under cron their output is discarded.

## 9. Updating the demo

1. `cd /srv/researcherx && git pull`.
2. Run the deploy line from section 5, step 3. This restarts only ResearcherX; the edge Caddy and every other app keep serving (the site is briefly unavailable while the backend restarts).

## 10. Watching cost

The OpenAI usage page and the Cohere dashboard. The per-client limits cap one client at about $1.10/day, and `GLOBAL_CHAT_TURNS_PER_DAY` (1000) caps the whole demo. That figure covers chat only; ingest embeddings, metadata LLM calls and title assists add to it.

A Cohere trial key is rate-capped and not licensed for production. When it runs out, rerank fails open silently (answers get worse, nothing errors), so use a production key.

## 11. If sign-in breaks

1. A Supabase project that paused: restore it from the dashboard (and check the keep-alive cron runs).
2. Login loops back to `/login` with no message: the project signs with HS256; see section 4, step 6.
3. Resend domain not verified.
4. A code that never arrives: check Supabase → Logs → Auth, then the Resend dashboard.
5. "Sign-in is temporarily unavailable." from the API: the backend cannot reach Supabase's keys; check `SUPABASE_URL` and `make demo-logs`.

## 12. The edge proxy and other apps on the VM

The edge Caddy is its own stack, owned by no app: `/srv/edge/docker-compose.yml` (project `edge`, container `edge-caddy`), sources in `deploy/edge/`. It is the only container that publishes 80/443 and holds every certificate (volume `edge_caddy_data`). Taking ResearcherX or any other app down leaves every other site serving.

**Moving off the old demo Caddy** (`researcherx-demo-caddy-1`, which used to be the edge): as root, from an up-to-date checkout of `main`:
```bash
cd /srv/researcherx && git fetch && git checkout main && git pull --ff-only
deploy/edge/edge-cutover.sh plan
deploy/edge/edge-cutover.sh cutover
```
`plan` changes nothing. `cutover` installs `/srv/edge`, copies the certificates, validates, attaches the running ResearcherX containers to `proxy`, then stops the old Caddy and starts the edge one (a few seconds without HTTPS for every site) and health-checks every site; any failure rolls back on its own. Check every site in a browser, then `deploy/edge/edge-cutover.sh cleanup` removes the stopped old Caddy (its volumes are kept until you delete them). `deploy/edge/edge-cutover.sh rollback` restores the old Caddy at any point before `cleanup`. Deploy new ResearcherX code (section 9) only after the cutover, never during it.

**A fresh VM** (no old Caddy): after `make demo-up` (section 5), run `deploy/edge/edge-cutover.sh install`.

**Adding an app:**
1. The app's compose publishes no ports. Its entry containers join the external `proxy` network under aliases unique on the VM (e.g. `myapp-web`), never bare names like `backend` or `frontend`.
2. Its site block goes in `/srv/edge/sites/<app>.caddy`, as root, with the hostname written literally: the edge Caddy has no environment, so `{$VAR}` placeholders resolve to nothing. Write into an existing file in place (`cat new > file`), never `mv`.
3. Point the app's DNS `A` record at the VM, then validate and reload, as root:
   `docker exec edge-caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile && docker exec edge-caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile`

A bad site file fails validation and the reload is skipped, so every site keeps serving. ResearcherX's own site file is rendered from `deploy/edge/sites/researcherx.caddy.template` by the cutover script; after changing the template, re-run `deploy/edge/edge-cutover.sh plan`, then render and reload by hand the same way.

## Acceptance

1. Sign up at `/signup`; the activation email arrives (check Resend's log if not), its link lands on `/auth/confirm` and opens the app. Sign out, sign back in with the password; "Forgot password?" sends a reset link that lands on `/auth/reset`; "Email me a sign-in code instead" still works. Signing in before activating shows "Activate your account first".
2. Create a project, upload 2-3 PDFs; they appear in Papers as searchable.
3. Ask a question: the answer streams with citation chips that open the passage.
4. `/admin/research/<id>/graph` shows not-found, and the add-paper dialog has only file upload (PDF, DOCX, Markdown, TXT, RTF).
5. LaTeX: create a blank project, compile it, and see the PDF. The editor has no Share button. `docker inspect researcherx-demo-latex-compiler-1 --format '{{.Config.Env}}'` lists no secrets, and the compiler publishes no ports.
6. Paper cap race: with 19/20 papers, upload a batch of 3. Exactly one succeeds and the others show "Paper limit reached (20). Delete a paper to add another."
7. Sign out: back to `/login`. Sign in again with a new code: same projects.
8. In a second browser or incognito window that is not signed in, `/admin` redirects to `/login`.
