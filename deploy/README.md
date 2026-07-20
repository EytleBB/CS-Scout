# CS-Scout production deployment

This guide targets Ubuntu 24.04 on a 4-vCPU, 4-GB RAM, 60-GB disk VPS. It
assumes a small group of trusted users, public HTTPS, and the application's
Bearer access key.

The production request path is:

```text
Internet -> Nginx :443 -> Gunicorn 127.0.0.1:5000
                            1 worker / 4 threads
                                 -> analysis thread
                                 -> bounded download and parse pools
```

Do not increase Gunicorn above one worker. Application status, the running-job
mutex, and Demo single-flight locks are process-local. Extra Gunicorn processes
would each accept a scan and would disagree about `/api/status`.

## 1. Host preparation

Replace `scout.example.com`, `ORG/CS_Scout`, and release tags in every example.

```bash
sudo apt update
sudo apt install -y python3 python3-venv git nginx certbot curl openssl ufw

sudo useradd --system --home-dir /var/lib/cs-scout \
  --create-home --shell /usr/sbin/nologin cs-scout
sudo install -d -m 0755 /opt/cs-scout/releases /etc/cs-scout /var/www/letsencrypt
sudo install -d -o cs-scout -g cs-scout -m 0700 \
  /var/lib/cs-scout/demos /var/lib/cs-scout/output /var/lib/cs-scout/maps
```

Use a separate data disk mounted at `/var/lib/cs-scout` when available. Do not
run an external cache cleanup while an analysis is active.

## 2. Install the first release

Create an immutable release directory and install only runtime dependencies:

```bash
RELEASE_TAG='v2.0.0'
REPOSITORY_URL='https://github.com/ORG/CS_Scout.git'
RELEASE_DIR="/opt/cs-scout/releases/${RELEASE_TAG}"

sudo git clone --depth 1 --branch "${RELEASE_TAG}" \
  "${REPOSITORY_URL}" "${RELEASE_DIR}"
sudo python3 -m venv "${RELEASE_DIR}/.venv"
sudo "${RELEASE_DIR}/.venv/bin/python" -m pip install --upgrade pip
sudo "${RELEASE_DIR}/.venv/bin/python" -m pip install \
  -r "${RELEASE_DIR}/server/requirements-runtime.txt"
```

Generate map assets once in a separate tooling environment:

```bash
sudo python3 -m venv /opt/cs-scout/map-venv
sudo /opt/cs-scout/map-venv/bin/python -m pip install --upgrade pip
sudo /opt/cs-scout/map-venv/bin/python -m pip install \
  -r "${RELEASE_DIR}/server/requirements-maps.txt"

sudo -u cs-scout env HOME=/var/lib/cs-scout \
  /opt/cs-scout/map-venv/bin/awpy get maps
sudo -u cs-scout env HOME=/var/lib/cs-scout \
  CS_SCOUT_MAPS_DIR=/var/lib/cs-scout/maps \
  /opt/cs-scout/map-venv/bin/python "${RELEASE_DIR}/server/setup_maps.py"
```

Verify that every map has both `meta.json` and `radar.png` before continuing.

## 3. Secrets and resource limits

```bash
sudo cp "${RELEASE_DIR}/.env.example" /etc/cs-scout/cs-scout.env
sudo chmod 600 /etc/cs-scout/cs-scout.env
openssl rand -hex 32
sudoedit /etc/cs-scout/cs-scout.env
```

Paste the generated value into `CS_SCOUT_SECRET_KEY`. Do not commit the real
environment file. For this 4-GB host, begin with four download workers and one
parse process, as provided by `.env.example`. Increase only after measuring
peak RSS and leaving at least 1 GB available for the OS and Nginx.

The same template limits one compressed Demo download to 1024 MB, the cache to
16 GB with a 10-GB cleanup target, one task to 12 GB of downloads, and preserves
at least 8 GB of filesystem free space. Lower these values if `/var/lib` shares
a smaller root filesystem; do not raise them without preserving extraction and
rollback headroom.

Demo URLs are restricted to the supported HTTPS CDN hostname allowlist. The
optional `CS_SCOUT_DEMO_REQUIRE_PUBLIC_DNS=true` check also rejects CDN DNS
answers in private address ranges. Leave it `false` if the VPS provider or a
Chinese acceleration network legitimately resolves that CDN through RFC1918;
set it to `true` only after confirming the CDN consistently resolves to public
addresses from the production VPS.

## 4. Validate and start Gunicorn

Point `current` at the prepared release, install the service, and run the
read-only validator as the service user:

```bash
sudo ln -s "${RELEASE_DIR}" /opt/cs-scout/current
sudo install -m 0644 "${RELEASE_DIR}/deploy/cs-scout.service" \
  /etc/systemd/system/cs-scout.service

sudo -u cs-scout env \
  CS_SCOUT_DEMO_DIR=/var/lib/cs-scout/demos \
  CS_SCOUT_OUTPUT_DIR=/var/lib/cs-scout/output \
  CS_SCOUT_MAPS_DIR=/var/lib/cs-scout/maps \
  bash "${RELEASE_DIR}/deploy/verify_release.sh" "${RELEASE_DIR}"

sudo systemctl daemon-reload
sudo systemctl enable --now cs-scout.service
sudo systemctl status cs-scout.service
curl --fail --silent http://127.0.0.1:5000/healthz
curl --fail --silent http://127.0.0.1:5000/readyz
```

