# Public demo runbook

One VM, `docker-compose.prod.yml` + `docker-compose.demo.yml`, Caddy with automatic HTTPS, Supabase email-OTP sign-in (invite-only), Resend for the email. You create every account and enter every secret.

`<DEMO_DOMAIN>` below is the hostname the demo is served on (e.g. `demo.example.com`).

## 1. VM

1. Create an Ubuntu LTS VM with at least 4 vCPU / 8GB and 40GB of disk. The LaTeX compiler image is TeX Live scheme-full (about 6GB on disk, built once, which takes a while on the first deploy), and each compile may use up to 2 CPUs and 2GB of memory.
2. Install Docker Engine and the compose plugin (https://docs.docker.com/engine/install/ubuntu/), and `make` and `git`: `apt install -y make git` (Ubuntu cloud images lack `make`; `make demo-up` and the backup cron need it).
3. Firewall: `ufw allow 22 && ufw allow 80 && ufw allow 443 && ufw enable`. Nothing else is open (the database and backend publish no ports).
4. Nothing else may hold ports 80/443: `docker ps --format '{{.Names}} {{.Ports}}' | grep -E ':(80|443)->'` must print nothing. Another stack's proxy there makes `make demo-up` fail with `port is already allocated`; stop that stack first (`docker compose -p <project> down` works without its compose file).
5. As root (`sudo -i`; `/srv` is root-owned, so a plain `git clone` there is refused): `git clone https://github.com/KadircanKara/ResearcherX.git /srv/researcherx && cd /srv/researcherx && git checkout demo`.
6. Give Docker real DNS servers. On OVH (and other images whose `/etc/resolv.conf` points at systemd-resolved's `127.0.0.53`), containers inherit that address, cannot reach it, and Caddy can never get a certificate (`lookup acme-v02.api.letsencrypt.org on 127.0.0.53:53: ... connection refused`). If `/etc/docker/daemon.json` does not exist: `echo '{"dns": ["1.1.1.1", "8.8.8.8"]}' > /etc/docker/daemon.json && systemctl restart docker` (merge the `dns` key in instead if the file exists).

## 2. DNS

1. Add an `A` record for `<DEMO_DOMAIN>` pointing at the VM's public IP.
2. Wait until `dig +short <DEMO_DOMAIN>` returns that IP. Caddy cannot get a certificate before it does.

## 3. Resend

1. Create a Resend account and add the domain. Verify exactly `<DEMO_DOMAIN>` (the same host the sender `no-reply@<DEMO_DOMAIN>` uses), not only the apex: if the demo is on a subdomain and only the apex is verified, sending fails. Alternatively set the sender to the address of the domain you did verify.
2. Add the SPF and DKIM records it shows to your DNS and wait for "Verified".
3. API Keys → Create API Key: permission "Sending access", restricted to the verified domain. Resend has no separate SMTP credential: this key is the SMTP password in section 4, step 2 (host `smtp.resend.com`, port `465`, user `resend`). It is shown once.
4. Turn link tracking **off** (it rewrites links and can break or delay the email).

## 4. Supabase (new free project)

1. Authentication → Sign In / Providers → Email: enabled; "Allow new users to sign up" **off**; "Confirm email" on.
2. Authentication → Emails → SMTP: custom SMTP pointed at Resend, sender `no-reply@<DEMO_DOMAIN>`, name "ResearcherX".
3. Authentication → Emails → "Magic link or OTP" template: paste `deploy/supabase/otp-email.html`, subject "Your ResearcherX sign-in code".
4. Authentication → Sign In / Providers → Email: Email OTP Expiration `3600` seconds, **Email OTP Length `6`**. New projects default to 8, and the sign-in page accepts only 6 digits.
5. URL Configuration: Site URL `https://<DEMO_DOMAIN>`.
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
   `POSTGRES_PASSWORD` (random: `openssl rand -hex 32`; it is the password of the demo's own Postgres container, set on first start and never read from anywhere else), `LLM_API_KEY` (OpenAI), `EMBEDDING_API_KEY` (OpenAI), `COHERE_API_KEY`, `OWNER_API_KEY` (random: `openssl rand -hex 32`), `SUPABASE_URL` (`https://<project-ref>.supabase.co`), `SUPABASE_ANON_KEY` (the publishable or anon key, never the secret/service_role key) and `DEMO_DOMAIN` (hostname only, no `https://`).

   Create the file already locked down, as root: `install -m 600 -o root -g root /dev/null /etc/researcherx-demo.env`, then edit it with `nano` as root. Long keys pasted through an editor get truncated easily; write them without one instead, so they are never echoed:
   ```bash
   read -rsp 'OpenAI key: ' K; echo
   printf "LLM_API_KEY='%s'\nEMBEDDING_API_KEY='%s'\n" "$K" "$K" >> /etc/researcherx-demo.env; unset K
   ```
   Check it before deploying: `bash -n /etc/researcherx-demo.env` prints nothing when the syntax is valid, and `cut -d= -f1 /etc/researcherx-demo.env` lists the names without the values. Every `docker compose` command for the demo (including `logs` and `ps`) needs this file loaded in the same shell first, or it fails with `required variable ... is missing a value`.
2. **Start on a fresh database.** The demo must not reuse a dev database or `data/postgres` from a dev machine. `AUTH_MODE=supabase` never seeds users, so a fresh database has no seed users; a database carried over from dev would hold the dev seed accounts and their projects.
3. Deploy as root (`sudo -i` first; the env file is mode 600, root only): `cd /srv/researcherx && set -a; . /etc/researcherx-demo.env; set +a; make demo-up`.
4. Health check: `curl -fsS https://<DEMO_DOMAIN>/v1/health`.
5. `SUPABASE_ANON_KEY` and `SUPABASE_URL` are baked into the frontend image at build time; if you change either, redeploy (step 3 rebuilds).
6. FastAPI's `/docs`, `/redoc` and `/openapi.json` are not exposed: Caddy proxies only `/v1/*` to the backend, and everything else goes to the frontend.

## 6. Add a client

1. Supabase → Authentication → Users → Add user → Create new user, with their email and **Auto Confirm User** ticked. If the dialog asks for a password, paste a long random one (`openssl rand -base64 32`) and keep no copy: login is by email code only, and Supabase's own password endpoint is reachable with the public key, so the password must be unguessable.
2. Send them the URL and ask them to sign in with that email.
3. **Never re-invite an email address that previously belonged to a different client.** Accounts relink by email, so the new person would get the old client's papers and chats. Use a different address, or wipe that user's data first.
4. The demo admits at most `DEMO_MAX_USERS` (20) accounts; the 21st gets "The demo is full."

## 7. Remove a client

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
2. Run the deploy line from section 5, step 3.

## 10. Watching cost

The OpenAI usage page and the Cohere dashboard. The per-client limits cap one client at about $1.10/day, and `GLOBAL_CHAT_TURNS_PER_DAY` (1000) caps the whole demo. That figure covers chat only; ingest embeddings, metadata LLM calls and title assists add to it.

A Cohere trial key is rate-capped and not licensed for production. When it runs out, rerank fails open silently (answers get worse, nothing errors), so use a production key.

## 11. If sign-in breaks

1. A Supabase project that paused: restore it from the dashboard (and check the keep-alive cron runs).
2. Login loops back to `/login` with no message: the project signs with HS256; see section 4, step 6.
3. Resend domain not verified.
4. A code that never arrives: check Supabase → Logs → Auth, then the Resend dashboard.
5. "Sign-in is temporarily unavailable." from the API: the backend cannot reach Supabase's keys; check `SUPABASE_URL` and `make demo-logs`.

## Acceptance

1. Invite yourself and sign in with the emailed 6-digit code.
2. Create a project, upload 2-3 PDFs; they appear in Papers as searchable.
3. Ask a question: the answer streams with citation chips that open the passage.
4. `/admin/research/<id>/graph` shows not-found, and the add-paper dialog has only file upload (PDF, DOCX, Markdown, TXT, RTF).
5. LaTeX: create a blank project, compile it, and see the PDF. The editor has no Share button. `docker inspect researcherx-demo-latex-compiler-1 --format '{{.Config.Env}}'` lists no secrets, and the compiler publishes no ports.
6. Paper cap race: with 19/20 papers, upload a batch of 3. Exactly one succeeds and the others show "Paper limit reached (20). Delete a paper to add another."
7. Sign out: back to `/login`. Sign in again with a new code: same projects.
8. In a second browser or incognito window that is not signed in, `/admin` redirects to `/login`.
