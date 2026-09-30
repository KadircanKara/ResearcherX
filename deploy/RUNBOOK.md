# Public demo runbook

One VM, `docker-compose.prod.yml` + `docker-compose.demo.yml`, Caddy with automatic HTTPS, Supabase email-OTP sign-in (invite-only), Resend for the email. You create every account and enter every secret.

`<DEMO_DOMAIN>` below is the hostname the demo is served on (e.g. `demo.example.com`).

## 1. VM

1. Create an Ubuntu LTS VM with 2 vCPU / 4GB.
2. Install Docker Engine and the compose plugin (https://docs.docker.com/engine/install/ubuntu/), and `make` and `git`: `apt install -y make git` (Ubuntu cloud images lack `make`; `make demo-up` and the backup cron need it).
3. Firewall: `ufw allow 22 && ufw allow 80 && ufw allow 443 && ufw enable`. Nothing else is open (the database and backend publish no ports).
4. `git clone <repo> /srv/researcherx && cd /srv/researcherx && git checkout demo`.

## 2. DNS

1. Add an `A` record for `<DEMO_DOMAIN>` pointing at the VM's public IP.
2. Wait until `dig +short <DEMO_DOMAIN>` returns that IP. Caddy cannot get a certificate before it does.

## 3. Resend

1. Create a Resend account and add the domain. Verify exactly `<DEMO_DOMAIN>` (the same host the sender `no-reply@<DEMO_DOMAIN>` uses), not only the apex: if the demo is on a subdomain and only the apex is verified, sending fails. Alternatively set the sender to the address of the domain you did verify.
2. Add the SPF and DKIM records it shows to your DNS and wait for "Verified".
3. Create an SMTP credential: host `smtp.resend.com`, port `465`, user `resend`, password is the API key.
4. Turn link tracking **off** (it rewrites links and can break or delay the email).

## 4. Supabase (new free project)

1. Authentication → Sign In / Providers → Email: enabled; "Allow new users to sign up" **off**; "Confirm email" on.
2. Authentication → Emails → SMTP: custom SMTP pointed at Resend, sender `no-reply@<DEMO_DOMAIN>`, name "ResearcherX".
3. Authentication → Emails → "Magic link or OTP" template: paste `deploy/supabase/otp-email.html`, subject "Your ResearcherX sign-in code".
4. OTP expiry `3600` seconds, email OTP length `6`.
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
   `POSTGRES_PASSWORD`, `LLM_API_KEY` (OpenAI), `EMBEDDING_API_KEY` (OpenAI), `COHERE_API_KEY`, `OWNER_API_KEY` (random: `openssl rand -hex 32`), `SUPABASE_URL`, `SUPABASE_ANON_KEY` and `DEMO_DOMAIN`.
2. **Start on a fresh database.** The demo must not reuse a dev database or `data/postgres` from a dev machine. `AUTH_MODE=supabase` never seeds users, so a fresh database has no seed users; a database carried over from dev would hold the dev seed accounts and their projects.
3. Deploy as root (`sudo -i` first; the env file is mode 600, root only): `cd /srv/researcherx && set -a; . /etc/researcherx-demo.env; set +a; make demo-up`.
4. Health check: `curl -fsS https://<DEMO_DOMAIN>/v1/health`.
5. `SUPABASE_ANON_KEY` and `SUPABASE_URL` are baked into the frontend image at build time; if you change either, redeploy (step 3 rebuilds).
6. FastAPI's `/docs`, `/redoc` and `/openapi.json` are not exposed: Caddy proxies only `/v1/*` to the backend, and everything else goes to the frontend.

## 6. Add a client

1. Supabase → Authentication → Users → Add user → Create new user, with their email and **Auto Confirm User** ticked. If the dialog asks for a password, set a long random one that is never used (login is by email code only).
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
4. `/admin/research/<id>/latex` and `/graph` show not-found, and the add-paper dialog has only PDF upload.
5. Paper cap race: with 19/20 papers, upload a batch of 3. Exactly one succeeds and the others show "Paper limit reached (20). Delete a paper to add another."
6. Sign out: back to `/login`. Sign in again with a new code: same projects.
7. In a second browser or incognito window that is not signed in, `/admin` redirects to `/login`.