The service binds only to loopback. Never expose port 5000 in the cloud
security group or host firewall.

## 5. Nginx and TLS

Nginx terminates HTTPS and explicitly forwards the `Authorization: Bearer ...`
header to Flask. Do not enable Nginx Basic Auth in front of the current UI:
Basic and Bearer authentication both use the `Authorization` header and would
overwrite each other. If another perimeter is required, prefer a VPN or an IP
allowlist such as `allow TRUSTED_PUBLIC_IP; deny all;` in the TLS server block.

For the first certificate, edit and install the HTTP-only bootstrap virtual
host, then use Certbot's webroot mode:

```bash
sudo cp "${RELEASE_DIR}/deploy/nginx-cs-scout-bootstrap.conf" \
  /etc/nginx/sites-available/cs-scout
sudoedit /etc/nginx/sites-available/cs-scout
sudo ln -s /etc/nginx/sites-available/cs-scout /etc/nginx/sites-enabled/cs-scout
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx

sudo certbot certonly --webroot -w /var/www/letsencrypt \
  -d scout.example.com
```

After the certificate exists, install and edit the production proxy:

```bash
sudo cp "${RELEASE_DIR}/deploy/nginx-cs-scout.conf" \
  /etc/nginx/sites-available/cs-scout
sudoedit /etc/nginx/sites-available/cs-scout
sudo nginx -t
sudo systemctl reload nginx
sudo certbot renew --dry-run
```

Allow only SSH and Nginx through the host firewall. Apply equivalent rules in
the VPS provider's security group.

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw deny 5000/tcp
sudo ufw enable
```

Final smoke checks. Read the application key without placing it in shell
history, then verify readiness and one protected endpoint:

```bash
read -rsp 'CS-Scout access key: ' CS_SCOUT_ACCESS_KEY; echo
curl --fail --silent https://scout.example.com/readyz
curl --fail --silent \
  -H "Authorization: Bearer ${CS_SCOUT_ACCESS_KEY}" \
  https://scout.example.com/api/status
unset CS_SCOUT_ACCESS_KEY
sudo journalctl -u cs-scout.service -n 100 --no-pager
```

`/healthz` is a liveness probe. `/readyz` additionally checks the configured
secret, map availability, and write access to the Demo/output directories.
Both probes deliberately return only generic status and may be monitored
without the application key.

## 6. Upgrade without mixing versions

Never update files in place. A fast analysis starts new Python processes over
time, so changing source files during a scan can mix two code versions.

1. Authenticate to `/api/status` and confirm `status` is not `running`.
2. Clone the new tag into a new `/opt/cs-scout/releases/<tag>` directory.
3. Create its own `.venv` and install `requirements-runtime.txt`.
4. Run `pip check`, the automated tests in CI/staging, and
   `deploy/verify_release.sh` against the new directory.
5. Atomically switch `current`, restart, and perform the HTTPS smoke check.

```bash
NEW_RELEASE='/opt/cs-scout/releases/v2.0.1'
sudo ln -s "${NEW_RELEASE}" /opt/cs-scout/current.next
sudo mv -Tf /opt/cs-scout/current.next /opt/cs-scout/current
sudo systemctl restart cs-scout.service
curl --fail --silent https://scout.example.com/readyz
```

Do not restart while analysis is running: the current in-process background
job is not resumable.

## 7. Rollback

Keep the previous two or three release directories. If the health or smoke
check fails, point `current` back to the last known-good release and restart:

```bash
PREVIOUS_RELEASE='/opt/cs-scout/releases/v2.0.0'
sudo ln -s "${PREVIOUS_RELEASE}" /opt/cs-scout/current.rollback
sudo mv -Tf /opt/cs-scout/current.rollback /opt/cs-scout/current
sudo systemctl restart cs-scout.service
```

Demo files and `.demo_index.json` are caches and do not need backup. Back up
`/etc/cs-scout`, Nginx/systemd configuration, and the versioned map assets.
Back up `/var/lib/cs-scout/output` only if historical results matter; it may
contain player analysis data and should be encrypted and access-controlled.

## 8. Operations

Useful commands:

```bash
sudo systemctl status cs-scout.service
sudo journalctl -u cs-scout.service -f
sudo nginx -t
sudo du -sh /var/lib/cs-scout/demos
df -h /var/lib/cs-scout
```

Alert on low disk space, repeated service restarts, analysis jobs with no
progress, HTTP 5xx responses, and sustained memory pressure. Journald and the
Ubuntu Nginx package rotate their own logs; set site-wide journald retention to
match the VPS disk budget.
